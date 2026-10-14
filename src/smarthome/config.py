"""Monta a central a partir de variáveis de ambiente."""

from __future__ import annotations

import os
from pathlib import Path

from .adapters.base import Adapter
from .adapters.lg_tv import LGTVAdapter
from .adapters.samsung_tv import SamsungTVAdapter
from .adapters.smartthings import SmartThingsAdapter
from .adapters.virtual import VirtualAdapter
from .hub import Hub
from .registry import DeviceRegistry


def build_hub() -> Hub:
    path = Path(os.environ.get("SMARTHOME_DEVICES", "config/devices.yaml"))
    adapters: list[Adapter] = [VirtualAdapter(), SamsungTVAdapter(), LGTVAdapter()]
    if token := os.environ.get("SMARTTHINGS_TOKEN"):
        adapters.append(SmartThingsAdapter(token))
    return Hub(DeviceRegistry(path), adapters)
