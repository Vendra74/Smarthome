"""Modelo de domínio: dispositivos, comandos e estado.

Todo dispositivo, venha de onde vier (SmartThings, TV LG, TV Samsung, virtual),
é descrito do mesmo jeito. Quem fala com o hardware é o adaptador; o resto da
central (API, agente de voz, Alexa) só enxerga este modelo.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DeviceType(str, Enum):
    LIGHT = "light"
    TV = "tv"
    AC = "ac"
    SWITCH = "switch"
    SENSOR = "sensor"
    OTHER = "other"


class Command(str, Enum):
    """Vocabulário único de comandos. Cada adaptador traduz para o protocolo dele."""

    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    SET_BRIGHTNESS = "set_brightness"  # params: level 0-100
    SET_TEMPERATURE = "set_temperature"  # params: celsius
    SET_MODE = "set_mode"  # params: mode (cool, heat, dry, fan, auto)
    SET_FAN_SPEED = "set_fan_speed"  # params: speed (low, medium, high, auto)
    SET_VOLUME = "set_volume"  # params: level 0-100
    MUTE = "mute"  # params: muted bool
    SET_CHANNEL = "set_channel"  # params: channel
    OPEN_APP = "open_app"  # params: app (ex.: netflix, youtube)


# Comandos que cada tipo aceita por padrão; um dispositivo pode restringir mais.
DEFAULT_CAPABILITIES: dict[DeviceType, list[Command]] = {
    DeviceType.LIGHT: [Command.TURN_ON, Command.TURN_OFF, Command.SET_BRIGHTNESS],
    DeviceType.TV: [
        Command.TURN_ON,
        Command.TURN_OFF,
        Command.SET_VOLUME,
        Command.MUTE,
        Command.SET_CHANNEL,
        Command.OPEN_APP,
    ],
    DeviceType.AC: [
        Command.TURN_ON,
        Command.TURN_OFF,
        Command.SET_TEMPERATURE,
        Command.SET_MODE,
        Command.SET_FAN_SPEED,
    ],
    DeviceType.SWITCH: [Command.TURN_ON, Command.TURN_OFF],
    DeviceType.SENSOR: [],
    DeviceType.OTHER: [Command.TURN_ON, Command.TURN_OFF],
}


class Device(BaseModel):
    id: str = Field(description="Identificador estável, ex.: luz-sala")
    name: str = Field(description="Nome falado, ex.: Luz da sala")
    room: str | None = None
    type: DeviceType
    adapter: str = Field(description="Nome do adaptador: virtual, smartthings, samsung_tv, lg_tv")
    capabilities: list[Command] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list, description="Outros nomes falados")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Dados do adaptador: id externo, IP, MAC etc.",
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.capabilities:
            self.capabilities = list(DEFAULT_CAPABILITIES[self.type])

    def supports(self, command: Command) -> bool:
        return command in self.capabilities


class DeviceState(BaseModel):
    online: bool = True
    power: bool | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CommandRequest(BaseModel):
    command: Command
    params: dict[str, Any] = Field(default_factory=dict)


class CommandResult(BaseModel):
    device_id: str
    ok: bool
    message: str = ""
    state: DeviceState | None = None


class DiscoveredDevice(BaseModel):
    """Dispositivo encontrado num adaptador e ainda não cadastrado na central."""

    adapter: str
    external_id: str
    name: str
    suggested_type: DeviceType
    config: dict[str, Any] = Field(default_factory=dict)
