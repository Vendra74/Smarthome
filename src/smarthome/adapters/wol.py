"""Wake-on-LAN: as TVs desligadas não escutam a rede, só o pacote mágico."""

from __future__ import annotations

import ipaddress
import logging
import socket

log = logging.getLogger(__name__)


def _targets(host: str | None) -> list[str]:
    """255.255.255.255 nem sempre sai pela interface certa (macOS); por isso o pacote vai
    também para o broadcast da sub-rede da TV e para o IP dela (a Wi-Fi ainda o entrega)."""
    targets = ["255.255.255.255"]
    if host:
        try:
            network = ipaddress.ip_network(f"{host}/24", strict=False)
            targets += [str(network.broadcast_address), host]
        except ValueError:
            pass
    return targets


def send_magic_packet(mac: str, host: str | None = None, port: int = 9) -> None:
    clean = mac.replace(":", "").replace("-", "")
    if len(clean) != 12:
        raise ValueError(f"MAC inválido: {mac}")
    packet = bytes.fromhex("FF" * 6 + clean * 16)
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for target in _targets(host):
            try:
                sock.sendto(packet, (target, port))
                sent += 1
            except OSError as exc:
                log.info("Wake-on-LAN para %s falhou: %s", target, exc)
    if not sent:
        raise OSError(f"Wake-on-LAN não saiu para {mac}")
