"""TVs Samsung na rede local, sem precisar da nuvem.

Toda TV Samsung Tizen ligada responde a SSDP e expõe em http://<ip>:8001/api/v2/
nome, modelo, MAC do Wi-Fi e se está ligada. É o que a central usa para achar a
TV sozinha (IP e MAC) e para acordá-la por Wake-on-LAN quando a SmartThings a dá
como offline.
"""

from __future__ import annotations

import asyncio
import re
import socket
from typing import Any

import httpx

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_TARGET = "urn:samsung.com:device:RemoteControlReceiver:1"
INFO_PORT = 8001


async def tv_info(host: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """Nome, modelo, MAC e estado de energia da TV em `host`; None se ela não responde."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"http://{host}:{INFO_PORT}/api/v2/")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    device = data.get("device", {})
    return {
        "host": device.get("ip") or host,
        "name": device.get("name") or data.get("name") or "",
        "model": device.get("modelName", ""),
        "mac": (device.get("wifiMac") or "").upper() or None,
        "power": device.get("PowerState", "on") == "on",
    }


def _ssdp_hosts(timeout: float) -> set[str]:
    message = "\r\n".join(
        [
            "M-SEARCH * HTTP/1.1",
            f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}",
            'MAN: "ssdp:discover"',
            "MX: 1",
            f"ST: {SSDP_TARGET}",
            "",
            "",
        ]
    ).encode()
    hosts: set[str] = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        for _ in range(2):
            sock.sendto(message, SSDP_ADDR)
        try:
            while True:
                data, (addr, _port) = sock.recvfrom(4096)
                match = re.search(rb"LOCATION:\s*http://([^:/\r\n]+)", data, re.IGNORECASE)
                hosts.add(match.group(1).decode() if match else addr)
        except (socket.timeout, OSError):
            pass
    return hosts


async def find_samsung_tvs(timeout: float = 3.0) -> list[dict[str, Any]]:
    """TVs Samsung ligadas (ou em standby com rede ativa) na rede local."""
    hosts = await asyncio.to_thread(_ssdp_hosts, timeout)
    infos = await asyncio.gather(*(tv_info(h) for h in sorted(hosts)))
    return [i for i in infos if i]


def same_tv(a: str, b: str) -> bool:
    """Compara nomes de TV ignorando caixa, espaços e o prefixo '[TV]'."""

    def clean(text: str) -> str:
        text = re.sub(r"^\s*(\[tv\]|tv)\s*", "", text.lower())
        return re.sub(r"[^a-z0-9]", "", text)

    return bool(clean(a)) and clean(a) == clean(b)
