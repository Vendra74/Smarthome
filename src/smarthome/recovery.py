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
from .adapters.samsung_lan import find_samsung_tvs, same_tv, tv_info
from .adapters.wol import send_magic_packet
from .models import Command, Device, DeviceState, DeviceType

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

HINT = "Confira na TV 'Ligar com dispositivo móvel' e se ela está na mesma rede da central."


class TVRecovery:
    def __init__(
        self,
        scan: Callable[[], Awaitable[list[dict[str, Any]]]] = find_samsung_tvs,
        info: Callable[[str], Awaitable[dict[str, Any] | None]] = tv_info,
        wake: Callable[[str], None] = send_magic_packet,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 25.0,
        interval: float = 3.0,
    ) -> None:
        self._scan, self._info, self._wake, self._sleep = scan, info, wake, sleep
        self.timeout, self.interval = timeout, interval
        self._last_scan: dict[str, float] = {}

    def applies(self, device: Device) -> bool:
        return device.type == DeviceType.TV and device.adapter == "smartthings"

    async def locate(self, hub: Hub, device: Device) -> dict[str, Any] | None:
        """Dados da TV na rede local; descobre e guarda IP/MAC na primeira vez."""
        host = device.config.get("host")
        if host:
            found = await self._info(host)
            if found:
                return found
        tvs = await self._scan()
        log.info("TVs Samsung na rede local: %s", [(t["name"], t["host"], t["mac"]) for t in tvs] or "nenhuma")
        for tv in tvs:
            if same_tv(tv["name"], device.name) or (host and tv["host"] == host):
                if tv["host"] != host or (tv["mac"] and tv["mac"] != device.config.get("mac")):
                    device.config.update({"host": tv["host"], **({"mac": tv["mac"]} if tv["mac"] else {})})
                    hub.registry.update(device)
                    log.info("%s achada na rede local: %s %s", device.id, tv["host"], tv["mac"])
                return tv
        return None

    async def recover(
        self, hub: Hub, device: Device, command: Command, params: dict[str, Any], adapter: Adapter
    ) -> tuple[DeviceState, str]:
        """Tenta cumprir o comando pela rede local. Devolve (estado, nota) ou levanta AdapterError."""
        tv = await self.locate(hub, device)
        if tv and tv["power"]:
            return await self._local(hub, device, command, params, adapter)
        if command == Command.TURN_OFF:
            return DeviceState(online=False, power=False), f"{device.name} já estava desligada."
        mac = device.config.get("mac")
        if not mac:
            raise DeviceUnreachable(
                f"{device.name} está fora do alcance e não achei o MAC dela na rede para acordá-la. "
                "Ligue a TV uma vez pelo controle, que a central guarda o IP e o MAC. " + HINT
            )
        for _ in range(3):
            self._wake(mac)
        waited = 0.0
        while waited < self.timeout:
            await self._sleep(self.interval)
            waited += self.interval
            try:
                state = await adapter.execute(device, command, params)
            except DeviceUnreachable:
                continue
            return state, f"{device.name} estava fora do alcance; acordei pela rede."
        raise DeviceUnreachable(
            f"Mandei acordar {device.name} pela rede, mas ela não respondeu em {int(self.timeout)} s. " + HINT
        )

    async def _local(
        self, hub: Hub, device: Device, command: Command, params: dict[str, Any], adapter: Adapter
    ) -> tuple[DeviceState, str]:
        """A TV está ligada na rede, só a nuvem não a vê: age direto, ou espera a nuvem voltar."""
        if command == Command.TURN_ON:
            return DeviceState(power=True), f"{device.name} já está ligada; a SmartThings é que não a via."
        local = hub.adapters.get("samsung_tv")
        if local is not None and command != Command.SET_VOLUME:
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
            return state
        if device.config.get("host"):
            found = await self._info(device.config["host"])
        elif time.monotonic() - self._last_scan.get(device.id, -1e9) > 60:
            self._last_scan[device.id] = time.monotonic()
            found = await self.locate(hub, device)
        else:
            found = None
        if found and found["power"]:
            return DeviceState(online=True, power=True, attributes={**state.attributes, "via": "rede local"})
        return state
