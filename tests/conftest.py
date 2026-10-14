import pytest

from smarthome.adapters.virtual import VirtualAdapter
from smarthome.hub import Hub
from smarthome.models import Device, DeviceType
from smarthome.registry import DeviceRegistry


@pytest.fixture
def hub(tmp_path):
    registry = DeviceRegistry(tmp_path / "devices.yaml")
    registry.add(Device(id="luz-sala", name="Luz da sala", room="sala", type=DeviceType.LIGHT, adapter="virtual"))
    registry.add(Device(id="ar-quarto", name="Ar do quarto", room="quarto", type=DeviceType.AC, adapter="virtual"))
    return Hub(registry, [VirtualAdapter()])
