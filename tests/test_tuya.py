"""Adaptador Tuya (Intelbras Izy Smart) com uma nuvem falsa."""

import asyncio

import pytest

from smarthome.adapters.base import AdapterError
from smarthome.adapters.tuya import TuyaAdapter
from smarthome.models import Command, Device, DeviceType


class FakeCloud:
    def __init__(self):
        self.status = {"lamp": {"switch_led": False, "bright_value_v2": 505}, "plug": {"switch_1": True}}
        self.sent = []

    def getdevices(self):
        return [
            {"id": "lamp", "name": "Luz do corredor", "category": "dj", "key": "k1"},
            {"id": "plug", "name": "Tomada da sala", "category": "cz", "key": "k2"},
            {"id": "sens", "name": "Sensor porta", "category": "mcs", "key": "k3"},
        ]

    def getstatus(self, device_id):
        return {"success": True, "result": [{"code": c, "value": v} for c, v in self.status[device_id].items()]}

    def sendcommand(self, device_id, commands):
        self.sent.append((device_id, commands))
        for c in commands:
            self.status[device_id][c["code"]] = c["value"]
        return {"success": True}


def make():
    cloud = FakeCloud()
    return TuyaAdapter("id", "secret", cloud=cloud), cloud


def test_discover_maps_categories():
    adapter, _ = make()
    found = asyncio.run(adapter.discover())
    assert [(d.external_id, d.suggested_type) for d in found] == [
        ("lamp", DeviceType.LIGHT), ("plug", DeviceType.SWITCH), ("sens", DeviceType.SENSOR)]


def test_turn_on_and_brightness_scale():
    adapter, cloud = make()
    lamp = Device(id="luz", name="Luz", type=DeviceType.LIGHT, adapter="tuya", config={"device_id": "lamp"})

    state = asyncio.run(adapter.get_state(lamp))
    assert state.power is False and state.attributes["brightness"] == 50

    state = asyncio.run(adapter.execute(lamp, Command.SET_BRIGHTNESS, {"level": 100}))
    # apagada: liga antes de ajustar o brilho; 100% vira o topo da faixa v2 (1000)
    assert cloud.sent[-1] == ("lamp", [{"code": "switch_led", "value": True}, {"code": "bright_value_v2", "value": 1000}])
    assert state.power is True and state.attributes["brightness"] == 100

    state = asyncio.run(adapter.execute(lamp, Command.TURN_OFF, {}))
    assert cloud.sent[-1] == ("lamp", [{"code": "switch_led", "value": False}]) and state.power is False


def test_plug_uses_switch_1_and_rejects_brightness():
    adapter, cloud = make()
    plug = Device(id="tomada", name="Tomada", type=DeviceType.SWITCH, adapter="tuya", config={"device_id": "plug"})
    state = asyncio.run(adapter.execute(plug, Command.TURN_OFF, {}))
    assert cloud.sent == [("plug", [{"code": "switch_1", "value": False}])] and state.power is False
    with pytest.raises(AdapterError, match="não controla brilho"):
        asyncio.run(adapter.execute(plug, Command.SET_BRIGHTNESS, {"level": 10}))


def test_cloud_errors_become_adapter_errors():
    class Broken(FakeCloud):
        def getstatus(self, device_id):
            return {"success": False, "msg": "token invalid"}

    adapter = TuyaAdapter("id", "secret", cloud=Broken())
    lamp = Device(id="luz", name="Luz", type=DeviceType.LIGHT, adapter="tuya", config={"device_id": "lamp"})
    with pytest.raises(AdapterError, match="token invalid"):
        asyncio.run(adapter.get_state(lamp))
