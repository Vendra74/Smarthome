"""Teste de acordar uma TV Samsung pela rede, passo a passo, mostrando o que acontece.

Uso (na pasta do projeto, com o .env da central):
    python -m smarthome.tools.tv_wake <id-da-tv>        # ex.: tv-samsung-6-series-55-sala
    python -m smarthome.tools.tv_wake <id-da-tv> 40     # espera até 40 s (padrão 25)
    python -m smarthome.tools.tv_wake <id-da-tv> parear # com a TV LIGADA: pede a autorização dela

Mostra o IP e o MAC guardados, se a TV responde na porta 8001 agora (e o que diz
sobre energia), manda o pacote de acordar para cada destino e fica perguntando à TV
se ela acordou. Serve para separar "pacote não chega" de "TV não acorda".
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import sys
import time

from ..adapters import samsung_lan
from ..adapters.wol import _targets
from ..config import build_hub


def _send(mac: str, target: str) -> str:
    clean = mac.replace(":", "").replace("-", "")
    packet = bytes.fromhex("FF" * 6 + clean * 16)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (target, 9))
        return "enviado"
    except OSError as exc:
        return f"FALHOU: {exc}"


async def main(device_id: str, wait: float, mode: str = "") -> None:
    hub = build_hub()
    device = hub.registry.get(device_id)
    if device is None:
        ids = ", ".join(d.id for d in hub.registry.all() if d.type.value == "tv")
        print(f"TV '{device_id}' não cadastrada. TVs: {ids}")
        return
    host, mac = device.config.get("host"), device.config.get("mac")
    local = hub.adapters.get("samsung_tv")
    print(f"{device.name}: ip guardado={host or '?'}  mac guardado={mac or '?'}")
    if local is not None:
        print("Autorização da TV para a central (pareamento): " + ("já feita" if local.paired(device) else "AINDA NÃO"))
    if mode == "parear":
        if local is None or not host:
            print("Precisa do adaptador samsung_tv ativo e do IP da TV.")
            return
        print("\nLIGUE A TV pelo controle e deixe-a ligada. Pedindo autorização (até 30 s)...")
        print("Vai aparecer na tela da TV um pedido de 'Central Casa': aceite com o controle.")
        try:
            ok = await local.pair(device)
        except Exception as exc:
            print(f"  falhou: {exc}")
            return
        print("  autorizado e guardado." if ok else "  não foi aceito (ou a TV não estava ligada).")
        return
    print(f"Central em {samsung_lan.local_ip() or '?'} (rede {samsung_lan.local_network() or '?'})")
    if host and samsung_lan.local_network() and ipaddress.ip_address(host) not in samsung_lan.local_network():
        print("ATENÇÃO: o IP da TV está em outra rede que a central.")

    info = None
    if host:
        info = await samsung_lan.tv_info(host, timeout=2.0)
        if info:
            estado = ("ligada" if info["power"] else "em espera") if info["power_known"] else "responde, sem informar energia"
            print(f"Agora, na porta 8001: {estado} (nome '{info['name']}', mac {info['mac']})")
            if info["mac"] and mac and info["mac"] != mac:
                print(f"ATENÇÃO: o MAC que a TV informa ({info['mac']}) é diferente do guardado ({mac}).")
        else:
            print("Agora, na porta 8001: não responde (Wi-Fi da TV desligado, ou IP mudou).")
    print("\nProcurando TVs na rede (SSDP + varredura)...")
    tvs = await samsung_lan.find_samsung_tvs()
    for tv in tvs:
        print(f"  - {tv['name']!r} ip={tv['host']} mac={tv['mac']} {'ligada' if tv['power'] else 'em espera'}")
    if not tvs:
        print("  nenhuma")

    if not mac:
        print("\nSem MAC guardado: não dá para mandar o pacote de acordar.")
        return
    print("\nMandando pacote de acordar:")
    for target in _targets(host):
        print(f"  -> {target}: {_send(mac, target)}")
    knows_power = bool(info and info["power_known"]) if host else False
    if host and info and not knows_power:
        # TV que responde em espera mas não diz se está ligada: o Wake-on-LAN costuma ser
        # ignorado por ela; a tecla POWER pela rede é o que a acorda.
        print("\nEsta TV não informa energia pela porta 8001. Mandando a tecla POWER pela rede (8002)...")
        if local is None:
            print("  adaptador samsung_tv não está ativo nesta central; adicione-o em adapters no home.yaml.")
        elif not local.paired(device):
            print("  a TV ainda não autorizou a central, e em espera ela não mostra o pedido. Ligue a TV")
            print(f"  pelo controle e rode: python -m smarthome.tools.tv_wake {device.id} parear")
        else:
            try:
                await local.power_key(device)
                print("  tecla enviada. Se a TV estava em espera, ela deve ligar agora (na primeira vez ela pede")
                print("  autorização na tela: aceite). Se estava ligada, ela desliga: é a mesma tecla.")
            except Exception as exc:  # mostra o motivo
                print(f"  falhou: {exc}")
        return
    print(f"\nEsperando a TV responder (até {int(wait)} s)...")
    started = time.monotonic()
    while time.monotonic() - started < wait:
        await asyncio.sleep(2)
        if host:
            info = await samsung_lan.tv_info(host, timeout=2.0)
            if info and info["power"] and info["power_known"]:
                print(f"  acordou em {time.monotonic() - started:.0f} s (porta 8001 diz ligada).")
                return
    print("  não respondeu. Conclusão: o pacote não chega à TV ou ela não acorda por rede neste estado.")
    print("  Teste útil: desligue a TV pelo controle, espere 20 s e rode de novo; se então acordar,")
    print("  é só a espera longa que desliga o Wi-Fi dela.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    args = sys.argv[2:]
    mode = "parear" if "parear" in args else ""
    waits = [a for a in args if a != "parear"]
    asyncio.run(main(sys.argv[1], float(waits[0]) if waits else 25.0, mode))
