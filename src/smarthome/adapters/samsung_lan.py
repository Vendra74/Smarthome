"""TVs Samsung na rede local, sem precisar da nuvem.

Toda TV Samsung Tizen ligada responde a SSDP e expõe em http://<ip>:8001/api/v2/
nome, modelo, MAC do Wi-Fi e se está ligada. É o que a central usa para achar a
TV sozinha (IP e MAC) e para acordá-la por Wake-on-LAN quando a SmartThings a dá
como offline.
"""

from __future__ import annotations

import asyncio
import errno
import html
import ipaddress
import logging
import re
import socket
from typing import Any

import httpx

log = logging.getLogger(__name__)

SSDP_ADDR = ("239.255.255.250", 1900)
BROADCAST_ADDR = ("255.255.255.255", 1900)
SSDP_TARGET = "urn:samsung.com:device:RemoteControlReceiver:1"
INFO_PORT = 8001

# No macOS, sem a permissão "Rede local" para o app do terminal, todo pacote para a rede
# dá "No route to host" (errno 65): nem multicast, nem broadcast, nem a varredura por IP.
MACOS_HINT = (
    "O macOS parece estar bloqueando o acesso da central à rede local: em Ajustes do Sistema > "
    "Privacidade e Segurança > Rede local, ligue o app do terminal (Terminal, iTerm ou VS Code) "
    "e reinicie a central."
)
_blocked = False


def lan_blocked() -> bool:
    """True quando a última tentativa de falar com a rede local foi barrada pelo sistema."""
    return _blocked


def _note_oserror(exc: BaseException) -> None:
    global _blocked
    if getattr(exc, "errno", None) == errno.EHOSTUNREACH or "No route to host" in str(exc):
        _blocked = True


async def tv_info(host: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """Nome, modelo, MAC e estado de energia da TV em `host`; None se ela não responde."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"http://{host}:{INFO_PORT}/api/v2/")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        _note_oserror(exc)
        return None
    global _blocked
    _blocked = False  # a rede respondeu: não há bloqueio
    device = data.get("device", {})
    return {
        "host": device.get("ip") or host,
        # a TV devolve o nome com entidades HTML ("85&quot; Crystal UHD")
        "name": html.unescape(device.get("name") or data.get("name") or ""),
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
        sent = False
        for addr in (SSDP_ADDR, BROADCAST_ADDR):
            try:
                sock.sendto(message, addr)
                sock.sendto(message, addr)
                sent = True
            except OSError as exc:
                # No macOS, sem permissão de "Rede local" para o Terminal (ou com VPN),
                # multicast dá "No route to host". A varredura por IP cobre esse caso.
                log.info("SSDP para %s falhou: %s", addr[0], exc)
                _note_oserror(exc)
        if not sent:
            return hosts
        try:
            while True:
                data, (addr, _port) = sock.recvfrom(4096)
                match = re.search(rb"LOCATION:\s*http://([^:/\r\n]+)", data, re.IGNORECASE)
                hosts.add(match.group(1).decode() if match else addr)
        except (socket.timeout, OSError):
            pass
    return hosts


def local_network() -> ipaddress.IPv4Network | None:
    """A rede /24 em que a central está (sem mandar pacote: só consulta a rota padrão)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 53))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return ipaddress.ip_network(f"{ip}/24", strict=False)


async def sweep_samsung_tvs(network: ipaddress.IPv4Network, timeout: float = 1.5) -> list[dict[str, Any]]:
    """Pergunta a cada IP da rede se é uma TV Samsung (porta 8001). Não depende de multicast."""
    limit = asyncio.Semaphore(64)

    async def probe(host: str):
        async with limit:
            return await tv_info(host, timeout=timeout)

    infos = await asyncio.gather(*(probe(str(h)) for h in network.hosts()))
    return [i for i in infos if i]


async def find_samsung_tvs(timeout: float = 3.0) -> list[dict[str, Any]]:
    """TVs Samsung ligadas (ou em standby com rede ativa) na rede local.

    Tenta SSDP primeiro (rápido); se não achar nada, varre a rede IP a IP."""
    hosts = await asyncio.to_thread(_ssdp_hosts, timeout)
    infos = await asyncio.gather(*(tv_info(h) for h in sorted(hosts)))
    found = [i for i in infos if i]
    if found:
        return found
    network = local_network()
    if network is None:
        return []
    log.debug("SSDP não achou TV; varrendo %s", network)
    found = await sweep_samsung_tvs(network)
    if not found and _blocked:
        log.warning(MACOS_HINT)
    return found


def same_tv(a: str, b: str) -> bool:
    """Compara nomes de TV ignorando caixa, espaços e o prefixo '[TV]'."""

    def clean(text: str) -> str:
        text = re.sub(r"^\s*(\[tv\]|tv)\s*", "", text.lower())
        return re.sub(r"[^a-z0-9]", "", text)

    return bool(clean(a)) and clean(a) == clean(b)
