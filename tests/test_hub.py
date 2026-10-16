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


def test_discover_merges_lan_tv_into_cloud_tv_and_skips_registered(hub):
    import asyncio

    from smarthome.adapters.base import Adapter
    from smarthome.models import DiscoveredDevice

    class Cloud(Adapter):
        async def execute(self, device, command, params):
            raise NotImplementedError

        async def get_state(self, device):
            raise NotImplementedError

        name = "smartthings"

        async def discover(self):
            return [DiscoveredDevice(adapter="smartthings", external_id="st-85", name="85\" Crystal UHD",
                                     suggested_type=DeviceType.TV, config={"device_id": "st-85"})]

    class Lan(Adapter):
        async def execute(self, device, command, params):
            raise NotImplementedError

        async def get_state(self, device):
            raise NotImplementedError

        name = "samsung_tv"

        async def discover(self):
            return [
                DiscoveredDevice(adapter="samsung_tv", external_id="10.0.0.19", name="[TV] 85\" Crystal UHD",
                                 suggested_type=DeviceType.TV, config={"host": "10.0.0.19", "mac": "4C:57:39:8C:3B:D8"}),
                DiscoveredDevice(adapter="samsung_tv", external_id="10.0.0.18", name="[TV] Samsung 7 Series (75)",
                                 suggested_type=DeviceType.TV, config={"host": "10.0.0.18", "mac": "00:7C:2D:6B:35:0B"}),
                DiscoveredDevice(adapter="samsung_tv", external_id="10.0.0.30", name="TV do quarto",
                                 suggested_type=DeviceType.TV, config={"host": "10.0.0.30"}),
            ]

    hub.set_adapter(Cloud())
    hub.set_adapter(Lan())
    hub.register(name="TV 75", type=DeviceType.TV, adapter="smartthings",
                 config={"device_id": "st-75", "host": "10.0.0.18", "mac": "00:7C:2D:6B:35:0B"})
    found = asyncio.run(hub.discover())
    assert [(d.adapter, d.name) for d in found] == [("smartthings", "85\" Crystal UHD"), ("samsung_tv", "TV do quarto")]
    # a TV da nuvem já vem com IP e MAC da rede, para a recuperação local
    assert found[0].config == {"device_id": "st-85", "host": "10.0.0.19", "mac": "4C:57:39:8C:3B:D8"}
