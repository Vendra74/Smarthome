"""TVs Samsung Tizen (2016+) pela rede local, via biblioteca samsungtvws.

Na primeira conexão a TV mostra um pedido de autorização; o token aceito fica
salvo em config.token_file. Ligar usa Wake-on-LAN (config.mac).
Instale com: pip install -e ".[samsung]"
Também é o caminho de recuperação quando a SmartThings perde a TV (ver recovery.py).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..models import Command, Device, DeviceState, DeviceType, DiscoveredDevice
from .base import Adapter, AdapterError
from .samsung_lan import find_samsung_tvs, tv_info
from .wol import send_magic_packet

# IDs de apps conhecidos na Tizen; o usuário pode passar o ID direto também.
KNOWN_APPS = {
    "netflix": "3201907018807",
    "youtube": "111299001912",
    "prime video": "3201910019365",
    "disney+": "3201901017640",
    "globoplay": "3201908019041",
}


class SamsungTVAdapter(Adapter):
    name = "samsung_tv"

    @staticmethod
    def _token_file(device: Device) -> Path:
        return Path(device.config.get("token_file", f"tokens/{device.id}.token"))

    def paired(self, device: Device) -> bool:
        """A TV já autorizou a central (há token guardado)? Sem isso, a tecla pela rede só
        funciona com a TV ligada e aceitando o pedido na tela."""
        path = self._token_file(device)
        return path.exists() and path.stat().st_size > 0

    async def pair(self, device: Device, timeout: float = 30.0) -> bool:
        """Pede autorização à TV (ela mostra 'Central Casa' na tela; a pessoa aceita com o
        controle). Só funciona com a TV ligada. Devolve True se o token foi guardado."""
        def do() -> None:
            tv = self._connect(device, timeout=timeout)
            tv.open()
            tv.close()

        try:
            await asyncio.to_thread(do)
        except Exception as exc:
            raise AdapterError(f"TV Samsung {device.id}: pareamento falhou: {exc}") from exc
        return self.paired(device)

    def _connect(self, device: Device, timeout: float = 5):
        try:
            import urllib3
            from samsungtvws import SamsungTVWS
        except ImportError as exc:
            raise AdapterError("Instale o extra samsung: pip install -e '.[samsung]'") from exc
        # A TV usa certificado próprio; o aviso a cada comando só polui o log.
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        token_file = self._token_file(device)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        return SamsungTVWS(
            host=device.config["host"],
            port=device.config.get("port", 8002),
            token_file=str(token_file),
            name="Central Casa",
            timeout=timeout,  # sem isso, TV desligada segura cada comando (e o encerramento) por 75 s
        )

    def _run(self, device: Device, command: Command, params: dict[str, Any]) -> None:
        tv = self._connect(device)
        if command == Command.TURN_OFF:
            tv.send_key("KEY_POWER")
        elif command == Command.MUTE:
            tv.send_key("KEY_MUTE")
        elif command == Command.SET_CHANNEL:
            for digit in str(params["channel"]):
                tv.send_key(f"KEY_{digit}")
            tv.send_key("KEY_ENTER")
        elif command == Command.OPEN_APP:
            app = str(params["app"])
            tv.run_app(KNOWN_APPS.get(app.lower(), app))
        else:
            raise AdapterError(
                f"Comando {command.value} não suportado pela rede local; "
                "cadastre a TV pelo SmartThings para volume absoluto"
            )

    async def power_key(self, device: Device) -> None:
        """Manda a tecla POWER pela rede. TV em espera com Wi-Fi ligado acorda com isso
        mesmo quando ignora o Wake-on-LAN; em TV ligada, desliga (quem chama decide)."""
        try:
            await asyncio.to_thread(lambda: self._connect(device).send_key("KEY_POWER"))
        except Exception as exc:
            raise AdapterError(f"TV Samsung {device.id}: tecla POWER pela rede falhou: {exc}") from exc

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        if command == Command.TURN_ON:
            mac = device.config.get("mac")
            if not mac:
                raise AdapterError(f"{device.id}: falta config.mac para ligar por Wake-on-LAN")
            send_magic_packet(mac)
            return DeviceState(power=True)
        try:
            await asyncio.to_thread(self._run, device, command, params)
        except AdapterError:
            raise
        except Exception as exc:  # a biblioteca levanta vários tipos de erro de rede
            raise AdapterError(f"TV Samsung {device.id}: {exc}") from exc
        return await self.get_state(device) if command != Command.TURN_OFF else DeviceState(power=False)

    async def discover(self) -> list[DiscoveredDevice]:
        """TVs Samsung ligadas na rede local (SSDP e varredura), com IP e MAC prontos."""
        return [
            DiscoveredDevice(
                adapter=self.name,
                external_id=tv["host"],
                name=tv["name"] or tv["model"] or tv["host"],
                suggested_type=DeviceType.TV,
                config={"host": tv["host"], **({"mac": tv["mac"]} if tv["mac"] else {})},
            )
            for tv in await find_samsung_tvs()
        ]

    async def get_state(self, device: Device) -> DeviceState:
        # Pergunta direto à porta 8001 com timeout curto: a biblioteca não tem timeout e,
        # com a TV desligada, cada consulta esperava os 75 s do TCP do macOS.
        info = await tv_info(device.config["host"], timeout=2.0)
        if info is None:
            return DeviceState(online=False, power=False)
        return DeviceState(power=info["power"])
