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
import os
import re
import shutil
import socket
import subprocess
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


def local_ip() -> str | None:
    """O IP da central na rede de casa (sem mandar pacote: só consulta a rota padrão)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 53))
            return sock.getsockname()[0]
    except OSError:
        return None


def local_network() -> ipaddress.IPv4Network | None:
    """A rede /24 em que a central está."""
    ip = local_ip()
    return ipaddress.ip_network(f"{ip}/24", strict=False) if ip else None


async def sweep_samsung_tvs(network: ipaddress.IPv4Network, timeout: float = 1.5) -> list[dict[str, Any]]:
    """Pergunta a cada IP da rede se é uma TV Samsung (porta 8001). Não depende de multicast."""
    limit = asyncio.Semaphore(64)

    async def probe(host: str):
        async with limit:
            return await tv_info(host, timeout=timeout)

    infos = await asyncio.gather(*(probe(str(h)) for h in network.hosts()))
    return [i for i in infos if i]


def extra_networks() -> list[ipaddress.IPv4Network]:
    """Outras redes da casa a varrer (TV_SCAN_NETWORKS=192.168.0.0/24,...), para casas com dois
    roteadores em que as TVs não estão no mesmo Wi-Fi da central."""
    raw = os.environ.get("TV_SCAN_NETWORKS", "")
    networks = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item if "/" in item else f"{item}/24", strict=False)
        except ValueError:
            log.warning("TV_SCAN_NETWORKS: rede inválida ignorada: %r", item)
            continue
        if network.num_addresses <= 256:
            networks.append(network)
        else:
            log.warning("TV_SCAN_NETWORKS: %s é grande demais (máximo /24), ignorada", network)
    return networks


# Redes vizinhas descobertas sozinhas (casa com modem + roteador mesh em modo roteador):
# a central está na rede do segundo roteador e as TVs podem estar na do modem.
_neighbors: tuple[float, list[ipaddress.IPv4Network]] | None = None
NEIGHBOR_TTL = 600.0
MAX_NEIGHBORS = 3  # cada rede a mais custa uns segundos por varredura
COMMON_GATEWAYS = ["192.168.0.1", "192.168.1.1", "192.168.15.1", "192.168.100.1", "10.0.0.1",
                   "10.1.1.1", "192.168.2.1", "192.168.3.1", "192.168.88.1", "192.168.10.1", "172.16.0.1"]


def _private(ip: str) -> ipaddress.IPv4Address | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    return addr if addr.version == 4 and addr.is_private else None


def _run_traceroute() -> str:
    """Roda o traceroute do sistema sem fork(): no macOS, um fork() dentro de um processo com
    threads derruba o filho (fork safety do Objective-C) e abre o relatório de erro da Apple.
    Com caminho completo e close_fds=False o Python usa posix_spawn, que não tem esse problema."""
    for name, args in (("traceroute", ["-n", "-m", "3", "-q", "1", "-w", "1", "8.8.8.8"]),
                       ("tracepath", ["-n", "-m", "3", "8.8.8.8"])):
        path = shutil.which(name)
        if not path:
            continue
        try:
            done = subprocess.run([path, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  close_fds=False, timeout=8, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        return done.stdout.decode(errors="ignore")
    return ""


async def _traceroute_hops() -> list[str]:
    """IPs dos primeiros saltos até a internet: o segundo costuma ser o modem (a outra rede)."""
    out = await asyncio.to_thread(_run_traceroute)
    hops = re.findall(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", out)
    return [h for h in hops if h != "8.8.8.8"]


async def _gateway_answers(ip: str, timeout: float = 0.7) -> bool:
    """Há um roteador neste IP? Exige que ele responda algo na porta 80 (a página de admin)."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, 80), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    try:
        writer.write(b"HEAD / HTTP/1.0\r\nHost: " + ip.encode() + b"\r\n\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.read(64), timeout)
        return bool(data)
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        writer.close()


async def neighbor_networks(force: bool = False) -> list[ipaddress.IPv4Network]:
    """Outras redes privadas da casa, descobertas sem configuração: pelos saltos até a internet
    e por roteadores que respondem nos endereços de fábrica mais comuns. Resultado vale 10 min."""
    global _neighbors
    import time as _time
    if _neighbors and not force and _time.monotonic() - _neighbors[0] < NEIGHBOR_TTL:
        return _neighbors[1]
    local = local_network()
    found: dict[str, ipaddress.IPv4Network] = {}
    for hop in await _traceroute_hops():
        if (addr := _private(hop)) is not None:
            net = ipaddress.ip_network(f"{addr}/24", strict=False)
            if net != local:
                found[str(net)] = net
    candidates = [g for g in COMMON_GATEWAYS
                  if (n := ipaddress.ip_network(f"{g}/24", strict=False)) != local and str(n) not in found]
    answers = await asyncio.gather(*(_gateway_answers(g) for g in candidates))
    for gateway, ok in zip(candidates, answers):
        if ok:
            net = ipaddress.ip_network(f"{gateway}/24", strict=False)
            found[str(net)] = net
    networks = list(found.values())[:MAX_NEIGHBORS]
    if networks:
        log.info("Outras redes da casa: %s", ", ".join(str(n) for n in networks))
    _neighbors = (_time.monotonic(), networks)
    return networks


async def scan_networks() -> list[ipaddress.IPv4Network]:
    """Todas as redes a varrer: a da central, as vizinhas descobertas e as configuradas."""
    nets: dict[str, ipaddress.IPv4Network] = {}
    for n in [local_network()] + await neighbor_networks() + extra_networks():
        if n is not None:
            nets.setdefault(str(n), n)
    return list(nets.values())


async def find_samsung_tvs(timeout: float = 3.0) -> list[dict[str, Any]]:
    """TVs Samsung ligadas (ou em standby com rede ativa) na rede local.

    Junta o que o SSDP anuncia com uma varredura IP a IP da rede da central, das redes
    vizinhas descobertas sozinhas e das configuradas: TV que não responde multicast (ou está no outro roteador) ainda
    aparece."""
    hosts = await asyncio.to_thread(_ssdp_hosts, timeout)
    infos = await asyncio.gather(*(tv_info(h) for h in sorted(hosts)))
    found = {i["host"]: i for i in infos if i}
    for network in await scan_networks():
        log.debug("Varrendo %s", network)
        for tv in await sweep_samsung_tvs(network):
            found.setdefault(tv["host"], tv)
    if not found and _blocked:
        log.warning(MACOS_HINT)
    return sorted(found.values(), key=lambda t: ipaddress.ip_address(t["host"]) if _is_ip(t["host"]) else ipaddress.ip_address("0.0.0.0"))


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return False


def same_tv(a: str, b: str) -> bool:
    """Compara nomes de TV ignorando caixa, espaços e o prefixo '[TV]'."""

    def clean(text: str) -> str:
        text = re.sub(r"^\s*(\[tv\]|tv)\s*", "", text.lower())
        return re.sub(r"[^a-z0-9]", "", text)

    return bool(clean(a)) and clean(a) == clean(b)
