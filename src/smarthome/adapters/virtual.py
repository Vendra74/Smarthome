"""Dispositivos simulados em memória: para testar a central sem hardware."""

from __future__ import annotations

from typing import Any

from ..models import Command, Device, DeviceState
from .base import Adapter


class VirtualAdapter(Adapter):
    name = "virtual"

    def __init__(self) -> None:
        self._states: dict[str, DeviceState] = {}

    def _state(self, device: Device) -> DeviceState:
        return self._states.setdefault(device.id, DeviceState(power=False))

    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        state = self._state(device)
        if command == Command.TURN_ON:
            state.power = True
        elif command == Command.TURN_OFF:
            state.power = False
        elif command == Command.SET_BRIGHTNESS:
            state.power = True
            state.attributes["brightness"] = int(params["level"])
        elif command == Command.SET_TEMPERATURE:
            state.attributes["target_temperature"] = float(params["celsius"])
        elif command == Command.SET_MODE:
            state.attributes["mode"] = params["mode"]
        elif command == Command.SET_FAN_SPEED:
            state.attributes["fan_speed"] = params["speed"]
        elif command == Command.SET_VOLUME:
            state.attributes["volume"] = int(params["level"])
        elif command == Command.MUTE:
            state.attributes["muted"] = bool(params.get("muted", True))
        elif command == Command.SET_CHANNEL:
            state.attributes["channel"] = str(params["channel"])
        elif command == Command.OPEN_APP:
            state.attributes["app"] = params["app"]
        return state.model_copy(deep=True)

    async def get_state(self, device: Device) -> DeviceState:
        return self._state(device).model_copy(deep=True)
