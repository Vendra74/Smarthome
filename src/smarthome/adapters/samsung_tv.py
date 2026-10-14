"""TVs Samsung Tizen (2016+) pela rede local, via biblioteca samsungtvws.

Na primeira conexão a TV mostra um pedido de autorização; o token aceito fica
salvo em config.token_file. Ligar usa Wake-on-LAN (config.mac).
Instale com: pip install -e ".[samsung]"
Ainda não testado com hardware real.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import Command, Device, DeviceState
from .base import Adapter, AdapterError
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

    def _connect(self, device: Device):
        try:
            from samsungtvws import SamsungTVWS
        except ImportError as exc:
            raise AdapterError("Instale o extra samsung: pip install -e '.[samsung]'") from exc
        return SamsungTVWS(
            host=device.config["host"],
            port=device.config.get("port", 8002),
            token_file=device.config.get("token_file", f"tokens/{device.id}.token"),
            name="Central Casa",
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

    async def get_state(self, device: Device) -> DeviceState:
        def info() -> dict[str, Any]:
            return self._connect(device).rest_device_info()

        try:
            data = await asyncio.to_thread(info)
        except Exception:
            return DeviceState(online=False, power=False)
        power = data.get("device", {}).get("PowerState", "on") == "on"
        return DeviceState(power=power)
