"""Interface que todo adaptador de fabricante/plataforma implementa."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import Command, Device, DeviceState, DiscoveredDevice


class AdapterError(Exception):
    """Falha ao falar com o dispositivo ou a plataforma."""


class Adapter(ABC):
    name: str

    @abstractmethod
    async def execute(self, device: Device, command: Command, params: dict[str, Any]) -> DeviceState:
        """Executa o comando e devolve o estado conhecido depois dele."""

    @abstractmethod
    async def get_state(self, device: Device) -> DeviceState: ...

    async def discover(self) -> list[DiscoveredDevice]:
        """Lista dispositivos que a plataforma conhece. Nem todo adaptador sabe descobrir."""
        return []
