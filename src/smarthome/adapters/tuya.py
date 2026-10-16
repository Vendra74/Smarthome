"""Tuya (Intelbras Izy Smart, Smart Life, Tuya Smart) pela API de nuvem oficial.

Os aparelhos do app Izy Smart da Intelbras rodam na plataforma Tuya. Para a
central falar com eles é preciso um projeto gratuito em https://iot.tuya.com
com a conta do app vinculada (ver README). As chaves vão no .env:
TUYA_ACCESS_ID e TUYA_ACCESS_SECRET; a região costuma ser "us" para o Brasil.

Cobre lâmpadas (liga/desliga, brilho), tomadas e interruptores; outros tipos
aparecem na descoberta como sensor (só estado). Instale com: pip install -e ".[tuya]"
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import Command, Device, DeviceState, DeviceType, DiscoveredDevice
from .base import Adapter, AdapterError

# Códigos de função (DP) da Tuya que a central entende, em ordem de preferência.
SWITCH_CODES = ("switch_led", "switch_1", "switch")
BRIGHTNESS_CODES = {"bright_value_v2": (10, 1000), "bright_value": (25, 255), "bright_value_1": (10, 1000)}

# Categorias Tuya -> tipo da central
CATEGORIES = {
    "dj": DeviceType.LIGHT,  # lâmpada
    "dd": DeviceType.LIGHT,  # fita de LED
    "xdd": DeviceType.LIGHT,  # luminária de teto
    "cz": DeviceType.SWITCH,  # tomada
    "pc": DeviceType.SWITCH,  # régua / tomada múltipla
    "kg": DeviceType.SWITCH,  # interruptor
    "tdq": DeviceType.SWITCH,  # módulo relé
    "cl": DeviceType.OTHER,  # cortina
}


class TuyaAdapter(Adapter):
    name = "tuya"

    def __init__(self, access_id: str, access_secret: str, region: str = "us", cloud: Any = None) -> None:
        self._access_id, self._access_secret, self._region = access_id, access_secret, region
        self._cloud = cloud

    def _get_cloud(self):
        if self._cloud is None:
            try:
                import tinytuya
            except ImportError as exc:
                raise AdapterError("Instale o extra tuya: pip install -e '.[tuya]'") from exc
            self._cloud = tinytuya.Cloud(
                apiRegion=self._region, apiKey=self._access_id, apiSecret=self._access_secret
            )
        return self._cloud

    async def _call(self, fn, *args, **kwargs) -> Any:
        """Chama a biblioteca (bloqueante) fora do loop e traduz erros da Tuya."""
        try:
            result = await asyncio.to_thread(fn, *args, **kwargs)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(f"Tuya: {exc}") from exc
        if isinstance(result, dict) and result.get("success") is False:
            raise AdapterError(f"Tuya: {result.get('msg') or result.get('Error') or result}")
        if isinstance(result, dict) and "Error" in result:
            raise AdapterError(f"Tuya: {result.get('Err') or ''} {result['Error']}".strip())
        return result

    @staticmethod
    def _external_id(device: Device) -> str:
        try:
            return device.config["device_id"]
        except KeyError as exc:
            raise AdapterError(f"{device.id}: falta config.device_id da Tuya") from exc

    async def _status(self, device: Device) -> dict[str, Any]:
        data = await self._call(self._get_cloud().getstatus, self._external_id(device))
        return {item["code"]: item["value"] for item in data.get("result", []) if "code" in item}

    @staticmethod
    def _pick(status: dict[str, Any], codes) -> str | None:
        return next((c for c in codes if c in status), None)

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        status = await self._status(device)
        switch = device.config.get("switch_code") or self._pick(status, SWITCH_CODES)
        if command in (Command.TURN_ON, Command.TURN_OFF):
            if switch is None:
                raise AdapterError(f"{device.name} não tem interruptor na Tuya")
            commands = [{"code": switch, "value": command == Command.TURN_ON}]
        elif command == Command.SET_BRIGHTNESS:
            code = self._pick(status, BRIGHTNESS_CODES)
            if code is None:
                raise AdapterError(f"{device.name} não controla brilho")
            low, high = BRIGHTNESS_CODES[code]
            level = max(0, min(100, int(params["level"])))
            value = round(low + (high - low) * level / 100)
            commands = [{"code": code, "value": value}]
            if switch and not status.get(switch):
                commands.insert(0, {"code": switch, "value": True})
        else:
            raise AdapterError(f"Comando {command.value} não suportado na Tuya")
        await self._call(self._get_cloud().sendcommand, self._external_id(device), commands)
        return await self.get_state(device)

    async def get_state(self, device: Device) -> DeviceState:
        status = await self._status(device)
        switch = device.config.get("switch_code") or self._pick(status, SWITCH_CODES)
        attributes: dict[str, Any] = {}
        code = self._pick(status, BRIGHTNESS_CODES)
        if code is not None:
            low, high = BRIGHTNESS_CODES[code]
            attributes["brightness"] = round((status[code] - low) * 100 / (high - low))
        for key in ("temp_current", "va_temperature"):
            if key in status:
                attributes["temperature"] = status[key] / (10 if key == "va_temperature" else 1)
        return DeviceState(
            power=None if switch is None else bool(status.get(switch)),
            attributes=attributes,
        )

    async def discover(self) -> list[DiscoveredDevice]:
        items = await self._call(self._get_cloud().getdevices) or []
        if isinstance(items, dict):  # a biblioteca devolve dict só quando dá erro
            raise AdapterError(f"Tuya: {items.get('Error') or items}")
        found = []
        for item in items:
            if not item.get("id"):
                continue
            found.append(
                DiscoveredDevice(
                    adapter=self.name,
                    external_id=item["id"],
                    name=item.get("name") or item.get("product_name") or item["id"],
                    suggested_type=CATEGORIES.get(item.get("category", ""), DeviceType.SENSOR),
                    config={"device_id": item["id"], "category": item.get("category", "")},
                )
            )
        return found
