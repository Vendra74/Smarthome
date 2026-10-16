"""LG ThinQ pela API oficial ThinQ Connect, via SDK thinqconnect da LG.

Cobre os eletrodomésticos do app LG ThinQ. Hoje controla ar-condicionado
(liga/desliga, modo, temperatura); os demais aparelhos aparecem na descoberta
e mostram estado, mas ainda não recebem comandos. TVs LG não estão na API
ThinQ Connect: elas usam o adaptador lg_tv, pela rede local.

Token pessoal (PAT) em https://connect-pat.lgthinq.com, na variável indicada
no home.yaml (padrão LG_THINQ_TOKEN). Instale com: pip install -e ".[lg]"
Ainda não testado com aparelho real.
"""

from __future__ import annotations

import ssl
import uuid
from typing import Any

from ..models import Command, Device, DeviceState, DeviceType, DiscoveredDevice
from .base import Adapter, AdapterError

AIR_CONDITIONER = "DEVICE_AIR_CONDITIONER"

# Modos da central -> modos (job mode) do ar LG
AC_MODES = {"cool": "COOL", "heat": "HEAT", "dry": "AIR_DRY", "fan": "FAN", "auto": "AUTO"}
AC_MODES_BACK = {v: k for k, v in AC_MODES.items()}


class LGThinQAdapter(Adapter):
    name = "lg_thinq"

    def __init__(self, token: str, country: str = "BR", client_id: str | None = None, api: Any = None) -> None:
        self._token = token
        self._country = country
        # A LG pede um ID de cliente estável por instalação; derivado do token se não vier.
        self._client_id = client_id or str(uuid.uuid5(uuid.NAMESPACE_URL, f"smarthome:{token[-12:]}"))
        self._api = api
        self._session = None
        self._devices: dict[str, Any] = {}

    async def _get_api(self):
        if self._api is None:
            try:
                import certifi
                from aiohttp import ClientSession, TCPConnector
                from thinqconnect import ThinQApi
            except ImportError as exc:
                raise AdapterError("Instale o extra lg: pip install -e '.[lg]'") from exc
            # O Python do python.org/Homebrew no macOS não usa os certificados do sistema;
            # o certifi (o mesmo que o httpx usa no SmartThings) evita CERTIFICATE_VERIFY_FAILED.
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._session = ClientSession(connector=TCPConnector(ssl=ssl_context))
            self._api = ThinQApi(
                session=self._session,
                access_token=self._token,
                country_code=self._country,
                client_id=self._client_id,
            )
        return self._api

    async def _call(self, coro):
        try:
            return await coro
        except AdapterError:
            raise
        except Exception as exc:  # o SDK levanta ThinQAPIException e erros de rede
            raise AdapterError(f"LG ThinQ: {exc}") from exc

    async def _ac(self, device: Device) -> Any:
        """Objeto AirConditionerDevice do SDK, criado uma vez a partir do perfil do aparelho."""
        external_id = device.config.get("device_id")
        if not external_id:
            raise AdapterError(f"{device.id}: falta config.device_id do LG ThinQ")
        if external_id not in self._devices:
            api = await self._get_api()
            from thinqconnect import AirConditionerDevice

            profile = await self._call(api.async_get_device_profile(external_id))
            self._devices[external_id] = AirConditionerDevice(
                thinq_api=api,
                device_id=external_id,
                device_type=AIR_CONDITIONER,
                model_name=device.config.get("model", ""),
                alias=device.name,
                reportable=True,
                profile=profile,
            )
        return self._devices[external_id]

    async def _refresh(self, device: Device, ac: Any) -> DeviceState:
        api = await self._get_api()
        status = await self._call(api.async_get_device_status(device.config["device_id"]))
        ac.set_status(status)
        mode = ac.get_status("current_job_mode")
        target = (
            ac.get_status("heat_target_temperature_c")
            if mode == "HEAT"
            else ac.get_status("auto_target_temperature_c")
            if mode == "AUTO"
            else ac.get_status("cool_target_temperature_c")
        ) or ac.get_status("target_temperature_c")
        attributes = {
            "mode": AC_MODES_BACK.get(mode, mode),
            "target_temperature": target,
            "temperature": ac.get_status("current_temperature_c"),
        }
        return DeviceState(
            power=ac.get_status("air_con_operation_mode") == "POWER_ON",
            attributes={k: v for k, v in attributes.items() if v is not None},
        )

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        if device.type != DeviceType.AC:
            raise AdapterError(f"Comandos para {device.name} pelo LG ThinQ ainda não são suportados")
        ac = await self._ac(device)
        try:
            return await self._execute_ac(device, ac, command, params)
        except AdapterError as exc:
            if "COMMAND_NOT_SUPPORTED_IN_POWER_OFF" not in str(exc):
                raise
            # A LG recusa qualquer comando com o ar desligado, inclusive "desligar".
            # Se era desligar, está feito; se não, a pessoa precisa ligar antes.
            if command == Command.TURN_OFF:
                return await self._refresh(device, ac)
            raise AdapterError(f"{device.name} está desligado; ligue-o antes de mudar temperatura ou modo") from exc

    async def _execute_ac(self, device: Device, ac: Any, command: Command, params: dict[str, Any]) -> DeviceState:
        if command == Command.TURN_ON:
            await self._call(ac.set_air_con_operation_mode("POWER_ON"))
        elif command == Command.TURN_OFF:
            await self._call(ac.set_air_con_operation_mode("POWER_OFF"))
        elif command == Command.SET_MODE:
            mode = AC_MODES.get(str(params["mode"]))
            if mode is None:
                raise AdapterError(f"Modo {params['mode']} não existe no ar LG")
            await self._call(ac.set_current_job_mode(mode))
        elif command == Command.SET_TEMPERATURE:
            celsius = float(params["celsius"])
            await self._refresh(device, ac)  # o setter certo depende do modo atual
            mode = ac.get_status("current_job_mode")
            setter = (
                ac.set_heat_target_temperature_c
                if mode == "HEAT"
                else ac.set_auto_target_temperature_c
                if mode == "AUTO"
                else ac.set_cool_target_temperature_c
            )
            await self._call(setter(celsius))
        else:
            raise AdapterError(f"Comando {command.value} não suportado no ar LG")
        return await self._refresh(device, ac)

    async def get_state(self, device: Device) -> DeviceState:
        if device.type != DeviceType.AC:
            api = await self._get_api()
            await self._call(api.async_get_device_status(device.config["device_id"]))
            return DeviceState()
        return await self._refresh(device, await self._ac(device))

    async def discover(self) -> list[DiscoveredDevice]:
        api = await self._get_api()
        items = await self._call(api.async_get_device_list()) or []
        found = []
        for item in items:
            info = item.get("deviceInfo") or {}
            if not item.get("deviceId"):
                continue
            device_type = info.get("deviceType")
            found.append(
                DiscoveredDevice(
                    adapter=self.name,
                    external_id=item["deviceId"],
                    name=info.get("alias") or info.get("modelName") or item["deviceId"],
                    suggested_type=DeviceType.AC if device_type == AIR_CONDITIONER else DeviceType.SENSOR,
                    config={"device_id": item["deviceId"], "model": info.get("modelName", "")},
                )
            )
        return found
