"""Tuya pela conta do app (Smart Life / Tuya Smart), sem projeto de desenvolvedor.

Usa o SDK de compartilhamento de dispositivos da Tuya (`tuya-device-sharing-sdk`),
o mesmo caminho do Home Assistant: o usuário lê um QR code no app e a central
recebe tokens da conta dele. Intelbras Izy Smart não permite esse vínculo, mas
os aparelhos podem ser pareados de novo no Smart Life e entram por aqui.

Usa o mesmo nome de adaptador ("tuya") e o mesmo `config.device_id` do
adaptador por chaves de projeto, então um dispositivo cadastrado vale nos dois.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..models import Command, Device, DeviceState, DeviceType, DiscoveredDevice
from .base import Adapter, AdapterError
from .tuya import BRIGHTNESS_CODES, CATEGORIES, SWITCH_CODES

log = logging.getLogger(__name__)
CACHE_TTL = 20.0


class TuyaSharingAdapter(Adapter):
    name = "tuya"

    def __init__(self, account: dict[str, Any], on_token: Any = None, manager: Any = None) -> None:
        self._account = account
        self._on_token = on_token  # chamado com os tokens novos quando o SDK renova
        self._manager = manager
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def _get_manager(self):
        if self._manager is None:
            from tuya_sharing import Manager, SharingTokenListener

            from ..accounts import TUYA_CLIENT_ID

            on_token = self._on_token

            class Listener(SharingTokenListener):
                def update_token(self, token_info: dict[str, Any]) -> None:
                    if on_token:
                        on_token(token_info)

            self._manager = Manager(
                TUYA_CLIENT_ID,
                self._account["user_code"],
                self._account["terminal_id"],
                self._account["endpoint"],
                self._account["token_response"],
                Listener(),
            )
        return self._manager

    async def _call(self, fn, *args) -> Any:
        try:
            return await asyncio.to_thread(fn, *args)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Smart Life: {exc}") from exc

    async def _devices(self, fresh: bool = False) -> dict[str, Any]:
        async with self._lock:
            if fresh or time.monotonic() - self._loaded_at > CACHE_TTL:
                await self._call(self._get_manager().update_device_cache)
                self._loaded_at = time.monotonic()
        return self._get_manager().device_map

    async def _device(self, device: Device, fresh: bool = False) -> Any:
        try:
            external_id = device.config["device_id"]
        except KeyError as exc:
            raise AdapterError(f"{device.id}: falta config.device_id da Tuya") from exc
        found = (await self._devices(fresh)).get(external_id)
        if found is None:
            raise AdapterError(f"{device.name} não está mais na conta Smart Life")
        return found

    @staticmethod
    def _pick(item: Any, codes) -> str | None:
        known = set(item.status) | set(item.function)
        return next((c for c in codes if c in known), None)

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        item = await self._device(device)
        switch = device.config.get("switch_code") or self._pick(item, SWITCH_CODES)
        if command in (Command.TURN_ON, Command.TURN_OFF):
            if switch is None:
                raise AdapterError(f"{device.name} não tem interruptor na Tuya")
            commands = [{"code": switch, "value": command == Command.TURN_ON}]
        elif command == Command.SET_BRIGHTNESS:
            code = self._pick(item, BRIGHTNESS_CODES)
            if code is None:
                raise AdapterError(f"{device.name} não controla brilho")
            low, high = BRIGHTNESS_CODES[code]
            level = max(0, min(100, int(params["level"])))
            commands = [{"code": code, "value": round(low + (high - low) * level / 100)}]
            if switch and not item.status.get(switch):
                commands.insert(0, {"code": switch, "value": True})
        else:
            raise AdapterError(f"Comando {command.value} não suportado na Tuya")
        await self._call(self._get_manager().send_commands, item.id, commands)
        # A nuvem demora a refletir; o que foi mandado vale como estado até a próxima leitura.
        for c in commands:
            item.status[c["code"]] = c["value"]
        self._loaded_at = time.monotonic()
        return self._state(device, item)

    def _state(self, device: Device, item: Any) -> DeviceState:
        status = item.status
        switch = device.config.get("switch_code") or self._pick(item, SWITCH_CODES)
        attributes: dict[str, Any] = {}
        code = self._pick(item, BRIGHTNESS_CODES)
        if code is not None and code in status:
            low, high = BRIGHTNESS_CODES[code]
            attributes["brightness"] = round((status[code] - low) * 100 / (high - low))
        for key in ("temp_current", "va_temperature"):
            if key in status:
                attributes["temperature"] = status[key] / (10 if key == "va_temperature" else 1)
        return DeviceState(
            power=None if switch is None else bool(status.get(switch)),
            online=bool(getattr(item, "online", True)),
            attributes=attributes,
        )

    async def get_state(self, device: Device) -> DeviceState:
        return self._state(device, await self._device(device))

    async def discover(self) -> list[DiscoveredDevice]:
        items = await self._devices(fresh=True)
        return [
            DiscoveredDevice(
                adapter=self.name,
                external_id=item.id,
                name=item.name or item.product_name or item.id,
                suggested_type=CATEGORIES.get(item.category, DeviceType.SENSOR),
                config={"device_id": item.id, "category": item.category},
            )
            for item in items.values()
        ]
