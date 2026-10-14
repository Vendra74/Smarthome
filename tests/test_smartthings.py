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
