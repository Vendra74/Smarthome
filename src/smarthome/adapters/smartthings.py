"""SmartThings (Samsung) via API REST oficial.

Cobre tudo que estiver no app SmartThings: lâmpadas, tomadas, ar-condicionado,
e também TVs Samsung. Autenticação por Personal Access Token
(https://account.smartthings.com/tokens), na variável SMARTTHINGS_TOKEN.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models import Command, Device, DeviceState, DeviceType, DiscoveredDevice
from .base import Adapter, AdapterError

API_URL = "https://api.smartthings.com/v1"
log = logging.getLogger(__name__)


def _to_smartthings(command: Command, params: dict[str, Any]) -> tuple[str, str, list[Any]]:
    """Traduz o comando da central para (capability, command, arguments) do SmartThings."""
    if command == Command.TURN_ON:
        return "switch", "on", []
    if command == Command.TURN_OFF:
        return "switch", "off", []
    if command == Command.SET_BRIGHTNESS:
        return "switchLevel", "setLevel", [int(params["level"])]
    if command == Command.SET_TEMPERATURE:
        return "thermostatCoolingSetpoint", "setCoolingSetpoint", [float(params["celsius"])]
    if command == Command.SET_MODE:
        return "airConditionerMode", "setAirConditionerMode", [params["mode"]]
    if command == Command.SET_VOLUME:
        return "audioVolume", "setVolume", [int(params["level"])]
    if command == Command.MUTE:
        return "audioMute", "mute" if params.get("muted", True) else "unmute", []
    if command == Command.SET_CHANNEL:
        return "tvChannel", "setTvChannel", [str(params["channel"])]
    if command == Command.OPEN_APP:
        return "custom.launchapp", "launchApp", [params["app"]]
    raise AdapterError(f"Comando {command.value} não mapeado para SmartThings")


def _describe_error(response: httpx.Response) -> str:
    """Mensagem curta com o código e, quando a SmartThings manda, o motivo."""
    try:
        error = response.json().get("error", {})
        details = "; ".join(d.get("message", "") for d in error.get("details", []) if d.get("message"))
        reason = details or error.get("message", "")
    except ValueError:
        reason = ""
    return f"{response.status_code} {response.reason_phrase}" + (f" ({reason})" if reason else "")


def _guess_type(capabilities: set[str]) -> DeviceType:
    if "airConditionerMode" in capabilities:
        return DeviceType.AC
    if "tvChannel" in capabilities or "audioVolume" in capabilities:
        return DeviceType.TV
    if "switchLevel" in capabilities or "colorControl" in capabilities:
        return DeviceType.LIGHT
    if "switch" in capabilities:
        return DeviceType.SWITCH
    return DeviceType.OTHER


class SmartThingsAdapter(Adapter):
    name = "smartthings"

    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
            transport=transport,
        )

    @staticmethod
    def _external_id(device: Device) -> str:
        try:
            return device.config["device_id"]
        except KeyError as exc:
            raise AdapterError(f"{device.id}: falta config.device_id do SmartThings") from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.warning("SmartThings %s %s -> %s: %s", method, url, exc.response.status_code, exc.response.text[:500])
            raise AdapterError(f"SmartThings: {_describe_error(exc.response)}") from exc
        except httpx.HTTPError as exc:
            raise AdapterError(f"SmartThings: {exc}") from exc
        return response.json() if response.content else {}

    async def _is_online(self, device: Device) -> bool:
        """A SmartThings guarda o último estado conhecido mesmo com o aparelho fora do ar;
        só o endpoint de saúde diz se ele está acessível agora."""
        try:
            data = await self._request("GET", f"/devices/{self._external_id(device)}/health")
        except AdapterError:
            return True  # sem resposta de saúde, assume o estado informado
        return data.get("state", "ONLINE") != "OFFLINE"

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        capability, st_command, arguments = _to_smartthings(command, params)
        body = {
            "commands": [
                {
                    "component": device.config.get("component", "main"),
                    "capability": capability,
                    "command": st_command,
                    "arguments": arguments,
                }
            ]
        }
        try:
            await self._request("POST", f"/devices/{self._external_id(device)}/commands", json=body)
        except AdapterError as exc:
            # 409 é a SmartThings sem conseguir falar com o aparelho agora. O endpoint de saúde
            # costuma confirmar (OFFLINE), mas em TVs Samsung em standby ele ainda diz ONLINE
            # por um tempo; nesse caso o 409 por si só já indica que a TV não respondeu.
            if "409" in str(exc) and (device.type == DeviceType.TV or not await self._is_online(device)):
                reason = str(exc).partition("(")[2].rstrip(")")
                hint = f" (SmartThings: {reason})" if reason else ""
                raise AdapterError(
                    f"{device.name} não respondeu à SmartThings: está desligada, em standby ou fora da rede{hint}. "
                    "Para ligar pela rede, ative na TV 'Ligar com dispositivo móvel'."
                ) from exc
            raise
        return await self.get_state(device)

    async def get_state(self, device: Device) -> DeviceState:
        data = await self._request("GET", f"/devices/{self._external_id(device)}/status")
        main = data.get("components", {}).get(device.config.get("component", "main"), {})

        def value(capability: str, attribute: str) -> Any:
            return main.get(capability, {}).get(attribute, {}).get("value")

        switch = value("switch", "switch")
        attributes = {
            "brightness": value("switchLevel", "level"),
            "target_temperature": value("thermostatCoolingSetpoint", "coolingSetpoint"),
            "temperature": value("temperatureMeasurement", "temperature"),
            "mode": value("airConditionerMode", "airConditionerMode"),
            "volume": value("audioVolume", "volume"),
        }
        online = await self._is_online(device)
        return DeviceState(
            online=online,
            # Fora da rede, o "ligado" guardado pela SmartThings é velho: trata como desligado.
            power=False if not online else None if switch is None else switch == "on",
            attributes={k: v for k, v in attributes.items() if v is not None},
        )

    async def discover(self) -> list[DiscoveredDevice]:
        data = await self._request("GET", "/devices")
        found = []
        for item in data.get("items", []):
            capabilities = {
                cap["id"]
                for component in item.get("components", [])
                for cap in component.get("capabilities", [])
            }
            found.append(
                DiscoveredDevice(
                    adapter=self.name,
                    external_id=item["deviceId"],
                    name=item.get("label") or item.get("name") or item["deviceId"],
                    suggested_type=_guess_type(capabilities),
                    config={"device_id": item["deviceId"]},
                )
            )
        return found
