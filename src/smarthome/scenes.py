"""Cenas: um nome falado ("modo filme") que dispara vários comandos de uma vez.

Ficam em data/homes/<id>/scenes.yaml. Uma cena é só uma lista de ações
(dispositivo + comando + parâmetros); quem executa é o Hub, então vale para
qualquer adaptador e aparece igual no app, na voz e, depois, na Alexa.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .models import Command, CommandResult, DeviceState
from .registry import _normalize


class SceneAction(BaseModel):
    device_id: str
    command: Command
    params: dict[str, Any] = Field(default_factory=dict)


class Scene(BaseModel):
    id: str
    name: str = Field(description="Nome falado, ex.: Modo filme")
    aliases: list[str] = Field(default_factory=list)
    actions: list[SceneAction] = Field(default_factory=list)


class SceneResult(BaseModel):
    scene_id: str
    ok: bool
    results: list[CommandResult] = Field(default_factory=list)

    @property
    def failed(self) -> list[CommandResult]:
        return [r for r in self.results if not r.ok]


# Atributos do estado -> comando que os reproduz (para "salvar como cena" o estado atual)
_ATTRIBUTE_COMMANDS: list[tuple[str, Command, str]] = [
    ("brightness", Command.SET_BRIGHTNESS, "level"),
    ("mode", Command.SET_MODE, "mode"),
    ("target_temperature", Command.SET_TEMPERATURE, "celsius"),
    ("fan_speed", Command.SET_FAN_SPEED, "speed"),
    ("volume", Command.SET_VOLUME, "level"),
    ("app", Command.OPEN_APP, "app"),
]


def actions_from_state(device_id: str, state: DeviceState, capabilities: list[Command]) -> list[SceneAction]:
    """Traduz o estado atual de um aparelho nas ações que o reproduzem."""
    if state.power is False or (state.power is None and not state.online):
        return [SceneAction(device_id=device_id, command=Command.TURN_OFF)] if Command.TURN_OFF in capabilities else []
    actions = [SceneAction(device_id=device_id, command=Command.TURN_ON)] if Command.TURN_ON in capabilities else []
    for attribute, command, param in _ATTRIBUTE_COMMANDS:
        value = state.attributes.get(attribute)
        if value is not None and command in capabilities:
            actions.append(SceneAction(device_id=device_id, command=command, params={param: value}))
    if state.attributes.get("muted") and Command.MUTE in capabilities:
        actions.append(SceneAction(device_id=device_id, command=Command.MUTE, params={"muted": True}))
    return actions


class SceneStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._scenes: dict[str, Scene] = {}
        self._lock = threading.Lock()
        if path and path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in data.get("scenes", []):
                scene = Scene.model_validate(raw)
                self._scenes[scene.id] = scene

    def all(self) -> list[Scene]:
        return list(self._scenes.values())

    def get(self, scene_id: str) -> Scene | None:
        return self._scenes.get(scene_id)

    def find_by_name(self, name: str) -> list[Scene]:
        target = _normalize(name)
        exact = [
            s for s in self._scenes.values()
            if target in {_normalize(s.name), s.id, *(_normalize(a) for a in s.aliases)}
        ]
        return exact or [s for s in self._scenes.values() if target in _normalize(s.name)]

    def add(self, scene: Scene) -> Scene:
        with self._lock:
            if scene.id in self._scenes:
                raise ValueError(f"Já existe uma cena com id '{scene.id}'")
            self._scenes[scene.id] = scene
            self._save()
        return scene

    def update(self, scene: Scene) -> Scene:
        with self._lock:
            if scene.id not in self._scenes:
                raise ValueError(f"Cena '{scene.id}' não existe")
            self._scenes[scene.id] = scene
            self._save()
        return scene

    def remove(self, scene_id: str) -> bool:
        with self._lock:
            existed = self._scenes.pop(scene_id, None) is not None
            self._save()
        return existed

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"scenes": [s.model_dump(mode="json") for s in self._scenes.values()]}
        self._path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
