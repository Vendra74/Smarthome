import asyncio

from smarthome.models import Command, DeviceType
from smarthome.registry import DeviceRegistry


def run(coro):
    return asyncio.run(coro)


def test_turn_on_light(hub):
    result = run(hub.execute("luz-sala", Command.TURN_ON))
    assert result.ok and result.state.power is True


def test_rejects_unsupported_command(hub):
    result = run(hub.execute("luz-sala", Command.SET_TEMPERATURE, {"celsius": 22}))
    assert not result.ok
    assert "não aceita" in result.message


def test_unknown_device(hub):
    assert not run(hub.execute("nao-existe", Command.TURN_ON)).ok


def test_missing_param_is_reported_not_raised(hub):
    result = run(hub.execute("ar-quarto", Command.SET_TEMPERATURE, {}))
    assert not result.ok


def test_register_persists_and_slugifies(hub, tmp_path):
    device = hub.register("TV", DeviceType.TV, "virtual", room="Sala de Estar")
    assert device.id == "tv-sala-de-estar"
    again = hub.register("TV", DeviceType.TV, "virtual", room="Sala de Estar")
    assert again.id == "tv-sala-de-estar-2"
    reloaded = DeviceRegistry(tmp_path / "devices.yaml")
    assert reloaded.get("tv-sala-de-estar").type == DeviceType.TV


def test_find_by_name_ignores_accents(hub):
    hub.register("Luz da Cozinha", DeviceType.LIGHT, "virtual", aliases=["luz da copa"])
    assert [d.id for d in hub.registry.find_by_name("luz da cozinha")] == ["luz-da-cozinha"]
    assert hub.registry.find_by_name("LUZ DA CÓPA")[0].id == "luz-da-cozinha"
    assert [d.id for d in hub.registry.in_room("Sála")] == ["luz-sala"]
