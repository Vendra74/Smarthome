"""Recuperação de TV pela rede local quando a nuvem (SmartThings) a dá como offline.

A SmartThings perde a TV Samsung com frequência: em standby ela some da nuvem,
mas continua na rede local e acorda com Wake-on-LAN. A central, que está na
mesma rede, resolve isso sozinha:

1. acha a TV na rede (SSDP + http://<ip>:8001/api/v2/) e guarda IP e MAC;
2. se a TV está ligada na rede, manda o comando direto a ela (adaptador samsung_tv);
3. se está em standby, acorda com Wake-on-LAN e repete o comando pela nuvem
   assim que ela volta.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .adapters.base import Adapter, AdapterError, DeviceUnreachable
from .adapters import samsung_lan
from .adapters.samsung_lan import find_samsung_tvs, same_tv, tv_info
from .adapters.wol import send_magic_packet
from .models import Command, Device, DeviceState, DeviceType

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

HINT = "Confira na TV 'Ligar com dispositivo móvel' e se ela está na mesma rede da central."


def hint() -> str:
    """Dica para a pessoa; aponta o bloqueio do macOS quando foi ele que impediu a busca."""
    return samsung_lan.MACOS_HINT if samsung_lan.lan_blocked() else HINT


class TVRecovery:
    def __init__(
        self,
        scan: Callable[[], Awaitable[list[dict[str, Any]]]] = find_samsung_tvs,
        info: Callable[[str], Awaitable[dict[str, Any] | None]] = tv_info,
        wake: Callable[..., None] = send_magic_packet,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 25.0,
        interval: float = 3.0,
    ) -> None:
        self._scan, self._info, self._wake, self._sleep = scan, info, wake, sleep
        self.timeout, self.interval = timeout, interval
        self._last_scan: dict[str, float] = {}
        # Uma varredura serve para todas as TVs por um tempo; o log só muda quando o resultado muda.
        self._scan_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._scan_lock = asyncio.Lock()
        self._last_logged: list[tuple[Any, ...]] | None = None
        self._last_learn = -1e9

    def last_scan(self) -> tuple[float, list[dict[str, Any]]] | None:
        """(idade em segundos, TVs vistas) da última varredura, ou None se ainda não houve."""
        if self._scan_cache is None:
            return None
        return time.monotonic() - self._scan_cache[0], list(self._scan_cache[1])

    async def scan_now(self, max_age: float = 60.0) -> list[dict[str, Any]]:
        """Garante uma varredura recente (no máximo `max_age` s) e devolve as TVs vistas."""
        return await self._scan_shared(max_age=max_age)

    def unmatched(self, hub: Hub) -> list[dict[str, Any]]:
        """TVs vistas na rede que não casam com nenhuma TV cadastrada da SmartThings."""
        scan = self.last_scan()
        if not scan:
            return []
        registered = [d for d in hub.registry.all() if self.applies(d)]
        return [tv for tv in scan[1] if not any(self._matches(tv, d) for d in registered)]

    def applies(self, device: Device) -> bool:
        return device.type == DeviceType.TV and device.adapter == "smartthings"

    @staticmethod
    def _matches(tv: dict[str, Any], device: Device) -> bool:
        """A TV da rede é este cadastro? Pelo nome dado pela pessoa, pelo nome na SmartThings
        (que costuma ser o nome que a TV anuncia), por um apelido ou pelo IP já conhecido."""
        names = [device.name, device.config.get("st_name") or "", *device.aliases]
        return any(same_tv(tv["name"], n) for n in names if n) or (
            bool(device.config.get("host")) and tv["host"] == device.config.get("host")
        )

    def _remember(self, hub: Hub, device: Device, tv: dict[str, Any]) -> None:
        if tv["host"] != device.config.get("host") or (tv["mac"] and tv["mac"] != device.config.get("mac")):
            device.config.update({"host": tv["host"], **({"mac": tv["mac"]} if tv["mac"] else {})})
            hub.registry.update(device)
            log.info("%s achada na rede local: %s %s", device.id, tv["host"], tv["mac"])

    async def learn(self, hub: Hub) -> None:
        """Aprende IP e MAC de todas as TVs cadastradas que estão ligadas na rede agora,
        mesmo com a nuvem funcionando. É o que permite acordá-las depois."""
        tvs_at_home = [d for d in hub.registry.all() if self.applies(d)]
        if not tvs_at_home:
            return
        tvs = await self._scan_shared(max_age=0)
        if not tvs:
            return
        for device in tvs_at_home:
            if not device.config.get("st_name"):
                adapter = hub.adapters.get(device.adapter)
                label = getattr(adapter, "device_label", None)
                if label is not None:
                    try:
                        if name := await label(device):
                            device.config["st_name"] = name
                            hub.registry.update(device)
                    except AdapterError as exc:
                        log.info("Sem nome da SmartThings para %s: %s", device.id, exc)
            tv = next((t for t in tvs if self._matches(t, device)), None)
            if tv:
                self._remember(hub, device, tv)
            elif not device.config.get("mac"):
                log.info("%s ainda sem IP/MAC: nenhuma TV na rede com o nome '%s'", device.id, device.name)

    def start_background(self, hub: Hub, interval: float = 300.0) -> asyncio.Task:
        """Aprende as TVs ao iniciar e a cada `interval` segundos, sem travar nada."""

        async def loop() -> None:
            while True:
                try:
                    self._last_learn = time.monotonic()
                    await self.learn(hub)
                except Exception as exc:  # nunca derruba a central
                    log.warning("Aprendizado das TVs falhou: %s", exc)
                await self._sleep(interval)

        return asyncio.ensure_future(loop())

    def nudge(self, device: Device) -> None:
        """Antes de todo 'ligar' de TV, manda o pacote de acordar se o MAC é conhecido.

        A SmartThings às vezes aceita o 'ligar' de uma TV em standby profundo sem que ela
        reaja; o pacote é inofensivo com a TV ligada e resolve esse caso sem esperar o 409."""
        mac = device.config.get("mac")
        if not mac:
            return
        try:
            self._wake(mac, host=device.config.get("host"))
        except (OSError, ValueError) as exc:
            log.info("Wake-on-LAN preventivo em %s falhou: %s", device.id, exc)

    def stop(self) -> None:
        """Cancela buscas e aprendizados em andamento (no encerramento da central)."""
        for name in ("_scan_task", "_learn_task"):
            task = getattr(self, name, None)
            if task is not None and not task.done():
                task.cancel()

    def _background_learn(self, hub: Hub, min_gap: float = 60.0) -> None:
        """Roda `learn` em segundo plano, no máximo uma vez a cada `min_gap` s."""
        if time.monotonic() - self._last_learn < min_gap:
            return
        task = getattr(self, "_learn_task", None)
        if task is not None and not task.done():
            return
        self._last_learn = time.monotonic()

        async def run() -> None:
            try:
                await self.learn(hub)
            except Exception as exc:  # nunca derruba a listagem
                log.warning("Aprendizado das TVs falhou: %s", exc)

        self._learn_task = asyncio.ensure_future(run())

    def _background_scan(self) -> None:
        """Dispara uma varredura sem esperar por ela (uma de cada vez)."""
        task = getattr(self, "_scan_task", None)
        if task is not None and not task.done():
            return
        self._scan_task = asyncio.ensure_future(self._scan_shared(max_age=0))

    async def _scan_shared(self, max_age: float = 30.0) -> list[dict[str, Any]]:
        """Varre a rede no máximo uma vez a cada `max_age` s, para todas as TVs juntas."""
        async with self._scan_lock:
            if self._scan_cache and time.monotonic() - self._scan_cache[0] < max_age:
                return self._scan_cache[1]
            tvs = await self._scan()
            self._scan_cache = (time.monotonic(), tvs)
        summary = [(t["name"], t["host"], t["mac"]) for t in tvs]
        if summary != self._last_logged:
            log.info("TVs Samsung na rede local: %s", summary or "nenhuma")
            self._last_logged = summary
        return tvs

    async def locate(self, hub: Hub, device: Device) -> dict[str, Any] | None:
        """Dados da TV na rede local; descobre e guarda IP/MAC na primeira vez."""
        host = device.config.get("host")
        if host:
            found = await self._info(host)
            if found:
                return found
        tvs = await self._scan_shared()
        for tv in tvs:
            if self._matches(tv, device):
                self._remember(hub, device, tv)
                return tv
        return None

    async def recover(
        self, hub: Hub, device: Device, command: Command, params: dict[str, Any], adapter: Adapter
    ) -> tuple[DeviceState, str]:
        """Tenta cumprir o comando pela rede local. Devolve (estado, nota) ou levanta AdapterError."""
        self._scan_cache = None  # num comando, vale a rede de agora
        tv = await self.locate(hub, device)
        if tv and tv["power"]:
            return await self._local(hub, device, command, params, adapter)
        if command == Command.TURN_OFF:
            return DeviceState(online=False, power=False), f"{device.name} já estava desligada."
        mac = device.config.get("mac")
        if not mac:
            raise DeviceUnreachable(
                f"{device.name} está fora do alcance e não achei o MAC dela na rede para acordá-la. "
                "Ligue a TV uma vez pelo controle, que a central guarda o IP e o MAC. " + hint()
            )
        host = device.config.get("host")
        for _ in range(3):
            self._wake(mac, host=host)
        waited = 0.0
        while waited < self.timeout:
            await self._sleep(self.interval)
            waited += self.interval
            try:
                state = await adapter.execute(device, command, params)
            except DeviceUnreachable:
                # A TV responde na rede segundos antes de a nuvem perceber: se ela já
                # acordou, "ligar" está cumprido mesmo com a SmartThings atrasada.
                if host and command == Command.TURN_ON:
                    info = await self._info(host)
                    if info and info["power"]:
                        return DeviceState(power=True), f"{device.name} acordou pela rede; a SmartThings ainda não a vê."
                continue
            return state, f"{device.name} estava fora do alcance; acordei pela rede."
        raise DeviceUnreachable(
            f"Mandei acordar {device.name} pela rede, mas ela não respondeu em {int(self.timeout)} s. " + hint()
        )

    async def _local(
        self, hub: Hub, device: Device, command: Command, params: dict[str, Any], adapter: Adapter
    ) -> tuple[DeviceState, str]:
        """A TV está ligada na rede, só a nuvem não a vê: age direto, ou espera a nuvem voltar."""
        if command == Command.TURN_ON:
            return DeviceState(power=True), f"{device.name} já está ligada; a SmartThings é que não a via."
        local = hub.adapters.get("samsung_tv")
        if command == Command.SET_VOLUME:
            raise DeviceUnreachable(
                f"{device.name} está ligada, mas a SmartThings não a enxerga agora e pela rede a TV só "
                "aceita 'aumentar' e 'diminuir', não um valor. Tente de novo em instantes."
            )
        if local is not None:
            try:
                state = await local.execute(device, command, params)
                return state, f"{device.name} atendeu pela rede local."
            except AdapterError as exc:
                log.warning("Comando local em %s falhou: %s", device.id, exc)
        raise DeviceUnreachable(
            f"{device.name} está ligada e na rede, mas a SmartThings não a enxerga agora. "
            "Tente de novo em instantes."
        )

    async def state(self, hub: Hub, device: Device, state: DeviceState) -> DeviceState:
        """Corrige 'sem conexão' da nuvem quando a TV responde ligada na rede local.

        Sem IP conhecido, varre a rede no máximo uma vez por minuto por TV."""
        if state.online:
            if not device.config.get("mac"):
                # A TV acabou de aparecer na nuvem: é a hora de aprender IP e MAC dela,
                # sem esperar o próximo ciclo de 5 min.
                self._background_learn(hub)
            elif state.power and device.config.get("host"):
                # A SmartThings continua dizendo "ligada" por um tempo depois que a TV
                # entra em espera; a própria TV, pela rede, diz a verdade na hora.
                info = await self._info(device.config["host"])
                if info and not info["power"]:
                    return DeviceState(online=True, power=False,
                                       attributes={**state.attributes, "via": "rede local"})
            return state
        if device.config.get("host"):
            found = await self._info(device.config["host"])
        elif self._scan_cache is not None:
            # Lista pronta da última varredura; a próxima roda em segundo plano, para a
            # tela não esperar a rede inteira ser varrida.
            found = next((tv for tv in self._scan_cache[1] if self._matches(tv, device)), None)
            if found:
                self._remember(hub, device, found)
            if time.monotonic() - self._scan_cache[0] > 60:
                self._background_scan()
        else:
            self._background_scan()
            found = None
        if found and found["power"]:
            return DeviceState(online=True, power=True, attributes={**state.attributes, "via": "rede local"})
        return state
