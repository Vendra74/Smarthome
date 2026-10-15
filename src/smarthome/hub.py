"""O núcleo da central: liga o cadastro de dispositivos aos adaptadores.

API HTTP, agente de voz e (no futuro) a skill da Alexa chamam só o Hub.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from .adapters.base import Adapter, AdapterError
from .models import Command, CommandResult, Device, DeviceState, DeviceType, DiscoveredDevice
from .registry import DeviceRegistry

if TYPE_CHECKING:
    from .config import HomeConfig

log = logging.getLogger(__name__)


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class Hub:
    """A central de uma casa. Cada casa tem seu Hub, cadastro e credenciais."""

    def __init__(
        self, registry: DeviceRegistry, adapters: list[Adapter], home: HomeConfig | None = None
    ) -> None:
        self.home = home
        self.registry = registry
        self.integrations: list[dict[str, str]] = []
        # Último erro de descoberta por adaptador, para o app mostrar o motivo.
        self.discovery_errors: dict[str, str] = {}
        self.adapters = {a.name: a for a in adapters}

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
        try:
            state = await self._adapter(device).execute(device, command, params or {})
        except (AdapterError, KeyError, ValueError) as exc:
            log.warning("Falha em %s %s: %s", device_id, command.value, exc)
            return CommandResult(device_id=device_id, ok=False, message=str(exc))
        log.info("%s <- %s %s", device_id, command.value, params or {})
        return CommandResult(device_id=device_id, ok=True, state=state)

    async def get_state(self, device_id: str) -> DeviceState | None:
        device = self.registry.get(device_id)
        if device is None:
            return None
        try:
            return await self._adapter(device).get_state(device)
        except AdapterError:
            return DeviceState(online=False)

    async def discover(self, adapter_name: str | None = None) -> list[DiscoveredDevice]:
        """Dispositivos que as plataformas conhecem e a central ainda não cadastrou."""
        known = {
            (d.adapter, d.config.get("device_id") or d.config.get("host")) for d in self.registry.all()
        }
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
            found.extend(i for i in items if (i.adapter, i.external_id) not in known)
        return found

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
