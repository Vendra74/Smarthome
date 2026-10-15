import asyncio
from types import SimpleNamespace

import pytest

from smarthome.adapters.base import AdapterError
from smarthome.adapters.tuya_sharing import TuyaSharingAdapter
from smarthome.models import Command, Device, DeviceType

ACCOUNT = {"user_code": "u", "terminal_id": "t", "endpoint": "e", "token_response": {"access_token": "a"}}


class FakeManager:
    def __init__(self):
        self.sent = []
        self.refreshes = 0
        self.device_map = {}
        self._cloud = {
            "lamp": SimpleNamespace(
                id="lamp", name="Luz do quarto", product_name="Bulb", category="dj", online=True,
                status={"switch_led": False, "bright_value_v2": 505}, function={"switch_led": {}, "bright_value_v2": {}},
            ),
            "plug": SimpleNamespace(
                id="plug", name="Tomada", product_name="Plug", category="cz", online=False,
                status={"switch_1": True}, function={"switch_1": {}},
            ),
        }

    def update_device_cache(self):
        self.refreshes += 1
        self.device_map = dict(self._cloud)

    def send_commands(self, device_id, commands):
        self.sent.append((device_id, commands))


def device(did, dtype=DeviceType.LIGHT):
    return Device(id=did, name=did, type=dtype, adapter="tuya", config={"device_id": did})


def test_discover_and_state():
    manager = FakeManager()
    adapter = TuyaSharingAdapter(ACCOUNT, manager=manager)
    found = {d.external_id: d for d in asyncio.run(adapter.discover())}
    assert found["lamp"].suggested_type == DeviceType.LIGHT and found["plug"].suggested_type == DeviceType.SWITCH
    state = asyncio.run(adapter.get_state(device("lamp")))
    assert state.power is False and state.attributes["brightness"] == 50
    assert asyncio.run(adapter.get_state(device("plug", DeviceType.SWITCH))).online is False
    assert manager.refreshes == 1  # a leitura seguinte usou o cache


def test_execute_turns_on_before_brightness():
    manager = FakeManager()
    adapter = TuyaSharingAdapter(ACCOUNT, manager=manager)
    state = asyncio.run(adapter.execute(device("lamp"), Command.SET_BRIGHTNESS, {"level": 100}))
    assert manager.sent == [("lamp", [{"code": "switch_led", "value": True}, {"code": "bright_value_v2", "value": 1000}])]
    assert state.power is True and state.attributes["brightness"] == 100
    asyncio.run(adapter.execute(device("plug", DeviceType.SWITCH), Command.TURN_OFF, {}))
    assert manager.sent[-1] == ("plug", [{"code": "switch_1", "value": False}])


def test_errors():
    adapter = TuyaSharingAdapter(ACCOUNT, manager=FakeManager())
    with pytest.raises(AdapterError, match="não está mais"):
        asyncio.run(adapter.get_state(device("sumiu")))
    with pytest.raises(AdapterError, match="brilho"):
        asyncio.run(adapter.execute(device("plug"), Command.SET_BRIGHTNESS, {"level": 10}))
