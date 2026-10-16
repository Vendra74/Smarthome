import asyncio
import json

import httpx

from smarthome.adapters.smartthings import SmartThingsAdapter
from smarthome.hub import Hub
from smarthome.models import Command, Device, DeviceType
from smarthome.registry import DeviceRegistry


def make_transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/status"):
            return httpx.Response(
                200,
                json={"components": {"main": {"switch": {"switch": {"value": "on"}}, "switchLevel": {"level": {"value": 40}}}}},
            )
        if request.url.path == "/v1/devices":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"deviceId": "abc", "label": "Luz cozinha", "components": [{"capabilities": [{"id": "switch"}, {"id": "switchLevel"}]}]},
                        {"deviceId": "ar1", "label": "Ar sala", "components": [{"capabilities": [{"id": "switch"}, {"id": "airConditionerMode"}]}]},
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    return httpx.MockTransport(handler)


def test_command_is_translated_and_state_read():
    calls = []
    adapter = SmartThingsAdapter("token", transport=make_transport(calls))
    device = Device(id="l", name="L", type=DeviceType.LIGHT, adapter="smartthings", config={"device_id": "abc"})
    state = asyncio.run(adapter.execute(device, Command.SET_BRIGHTNESS, {"level": 40}))
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/v1/devices/abc/commands")
    assert json.loads(body)["commands"][0] == {
        "component": "main",
        "capability": "switchLevel",
        "command": "setLevel",
        "arguments": [40],
    }
    assert state.power is True and state.attributes["brightness"] == 40


def test_discover_skips_already_registered(tmp_path):
    adapter = SmartThingsAdapter("token", transport=make_transport([]))
    registry = DeviceRegistry(tmp_path / "d.yaml")
    registry.add(Device(id="luz", name="Luz", type=DeviceType.LIGHT, adapter="smartthings", config={"device_id": "abc"}))
    found = asyncio.run(Hub(registry, [adapter]).discover())
    assert [(d.external_id, d.suggested_type) for d in found] == [("ar1", DeviceType.AC)]


def make_offline_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"deviceId": "tv", "state": "OFFLINE"})
        if request.url.path.endswith("/commands"):
            return httpx.Response(
                409,
                json={"error": {"code": "ConstraintViolationError", "message": "The request is malformed.",
                                "details": [{"code": "DeviceOfflineError", "message": "Device is offline"}]}},
            )
        return httpx.Response(200, json={"components": {"main": {"switch": {"switch": {"value": "on"}}}}})

    return httpx.MockTransport(handler)


def test_offline_tv_gets_a_clear_message_and_shows_as_off():
    import pytest

    from smarthome.adapters.base import AdapterError

    adapter = SmartThingsAdapter("token", transport=make_offline_transport())
    tv = Device(id="tv-sala", name="TV da sala", type=DeviceType.TV, adapter="smartthings", config={"device_id": "tv"})

    state = asyncio.run(adapter.get_state(tv))
    assert state.online is False and state.power is False  # o "on" guardado é velho

    with pytest.raises(AdapterError, match="Ligar com dispositivo móvel"):
        asyncio.run(adapter.execute(tv, Command.TURN_OFF, {}))


def test_other_errors_keep_the_smartthings_reason():
    import pytest

    from smarthome.adapters.base import AdapterError

    def handler(request):
        return httpx.Response(422, json={"error": {"code": "ConstraintViolationError", "message": "unsupported command"}})

    adapter = SmartThingsAdapter("token", transport=httpx.MockTransport(handler))
    tv = Device(id="tv", name="TV", type=DeviceType.TV, adapter="smartthings", config={"device_id": "tv"})
    with pytest.raises(AdapterError, match=r"422 .*unsupported command"):
        asyncio.run(adapter.execute(tv, Command.TURN_OFF, {}))


def test_tv_409_with_health_online_still_gets_a_clear_message():
    """TV Samsung em standby: a saúde ainda diz ONLINE, mas o comando volta 409."""
    import pytest

    from smarthome.adapters.base import AdapterError

    def handler(request):
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"deviceId": "tv", "state": "ONLINE"})
        if request.url.path.endswith("/commands"):
            return httpx.Response(409, json={"error": {"code": "ConstraintViolationError",
                                                       "message": "The request is malformed.",
                                                       "details": [{"message": "Device is offline"}]}})
        return httpx.Response(200, json={"components": {"main": {"switch": {"switch": {"value": "on"}}}}})

    adapter = SmartThingsAdapter("token", transport=httpx.MockTransport(handler))
    tv = Device(id="tv", name="TV da sala", type=DeviceType.TV, adapter="smartthings", config={"device_id": "tv"})
    with pytest.raises(AdapterError, match=r"TV da sala não respondeu.*Device is offline.*Ligar com dispositivo móvel"):
        asyncio.run(adapter.execute(tv, Command.TURN_OFF, {}))


def test_state_is_cached_and_survives_rate_limit():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"state": "ONLINE"})
        if len([c for c in calls if c.endswith("/status")]) > 1:
            return httpx.Response(429, json={"error": {"code": "TooManyRequestError", "message": "Too Many Requests"}})
        return httpx.Response(200, json={"components": {"main": {"switch": {"switch": {"value": "on"}}}}})

    adapter = SmartThingsAdapter("token", transport=httpx.MockTransport(handler))
    tv = Device(id="tv", name="TV", type=DeviceType.TV, adapter="smartthings", config={"device_id": "tv"})

    first = asyncio.run(adapter.get_state(tv))
    second = asyncio.run(adapter.get_state(tv))  # dentro do TTL: nenhuma chamada nova
    assert first.power is True and second.power is True
    assert calls == ["/v1/devices/tv/status", "/v1/devices/tv/health"]

    adapter._state_cache["tv"] = (0.0, first)  # força o TTL a vencer
    third = asyncio.run(adapter.get_state(tv))  # a SmartThings devolve 429: vale o último estado
    assert third.power is True and calls[-1] == "/v1/devices/tv/status"


def test_fan_speed_uses_fan_mode_capability():
    calls = []
    adapter = SmartThingsAdapter("token", transport=make_transport(calls))
    device = Device(id="a", name="Ar", type=DeviceType.AC, adapter="smartthings", config={"device_id": "ar1"})
    asyncio.run(adapter.execute(device, Command.SET_FAN_SPEED, {"speed": "high"}))
    assert json.loads(calls[0][2])["commands"][0] == {
        "component": "main",
        "capability": "airConditionerFanMode",
        "command": "setFanMode",
        "arguments": ["high"],
    }
