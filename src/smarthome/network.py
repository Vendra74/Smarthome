"""Visão de rede: em que rede (e IP) cada aparelho está, visto da central.

Junta o que a central sabe sem perguntar a ninguém: o próprio IP e rede, as redes
extras configuradas, o IP/MAC guardado em cada cadastro (aprendido na busca ou dado
pela pessoa) e o que a última busca de TVs encontrou, inclusive TVs sem cadastro.
Aparelhos só de nuvem (ar LG, Tuya) não anunciam IP: aparecem como "pela nuvem".
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .adapters import samsung_lan

if TYPE_CHECKING:
    from .hub import Hub

Where = Literal["central", "other", "cloud"]
LAN_ADAPTERS = {"samsung_tv", "lg_tv"}


class DeviceNetwork(BaseModel):
    id: str
    name: str
    room: str | None = None
    adapter: str
    host: str | None = None
    mac: str | None = None
    network: str | None = None  # rede /24 do IP conhecido
    where: Where  # central = mesma rede da central; other = outra rede; cloud = só pela nuvem
    how: str  # como a central sabe disso, em português


class SeenTV(BaseModel):
    name: str
    host: str
    mac: str | None = None
    power: bool = True
    network: str | None = None
    device_id: str | None = None  # cadastro com que casa, se algum


class NetworkView(BaseModel):
    central_ip: str | None = None
    central_network: str | None = None
    extra_networks: list[str] = Field(default_factory=list)
    scan_age_s: float | None = None  # None = ainda não houve busca
    devices: list[DeviceNetwork] = Field(default_factory=list)
    seen_tvs: list[SeenTV] = Field(default_factory=list)


def _net(host: str | None) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.ip_network(f"{host}/24", strict=False) if host else None
    except ValueError:
        return None


async def network_view(hub: Hub, fresh: bool = False) -> NetworkView:
    central_ip = samsung_lan.local_ip()
    central_net = _net(central_ip)
    scan = hub.recovery.last_scan()
    if fresh or scan is None:
        await hub.recovery.scan_now(max_age=0.0 if fresh else 60.0)
        scan = hub.recovery.last_scan()
    tvs = scan[1] if scan else []

    devices: list[DeviceNetwork] = []
    for d in hub.registry.all():
        host, mac = d.config.get("host"), d.config.get("mac")
        if d.adapter in LAN_ADAPTERS and not host:
            host = d.config.get("external_id") if _net(d.config.get("external_id")) else None
        net = _net(host)
        if net is None:
            where: Where = "cloud"
            how = "Controlado só pela nuvem do fabricante; o IP em casa não é informado."
        elif central_net and net == central_net:
            where = "central"
            how = "Visto pela central na rede local." if d.adapter == "smartthings" else "Falamos direto com ele na rede local."
        else:
            where = "other"
            how = "Está em outra rede: a central não alcança nem acorda este aparelho por aqui."
        devices.append(DeviceNetwork(id=d.id, name=d.name, room=d.room, adapter=d.adapter, host=host, mac=mac,
                                     network=str(net) if net else None, where=where, how=how))

    registered = [d for d in hub.registry.all() if hub.recovery.applies(d)]
    seen = []
    for tv in tvs:
        match = next((d for d in registered if hub.recovery._matches(tv, d)), None)
        if match is None:
            match = next((d for d in hub.registry.all() if d.config.get("host") == tv["host"]), None)
        net = _net(tv["host"])
        seen.append(SeenTV(name=tv["name"] or tv.get("model") or "TV Samsung", host=tv["host"], mac=tv["mac"],
                           power=tv["power"], network=str(net) if net else None, device_id=match.id if match else None))

    return NetworkView(
        central_ip=central_ip, central_network=str(central_net) if central_net else None,
        extra_networks=[str(n) for n in samsung_lan.extra_networks()],
        scan_age_s=scan[0] if scan else None, devices=devices, seen_tvs=seen,
    )
