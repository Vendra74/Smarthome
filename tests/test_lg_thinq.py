import asyncio

from smarthome.adapters.lg_thinq import LGThinQAdapter
from smarthome.models import Command, Device, DeviceType


class FakeApi:
    def __init__(self):
        self.status = {"mode": "COOL", "power": "POWER_OFF", "cool": 24.0, "current": 27.0}

    async def async_get_device_list(self):
        return [
            {"deviceId": "ac1", "deviceInfo": {"deviceType": "DEVICE_AIR_CONDITIONER", "alias": "Ar da sala", "modelName": "S4"}},
            {"deviceId": "w1", "deviceInfo": {"deviceType": "DEVICE_WASHER", "alias": "Lavadora"}},
            {"deviceInfo": {"alias": "sem id"}},
        ]

    async def async_get_device_status(self, device_id):
        return dict(self.status)


class FakeAC:
    """Imita o AirConditionerDevice do SDK: setters assíncronos e get_status."""

    def __init__(self, api):
        self.api = api
        self.calls = []

    def set_status(self, status):
        self._s = status

    def get_status(self, prop):
        return {
            "current_job_mode": self._s["mode"],
            "air_con_operation_mode": self._s["power"],
            "cool_target_temperature_c": self._s["cool"],
            "current_temperature_c": self._s["current"],
        }.get(prop)

    async def set_air_con_operation_mode(self, value):
        self.calls.append(("power", value))
        self.api.status["power"] = value

    async def set_current_job_mode(self, value):
        self.calls.append(("mode", value))
        self.api.status["mode"] = value

    async def set_cool_target_temperature_c(self, value):
        self.calls.append(("cool", value))
        self.api.status["cool"] = value

    async def set_heat_target_temperature_c(self, value):
        self.calls.append(("heat", value))


def make():
    api = FakeApi()
    adapter = LGThinQAdapter("token", api=api)
    ac = FakeAC(api)
    adapter._devices["ac1"] = ac
    device = Device(id="ar-sala", name="Ar da sala", type=DeviceType.AC, adapter="lg_thinq", config={"device_id": "ac1"})
    return adapter, ac, device


def test_discover_maps_types_and_skips_items_without_id():
    adapter, _, _ = make()
    found = asyncio.run(adapter.discover())
    assert [(d.external_id, d.name, d.suggested_type) for d in found] == [
        ("ac1", "Ar da sala", DeviceType.AC),
        ("w1", "Lavadora", DeviceType.SENSOR),
    ]


def test_ac_power_mode_and_temperature():
    adapter, ac, device = make()
    state = asyncio.run(adapter.execute(device, Command.TURN_ON, {}))
    assert state.power is True and state.attributes == {"mode": "cool", "target_temperature": 24.0, "temperature": 27.0}

    asyncio.run(adapter.execute(device, Command.SET_TEMPERATURE, {"celsius": 22}))
    asyncio.run(adapter.execute(device, Command.SET_MODE, {"mode": "dry"}))
    assert ac.calls == [("power", "POWER_ON"), ("cool", 22.0), ("mode", "AIR_DRY")]


def test_temperature_uses_heat_setter_in_heat_mode():
    adapter, ac, device = make()
    adapter._api.status["mode"] = "HEAT"
    asyncio.run(adapter.execute(device, Command.SET_TEMPERATURE, {"celsius": 26}))
    assert ac.calls == [("heat", 26.0)]


def test_non_ac_commands_are_refused_clearly():
    import pytest

    from smarthome.adapters.base import AdapterError

    adapter, _, _ = make()
    washer = Device(id="lav", name="Lavadora", type=DeviceType.SENSOR, adapter="lg_thinq", config={"device_id": "w1"})
    with pytest.raises(AdapterError, match="ainda não são suportados"):
        asyncio.run(adapter.execute(washer, Command.TURN_ON, {}))


def test_turning_off_an_ac_already_off_is_a_success():
    import asyncio

    import pytest

    from smarthome.adapters.base import AdapterError

    adapter, ac, device = make()
    ac.api.status["power"] = "POWER_OFF"

    async def refuse(value):
        raise Exception("ThinQAPIException: COMMAND_NOT_SUPPORTED_IN_POWER_OFF (2304) - Command not supported in POWER OFF")

    ac.set_air_con_operation_mode = refuse
    ac.set_cool_target_temperature_c = refuse
    state = asyncio.run(adapter.execute(device, Command.TURN_OFF, {}))
    assert state.power is False
    with pytest.raises(AdapterError, match="ligue-o antes"):
        asyncio.run(adapter.execute(device, Command.SET_TEMPERATURE, {"celsius": 22}))


def test_turning_on_an_ac_already_on_is_a_success_even_if_status_lags():
    import asyncio

    adapter, ac, device = make()
    ac.api.status["power"] = "POWER_OFF"  # a nuvem ainda diz desligado

    async def refuse(value):
        raise Exception("ThinQAPIException: COMMAND_NOT_SUPPORTED_IN_STATE (2302) - Command not supported in POWER_ON")

    ac.set_air_con_operation_mode = refuse
    state = asyncio.run(adapter.execute(device, Command.TURN_ON, {}))
    assert state.power is True
