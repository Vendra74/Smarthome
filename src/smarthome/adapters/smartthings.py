"""SmartThings (Samsung) via API REST oficial.

Cobre tudo que estiver no app SmartThings: lâmpadas, tomadas, ar-condicionado,
e também TVs Samsung. Autenticação pela conta (OAuth, tela "Contas") ou, para
quem publica o app, por Personal Access Token na variável SMARTTHINGS_TOKEN.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..accounts import OAuthError
from ..models import Command, Device, DeviceState, DeviceType, DiscoveredDevice
from .base import Adapter, AdapterError, DeviceUnreachable

API_URL = "https://api.smartthings.com/v1"
log = logging.getLogger(__name__)

# A SmartThings limita a ~10 consultas por minuto por aparelho (429). Com o app
# atualizando a cada 15 s em mais de uma tela, estoura; por isso o estado é
# guardado por um tempo e a saúde é consultada bem menos.
STATE_TTL = 20.0
HEALTH_TTL = 60.0


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
    if command == Command.SET_FAN_SPEED:
        return "airConditionerFanMode", "setFanMode", [params["speed"]]
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

    def __init__(
        self,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        token_provider: Callable[..., Awaitable[str]] | None = None,
    ) -> None:
        """`token` é um token fixo; `token_provider(force=False)` entrega o token da conta
        conectada por OAuth e, com `force=True`, renova (chamado quando a API responde 401)."""
        if token is None and token_provider is None:
            raise ValueError("SmartThingsAdapter precisa de token ou token_provider")
        self._token, self._token_provider = token, token_provider
        self._client = httpx.AsyncClient(base_url=API_URL, timeout=10.0, transport=transport)
        self._state_cache: dict[str, tuple[float, DeviceState]] = {}
        self._health_cache: dict[str, tuple[float, bool]] = {}

    @staticmethod
    def _external_id(device: Device) -> str:
        try:
            return device.config["device_id"]
        except KeyError as exc:
            raise AdapterError(f"{device.id}: falta config.device_id do SmartThings") from exc

    async def _auth(self, force: bool = False) -> dict[str, str]:
        token = self._token if self._token_provider is None else await self._token_provider(force=force)
        return {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            try:
                response = await self._client.request(method, url, headers=await self._auth(), **kwargs)
                if response.status_code == 401 and self._token_provider is not None:
                    # token da conta venceu: renova uma vez e repete
                    response = await self._client.request(method, url, headers=await self._auth(force=True), **kwargs)
                response.raise_for_status()
            except OAuthError as exc:
                raise AdapterError(f"SmartThings: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            # 409 em TV é rotina (standby); fica em INFO para não parecer erro no log
            level = logging.INFO if exc.response.status_code == 409 else logging.WARNING
            log.log(level, "SmartThings %s %s -> %s: %s", method, url, exc.response.status_code, exc.response.text[:500])
            raise AdapterError(f"SmartThings: {_describe_error(exc.response)}") from exc
        except httpx.HTTPError as exc:
            raise AdapterError(f"SmartThings: {exc}") from exc
        return response.json() if response.content else {}

    async def _is_online(self, device: Device, fresh: bool = False) -> bool:
        """A SmartThings guarda o último estado conhecido mesmo com o aparelho fora do ar;
        só o endpoint de saúde diz se ele está acessível agora."""
        external_id = self._external_id(device)
        cached = self._health_cache.get(external_id)
        if cached and not fresh and time.monotonic() - cached[0] < HEALTH_TTL:
            return cached[1]
        try:
            data = await self._request("GET", f"/devices/{external_id}/health")
        except AdapterError:
            return cached[1] if cached else True  # sem resposta de saúde, assume o estado informado
        online = data.get("state", "ONLINE") != "OFFLINE"
        self._health_cache[external_id] = (time.monotonic(), online)
        return online

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
            # 422 "response code is NOT_FOUND" num desligar: a TV costuma ter obedecido e
            # entrado em standby antes de responder à nuvem. Confere em vez de dar erro.
            if command == Command.TURN_OFF and "422" in str(exc) and "NOT_FOUND" in str(exc):
                await asyncio.sleep(2)
                self._state_cache.pop(self._external_id(device), None)
                self._health_cache.pop(self._external_id(device), None)
                try:
                    state = await self.get_state(device)
                except AdapterError:
                    raise exc from None
                if not state.online or not state.power:
                    log.info("%s: a SmartThings deu 422 no desligar, mas a TV desligou", device.id)
                    return DeviceState(online=state.online, power=False, attributes=state.attributes)
            # 409 é a SmartThings sem conseguir falar com o aparelho agora. O endpoint de saúde
            # costuma confirmar (OFFLINE), mas em TVs Samsung em standby ele ainda diz ONLINE
            # por um tempo; nesse caso o 409 por si só já indica que a TV não respondeu.
            if "409" in str(exc) and (device.type == DeviceType.TV or not await self._is_online(device, fresh=True)):
                reason = str(exc).partition("(")[2].rstrip(")")
                hint = f" (SmartThings: {reason})" if reason else ""
                raise DeviceUnreachable(
                    f"{device.name} não respondeu à SmartThings: está desligada, em standby ou fora da rede{hint}. "
                    "Para ligar pela rede, ative na TV 'Ligar com dispositivo móvel'."
                ) from exc
            raise
        external_id = self._external_id(device)
        previous = self._state_cache.pop(external_id, (0.0, None))[1]
        self._health_cache.pop(external_id, None)
        # A nuvem demora alguns segundos para refletir o comando (e, em TV, chega a dizer
        # "ligada" por um tempo depois de desligar). O estado devolvido já traz o que foi
        # pedido e vale como cache até a próxima leitura, para o app não "voltar atrás".
        if previous is None:
            try:
                previous = await self.get_state(device)
            except AdapterError:
                previous = None
        state = self._optimistic(command, params, previous)
        if state is None:
            return previous if previous is not None else await self.get_state(device)
        self._state_cache[external_id] = (time.monotonic(), state)
        return state

    @staticmethod
    def _optimistic(command: Command, params: dict[str, Any], previous: DeviceState | None) -> DeviceState | None:
        attributes = dict(previous.attributes) if previous else {}
        power = previous.power if previous else None
        if command == Command.TURN_ON:
            power = True
        elif command == Command.TURN_OFF:
            power = False
        elif command == Command.SET_TEMPERATURE:
            attributes["target_temperature"] = params.get("celsius")
        elif command == Command.SET_BRIGHTNESS:
            attributes["brightness"] = params.get("level")
        elif command == Command.SET_VOLUME:
            attributes["volume"] = params.get("level")
        elif command == Command.SET_MODE:
            attributes["mode"] = params.get("mode")
        elif command == Command.SET_FAN_SPEED:
            attributes["fan_speed"] = params.get("speed")
        else:
            return None
        return DeviceState(online=True, power=power, attributes=attributes)

    async def get_state(self, device: Device) -> DeviceState:
        external_id = self._external_id(device)
        cached = self._state_cache.get(external_id)
        if cached and time.monotonic() - cached[0] < STATE_TTL:
            return cached[1]
        try:
            data = await self._request("GET", f"/devices/{external_id}/status")
        except AdapterError as exc:
            if "429" in str(exc) and cached:
                return cached[1]  # limite de consultas: vale o último estado conhecido
            raise
        main = data.get("components", {}).get(device.config.get("component", "main"), {})

        def value(capability: str, attribute: str) -> Any:
            return main.get(capability, {}).get(attribute, {}).get("value")

        switch = value("switch", "switch")
        attributes = {
            "brightness": value("switchLevel", "level"),
            "target_temperature": value("thermostatCoolingSetpoint", "coolingSetpoint"),
            "temperature": value("temperatureMeasurement", "temperature"),
            "mode": value("airConditionerMode", "airConditionerMode"),
            "fan_speed": value("airConditionerFanMode", "fanMode"),
            "volume": value("audioVolume", "volume"),
        }
        online = await self._is_online(device)
        state = DeviceState(
            online=online,
            # Fora da rede, o "ligado" guardado pela SmartThings é velho: trata como desligado.
            power=False if not online else None if switch is None else switch == "on",
            attributes={k: v for k, v in attributes.items() if v is not None},
        )
        self._state_cache[external_id] = (time.monotonic(), state)
        return state

    async def device_label(self, device: Device) -> str | None:
        """Nome do aparelho na SmartThings; em TVs Samsung é o nome que a TV anuncia na rede."""
        data = await self._request("GET", f"/devices/{self._external_id(device)}")
        return data.get("label") or data.get("name")

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
