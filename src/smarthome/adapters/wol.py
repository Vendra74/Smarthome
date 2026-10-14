"""Wake-on-LAN: as TVs desligadas não escutam a rede, só o pacote mágico."""

from __future__ import annotations

import socket


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    clean = mac.replace(":", "").replace("-", "")
    if len(clean) != 12:
        raise ValueError(f"MAC inválido: {mac}")
    packet = bytes.fromhex("FF" * 6 + clean * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
