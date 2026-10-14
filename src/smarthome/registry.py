"""Cadastro de dispositivos, persistido em YAML."""

from __future__ import annotations

import threading
import unicodedata
from pathlib import Path

import yaml

from .models import Device


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


class DeviceRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._devices: dict[str, Device] = {}
        self._lock = threading.Lock()
        if path and path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in data.get("devices", []):
                device = Device.model_validate(raw)
                self._devices[device.id] = device

    def all(self) -> list[Device]:
        return list(self._devices.values())

    def get(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    def in_room(self, room: str) -> list[Device]:
        target = _normalize(room)
        return [d for d in self._devices.values() if d.room and _normalize(d.room) == target]

    def find_by_name(self, name: str) -> list[Device]:
        """Busca por nome ou apelido, ignorando acentos e maiúsculas."""
        target = _normalize(name)
        exact = [
            d
            for d in self._devices.values()
            if target in {_normalize(d.name), d.id, *(_normalize(a) for a in d.aliases)}
        ]
        if exact:
            return exact
        return [d for d in self._devices.values() if target in _normalize(d.name)]

    def add(self, device: Device) -> Device:
        with self._lock:
            if device.id in self._devices:
                raise ValueError(f"Já existe um dispositivo com id '{device.id}'")
            self._devices[device.id] = device
            self._save()
        return device

    def remove(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(device_id, None)
            self._save()

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"devices": [d.model_dump(mode="json") for d in self._devices.values()]}
        self._path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
