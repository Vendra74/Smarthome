"""Busca de TVs Samsung na rede, igual à que a central faz, mostrando o resultado na hora.

Uso (na pasta do projeto, com o .env da central):
    python -m smarthome.tools.tv_scan
    python -m smarthome.tools.tv_scan 192.168.0.0/24    # varre também essa rede

Mostra em que rede a central está, quais TVs responderam (nome, IP, MAC, ligada ou não)
e com qual TV cadastrada cada uma casa. Não imprime tokens.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from ..adapters import samsung_lan
from ..config import build_hub


async def main(networks: list[str]) -> None:
    logging.basicConfig(level=logging.WARNING)
    load_dotenv()
    if networks:
        os.environ["TV_SCAN_NETWORKS"] = ",".join(networks)
    print(f"Rede da central: {samsung_lan.local_network() or 'desconhecida'}")
    neighbors = await samsung_lan.neighbor_networks(force=True)
    if neighbors:
        print("Outras redes descobertas na casa: " + ", ".join(str(n) for n in neighbors))
    extra = samsung_lan.extra_networks()
    if extra:
        print("Redes extras configuradas: " + ", ".join(str(n) for n in extra))
    print("Procurando TVs Samsung (leva uns segundos)...\n")
    tvs = await samsung_lan.find_samsung_tvs()
    if not tvs:
        print("Nenhuma TV Samsung respondeu.")
        if samsung_lan.lan_blocked():
            print(samsung_lan.MACOS_HINT)
        else:
            print("Se há TV ligada, ela deve estar no outro Wi-Fi da casa: rode de novo passando essa rede, "
                  "por exemplo `python -m smarthome.tools.tv_scan 192.168.0.0/24`.")
        return
    hub = build_hub()
    registered = [d for d in hub.registry.all() if hub.recovery.applies(d)]
    for tv in tvs:
        power = "ligada" if tv["power"] else "em espera"
        print(f"- {tv['name'] or '?'!r}  ip={tv['host']}  mac={tv['mac'] or '?'}  modelo={tv['model'] or '?'}  {power}")
        match = next((d for d in registered if hub.recovery._matches(tv, d)), None)
        if match:
            print(f"    casa com a TV cadastrada {match.name!r} ({match.id})")
        elif registered:
            print("    não casa com nenhuma TV cadastrada da SmartThings: nomes cadastrados são "
                  + ", ".join(repr(d.config.get("st_name") or d.name) for d in registered))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
