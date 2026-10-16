"""TVs LG webOS pela rede local, via biblioteca aiowebostv.

Na primeira conexão a TV pede para aceitar o pareamento; a chave gerada deve
ser guardada em config.client_key. Ligar usa Wake-on-LAN (config.mac).
Instale com: pip install -e ".[lg]"
Ainda não testado com hardware real.
"""

from __future__ import annotations

from typing import Any

from ..models import Command, Device, DeviceState
from .base import Adapter, AdapterError
from .wol import send_magic_packet

KNOWN_APPS = {
    "netflix": "netflix",
    "youtube": "youtube.leanback.v4",
    "prime video": "amazon",
    "disney+": "com.disney.disneyplus-prod",
    "globoplay": "globoplay",
}


class LGTVAdapter(Adapter):
    name = "lg_tv"

    async def _client(self, device: Device):
        try:
            from aiowebostv import WebOsClient
        except ImportError as exc:
            raise AdapterError("Instale o extra lg: pip install -e '.[lg]'") from exc
        client = WebOsClient(device.config["host"], device.config.get("client_key"))
        try:
            await client.connect()
        except Exception as exc:
            raise AdapterError(f"TV LG {device.id} inacessível: {exc}") from exc
        return client

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        if command == Command.TURN_ON:
            mac = device.config.get("mac")
            if not mac:
                raise AdapterError(f"{device.id}: falta config.mac para ligar por Wake-on-LAN")
            send_magic_packet(mac)
            return DeviceState(power=True)

        client = await self._client(device)
        try:
            if command == Command.TURN_OFF:
                await client.power_off()
                return DeviceState(power=False)
            if command == Command.SET_VOLUME:
                await client.set_volume(int(params["level"]))
            elif command == Command.MUTE:
                await client.set_mute(bool(params.get("muted", True)))
            elif command == Command.OPEN_APP:
                app = str(params["app"])
                await client.launch_app(KNOWN_APPS.get(app.lower(), app))
            else:
                raise AdapterError(f"Comando {command.value} não suportado na TV LG")
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"TV LG {device.id}: {exc}") from exc
        finally:
            await client.disconnect()
        return DeviceState(power=True)

    async def get_state(self, device: Device) -> DeviceState:
        try:
            client = await self._client(device)
        except AdapterError:
            return DeviceState(online=False, power=False)
        await client.disconnect()
        return DeviceState(power=True)
