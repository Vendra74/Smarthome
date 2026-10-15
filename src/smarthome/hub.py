"""O núcleo da central: liga o cadastro de dispositivos aos adaptadores.

API HTTP, agente de voz e (no futuro) a skill da Alexa chamam só o Hub.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from .adapters.base import Adapter, AdapterError, DeviceUnreachable
from .adapters.samsung_lan import same_tv
from .models import Command, CommandResult, Device, DeviceState, DeviceType, DiscoveredDevice
from .recovery import TVRecovery
from .registry import DeviceRegistry

if TYPE_CHECKING:
    from .config import HomeConfig

log = logging.getLogger(__name__)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _merge_lan_tvs(found: list[DiscoveredDevice]) -> list[DiscoveredDevice]:
    """A mesma TV achada na nuvem (SmartThings) e na rede vira um item só: o da nuvem, que
    tem volume e estado, já com IP e MAC da rede para a recuperação local."""
    lan = [i for i in found if i.adapter in ("samsung_tv", "lg_tv")]
    others = [i for i in found if i not in lan]
    merged = []
    for item in others:
        if item.suggested_type == DeviceType.TV:
            twin = next((t for t in lan if same_tv(t.name, item.name)), None)
            if twin is not None:
                lan.remove(twin)
                item = item.model_copy(update={"config": {**item.config, **twin.config}})
        merged.append(item)
    return merged + lan


class Hub:
    """A central de uma casa. Cada casa tem seu Hub, cadastro e credenciais."""

    def __init__(
        self,
        registry: DeviceRegistry,
        adapters: list[Adapter],
        home: HomeConfig | None = None,
        recovery: TVRecovery | None = None,
    ) -> None:
        self.home = home
        self.recovery = recovery or TVRecovery()
        self.registry = registry
        self.integrations: list[dict[str, str]] = []
        # Último erro de descoberta por adaptador, para o app mostrar o motivo.
        self.discovery_errors: dict[str, str] = {}
        self.adapters = {a.name: a for a in adapters}
        # Contas de fabricante ligadas pela tela "Contas" (AccountStore); None nos testes.
        self.accounts: Any = None

    def set_adapter(self, adapter: Adapter) -> None:
        """Liga (ou troca) um adaptador em tempo de execução, ex.: quando uma conta é conectada."""
        self.adapters[adapter.name] = adapter
        self.discovery_errors.pop(adapter.name, None)

    def remove_adapter(self, name: str) -> None:
        self.adapters.pop(name, None)
        self.discovery_errors.pop(name, None)

    def _adapter(self, device: Device) -> Adapter:
        try:
            return self.adapters[device.adapter]
        except KeyError as exc:
            raise AdapterError(f"Adaptador '{device.adapter}' não está ativo") from exc

    async def execute(self, device_id: str, command: Command, params: dict[str, Any] | None = None) -> CommandResult:
        device = self.registry.get(device_id)
        if device is None:
            return CommandResult(device_id=device_id, ok=False, message="Dispositivo não encontrado")
        if not device.supports(command):
            return CommandResult(
                device_id=device_id,
                ok=False,
                message=f"{device.name} não aceita o comando {command.value}",
            )
        adapter = self._adapter(device)
        note = ""
        try:
            try:
                state = await adapter.execute(device, command, params or {})
            except DeviceUnreachable as exc:
                if not self.recovery.applies(device):
                    raise
                # A nuvem não alcança a TV; a central está na mesma rede e tenta por ela.
                log.info("%s fora do alcance da nuvem (%s); recuperando pela rede local", device_id, exc)
                state, note = await self.recovery.recover(self, device, command, params or {}, adapter)
        except (AdapterError, KeyError, ValueError) as exc:
            log.warning("Falha em %s %s: %s", device_id, command.value, exc)
            return CommandResult(device_id=device_id, ok=False, message=str(exc))
        log.info("%s <- %s %s %s", device_id, command.value, params or {}, note)
        return CommandResult(device_id=device_id, ok=True, state=state, message=note)

    async def get_state(self, device_id: str) -> DeviceState | None:
        device = self.registry.get(device_id)
        if device is None:
            return None
        try:
            state = await self._adapter(device).get_state(device)
        except AdapterError:
            state = DeviceState(online=False)
        if self.recovery.applies(device):
            try:
                state = await self.recovery.state(self, device, state)
            except Exception as exc:  # a busca na rede nunca pode derrubar a listagem
                log.warning("Busca de %s na rede local falhou: %s", device_id, exc)
        return state

    async def discover(self, adapter_name: str | None = None) -> list[DiscoveredDevice]:
        """Dispositivos que as plataformas conhecem e a central ainda não cadastrou."""
        known = {
            (d.adapter, d.config.get("device_id") or d.config.get("host")) for d in self.registry.all()
        }
        # TV já cadastrada por qualquer adaptador (a nuvem e a rede acham a mesma TV)
        known_hosts = {d.config.get(k) for d in self.registry.all() for k in ("host", "mac") if d.config.get(k)}
        found: list[DiscoveredDevice] = []
        for adapter in self.adapters.values():
            if adapter_name and adapter.name != adapter_name:
                continue
            try:
                items = await adapter.discover()
            except AdapterError as exc:
                log.warning("Descoberta falhou em %s: %s", adapter.name, exc)
                self.discovery_errors[adapter.name] = str(exc)
                continue
            self.discovery_errors.pop(adapter.name, None)
            for item in items:
                if (item.adapter, item.external_id) in known:
                    continue
                if item.config.get("host") in known_hosts or item.config.get("mac") in known_hosts:
                    continue
                found.append(item)
        return _merge_lan_tvs(found)

    def register(
        self,
        name: str,
        type: DeviceType,
        adapter: str,
        room: str | None = None,
        config: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
    ) -> Device:
        if adapter not in self.adapters:
            raise ValueError(f"Adaptador '{adapter}' não está ativo")
        base = slugify(name if not room or slugify(room) in slugify(name) else f"{name} {room}")
        device_id, n = base, 2
        while self.registry.get(device_id):
            device_id, n = f"{base}-{n}", n + 1
        device = Device(
            id=device_id,
            name=name,
            room=room,
            type=type,
            adapter=adapter,
            config=config or {},
            aliases=aliases or [],
        )
        return self.registry.add(device)
