"""Recuperação de TV Samsung pela rede local quando a SmartThings a dá como offline."""

import asyncio

import pytest

from smarthome.adapters.base import Adapter, AdapterError, DeviceUnreachable
from smarthome.hub import Hub
from smarthome.models import Command, Device, DeviceState, DeviceType
from smarthome.recovery import TVRecovery
from smarthome.registry import DeviceRegistry

TV_NAME = "[TV] Samsung 6 Series (55)"


class FlakyCloud(Adapter):
    """Finge a SmartThings: recusa até a TV 'acordar'."""

    name = "smartthings"

    def __init__(self, fails: int) -> None:
        self.fails, self.calls = fails, []

    async def execute(self, device, command, params):
        self.calls.append(command)
        if len(self.calls) <= self.fails:
            raise DeviceUnreachable(f"{device.name} não respondeu à SmartThings")
        return DeviceState(power=command != Command.TURN_OFF)

    async def get_state(self, device):
        return DeviceState(online=False, power=False)


class LocalTV(Adapter):
    name = "samsung_tv"

    def __init__(self) -> None:
        self.calls = []

    async def execute(self, device, command, params):
        self.calls.append((device.config.get("host"), command))
        return DeviceState(power=command != Command.TURN_OFF)

    async def get_state(self, device):
        return DeviceState()


def make_hub(tmp_path, cloud, lan=(), info=None, local=None, config=None):
    registry = DeviceRegistry(tmp_path / "devices.yaml")
    registry.add(Device(id="tv-sala", name=TV_NAME, room="sala", type=DeviceType.TV, adapter="smartthings",
                        config={"device_id": "st-1", **(config or {})}))
    woken = []

    async def scan():
        return list(lan)

    async def tv_info(host):
        return info(host) if info else None

    async def no_sleep(_seconds):
        pass

    def wake(mac, host=None):
        woken.append(mac)

    recovery = TVRecovery(scan=scan, info=tv_info, wake=wake, sleep=no_sleep, timeout=9, interval=3)
    hub = Hub(registry, [cloud, *([local] if local else [])], recovery=recovery)
    return hub, woken


def test_standby_tv_is_woken_and_command_retried(tmp_path):
    cloud = FlakyCloud(fails=2)
    lan = [{"host": "192.168.0.50", "name": TV_NAME, "model": "UN55", "mac": "AA:BB:CC:DD:EE:FF", "power": False}]
    hub, woken = make_hub(tmp_path, cloud, lan=lan)

    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))

    assert result.ok and result.state.power is True
    assert "acordei" in result.message
    assert woken == ["AA:BB:CC:DD:EE:FF"] * 3  # sem MAC guardado antes, só os 3 da recuperação
    assert cloud.calls == [Command.TURN_ON] * 3  # a original + 2 tentativas após o Wake-on-LAN
    saved = DeviceRegistry(tmp_path / "devices.yaml").get("tv-sala").config
    assert saved["host"] == "192.168.0.50" and saved["mac"] == "AA:BB:CC:DD:EE:FF"  # guardado para a próxima


def test_turn_off_an_unreachable_tv_reports_already_off(tmp_path):
    hub, woken = make_hub(tmp_path, FlakyCloud(fails=99))
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_OFF))
    assert result.ok and result.state.power is False and "já estava desligada" in result.message
    assert woken == []


def test_gives_up_with_a_clear_message_when_tv_never_wakes(tmp_path):
    hub, woken = make_hub(tmp_path, FlakyCloud(fails=99), config={"mac": "AA:BB:CC:DD:EE:FF"})
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert not result.ok and "não respondeu em 9 s" in result.message and "Ligar com dispositivo móvel" in result.message
    assert len(woken) == 4  # 1 preventivo no "ligar" + 3 da recuperação


def test_without_mac_explains_how_to_teach_the_hub(tmp_path):
    hub, _ = make_hub(tmp_path, FlakyCloud(fails=99))
    result = asyncio.run(hub.execute("tv-sala", Command.OPEN_APP, {"app": "netflix"}))
    assert not result.ok and "não achei o MAC" in result.message


def test_tv_on_in_lan_but_invisible_to_cloud_uses_local_control(tmp_path):
    local = LocalTV()
    info = lambda host: {"host": host, "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:FF", "power": True}
    hub, woken = make_hub(tmp_path, FlakyCloud(fails=99), info=info, local=local, config={"host": "192.168.0.50"})

    result = asyncio.run(hub.execute("tv-sala", Command.TURN_OFF))
    assert result.ok and result.state.power is False and "rede local" in result.message
    assert local.calls == [("192.168.0.50", Command.TURN_OFF)] and woken == []

    state = asyncio.run(hub.get_state("tv-sala"))
    assert state.online is True and state.power is True  # a nuvem dizia offline; a rede local corrige


def test_other_errors_are_not_recovered(tmp_path):
    class Broken(Adapter):
        name = "smartthings"

        async def execute(self, device, command, params):
            raise AdapterError("SmartThings: 401 Unauthorized")

        async def get_state(self, device):
            return DeviceState()

    hub, woken = make_hub(tmp_path, Broken())
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert not result.ok and "401" in result.message and woken == []


def test_same_tv_name_matching():
    from smarthome.adapters.samsung_lan import same_tv

    assert same_tv("[TV] Samsung 6 Series (55)", "[TV]Samsung 6 Series (55)")
    assert same_tv("TV Samsung 6 Series (55)", "[TV] Samsung 6 Series (55)")
    assert not same_tv("[TV] Samsung 6 Series (55)", "[TV] Samsung 7 Series (65)")
    assert not same_tv("", "")


def test_state_scans_lan_when_tv_has_no_host_yet(tmp_path):
    lan = [{"host": "192.168.0.50", "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:FF", "power": True}]
    hub, _ = make_hub(tmp_path, FlakyCloud(fails=99), lan=lan)

    async def run():
        first = await hub.get_state("tv-sala")  # dispara a varredura em segundo plano
        await hub.recovery._scan_task
        second = await hub.get_state("tv-sala")  # usa o resultado pronto
        return first, second

    first, state = asyncio.run(run())
    assert first.online is False  # a tela não espera a rede ser varrida
    assert state.online is True and state.power is True and state.attributes["via"] == "rede local"
    assert hub.registry.get("tv-sala").config["host"] == "192.168.0.50"


def test_ssdp_failure_does_not_crash_the_scan(monkeypatch):
    """No macOS sem permissão de rede local, sendto dá 'No route to host'."""
    import socket

    from smarthome.adapters import samsung_lan

    def boom(self, *_args, **_kwargs):
        raise OSError(65, "No route to host")

    monkeypatch.setattr(socket.socket, "sendto", boom)
    assert samsung_lan._ssdp_hosts(0.1) == set()


def test_sweep_finds_tv_without_multicast(monkeypatch):
    import ipaddress

    from smarthome.adapters import samsung_lan

    async def fake_info(host, timeout=2.0):
        if host == "10.0.0.20":
            return {"host": host, "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:FF", "power": True}
        return None

    monkeypatch.setattr(samsung_lan, "_ssdp_hosts", lambda timeout: set())
    monkeypatch.setattr(samsung_lan, "tv_info", fake_info)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: ipaddress.ip_network("10.0.0.0/24"))
    found = asyncio.run(samsung_lan.find_samsung_tvs())
    assert [t["host"] for t in found] == ["10.0.0.20"]


def test_macos_block_is_named_in_the_message(monkeypatch, tmp_path):
    """Errno 65 em tudo (multicast, broadcast, varredura) é o bloqueio de 'Rede local' do macOS."""
    import socket

    from smarthome.adapters import samsung_lan

    def boom(self, *_args, **_kwargs):
        raise OSError(65, "No route to host")

    monkeypatch.setattr(socket.socket, "sendto", boom)
    samsung_lan._blocked = False
    samsung_lan._ssdp_hosts(0.1)
    assert samsung_lan.lan_blocked()

    hub, _ = make_hub(tmp_path, FlakyCloud(fails=99), lan=[])
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert not result.ok and "Rede local" in result.message

    samsung_lan._blocked = False
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert "Rede local" not in result.message and "mesma rede" in result.message


def test_tv_awake_on_lan_but_cloud_still_blind_is_reported_as_on(tmp_path):
    cloud = FlakyCloud(fails=99)
    awake = {"host": "10.0.0.18", "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:FF", "power": True}
    answers = iter([None, awake])  # desligada na 1ª consulta (locate), ligada depois do Wake-on-LAN
    hub, _ = make_hub(tmp_path, cloud, info=lambda host: next(answers, awake),
                      config={"host": "10.0.0.18", "mac": "AA:BB:CC:DD:EE:FF"})
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert result.ok and result.state.power is True and "ainda não a vê" in result.message


def test_magic_packet_goes_to_subnet_broadcast_and_tv(monkeypatch):
    import socket

    from smarthome.adapters import wol

    sent = []
    monkeypatch.setattr(socket.socket, "sendto", lambda self, data, addr: sent.append(addr[0]))
    wol.send_magic_packet("AA:BB:CC:DD:EE:FF", host="10.0.0.18")
    assert sent == ["255.255.255.255", "10.0.0.255", "10.0.0.18"]


def test_turn_on_always_sends_wake_on_lan_when_mac_is_known(tmp_path):
    """A SmartThings pode aceitar o 'ligar' sem a TV reagir; o pacote vai junto, por garantia."""
    cloud = FlakyCloud(fails=0)
    hub, woken = make_hub(tmp_path, cloud, config={"host": "10.0.0.18", "mac": "AA:BB:CC:DD:EE:FF"})
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert result.ok and woken == ["AA:BB:CC:DD:EE:FF"] and cloud.calls == [Command.TURN_ON]
    # desligar não acorda
    asyncio.run(hub.execute("tv-sala", Command.TURN_OFF))
    assert len(woken) == 1


def test_one_scan_serves_all_tvs_and_logs_only_changes(tmp_path, caplog):
    import logging

    scans = []

    async def scan():
        scans.append(1)
        return []

    registry = DeviceRegistry(tmp_path / "devices.yaml")
    for i in (1, 2, 3):
        registry.add(Device(id=f"tv-{i}", name=f"TV {i}", type=DeviceType.TV, adapter="smartthings",
                            config={"device_id": f"st-{i}"}))
    recovery = TVRecovery(scan=scan, timeout=9, interval=3)
    hub = Hub(registry, [FlakyCloud(fails=99)], recovery=recovery)
    with caplog.at_level(logging.INFO, logger="smarthome.recovery"):
        for i in (1, 2, 3):
            asyncio.run(hub.get_state(f"tv-{i}"))
        asyncio.run(hub.get_state("tv-1"))
    assert len(scans) == 1  # três TVs offline, uma varredura só
    assert caplog.text.count("TVs Samsung na rede local") == 1


def test_learn_matches_tv_by_smartthings_label_even_when_user_renamed_it(tmp_path):
    """'TV da sala' não bate com o nome que a TV anuncia; o nome na SmartThings bate."""

    class Cloud(FlakyCloud):
        async def device_label(self, device):
            return "[TV] Samsung 6 Series (55)"

    registry = DeviceRegistry(tmp_path / "devices.yaml")
    registry.add(Device(id="tv-sala", name="TV da sala", type=DeviceType.TV, adapter="smartthings",
                        config={"device_id": "st-1"}))
    lan = [{"host": "10.0.0.10", "name": "[TV] Samsung 6 Series (55)", "model": "", "mac": "AA:BB:CC:DD:EE:01", "power": True}]

    async def scan():
        return lan

    async def no_sleep(_s):
        pass

    recovery = TVRecovery(scan=scan, sleep=no_sleep)
    hub = Hub(registry, [Cloud(fails=0)], recovery=recovery)
    asyncio.run(recovery.learn(hub))
    saved = DeviceRegistry(tmp_path / "devices.yaml").get("tv-sala").config
    assert saved == {"device_id": "st-1", "st_name": "[TV] Samsung 6 Series (55)", "host": "10.0.0.10", "mac": "AA:BB:CC:DD:EE:01"}


def test_scan_merges_ssdp_with_sweep_and_extra_networks(monkeypatch):
    """TV que não responde multicast, ou que está no outro roteador, ainda é encontrada."""
    import ipaddress

    from smarthome.adapters import samsung_lan

    async def fake_info(host, timeout=2.0):
        if host in ("10.0.0.18", "10.0.0.20", "192.168.0.30"):
            return {"host": host, "name": f"TV {host}", "model": "", "mac": None, "power": True}
        return None

    monkeypatch.setattr(samsung_lan, "_ssdp_hosts", lambda timeout: {"10.0.0.18"})
    monkeypatch.setattr(samsung_lan, "tv_info", fake_info)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: ipaddress.ip_network("10.0.0.0/24"))
    monkeypatch.setenv("TV_SCAN_NETWORKS", "192.168.0.0/24, 10.0.0.0/8, lixo")
    found = asyncio.run(samsung_lan.find_samsung_tvs())
    assert [t["host"] for t in found] == ["10.0.0.18", "10.0.0.20", "192.168.0.30"]
    assert [str(n) for n in samsung_lan.extra_networks()] == ["192.168.0.0/24"]


def test_tv_online_in_the_cloud_is_learned_right_away(tmp_path):
    """TV aparece ligada pela nuvem sem IP/MAC: a central busca na rede na hora, não em 5 min."""

    class OnlineCloud(FlakyCloud):
        async def get_state(self, device):
            return DeviceState(online=True, power=True)

    lan = [{"host": "10.0.0.10", "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:01", "power": True}]
    hub, _ = make_hub(tmp_path, OnlineCloud(fails=0), lan=lan)

    async def flow():
        state = await hub.get_state("tv-sala")
        assert state.online and state.power
        await asyncio.sleep(0)  # deixa o aprendizado em segundo plano rodar
        await hub.recovery._learn_task
        return hub.registry.get("tv-sala").config

    config = asyncio.run(flow())
    assert config["host"] == "10.0.0.10" and config["mac"] == "AA:BB:CC:DD:EE:01"


def test_neighbor_networks_come_from_traceroute_and_gateway_probes(monkeypatch):
    """Casa com dois roteadores: a rede do modem é descoberta sem configuração."""
    import ipaddress

    from smarthome.adapters import samsung_lan

    async def hops():
        return ["10.0.0.1", "192.168.0.1", "177.1.2.3"]

    async def answers(ip, timeout=0.7):
        return ip == "192.168.15.1"

    monkeypatch.setattr(samsung_lan, "_neighbors", None)
    monkeypatch.setattr(samsung_lan, "_traceroute_hops", hops)
    monkeypatch.setattr(samsung_lan, "_gateway_answers", answers)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: ipaddress.ip_network("10.0.0.0/24"))
    found = asyncio.run(samsung_lan.neighbor_networks(force=True))
    assert [str(n) for n in found] == ["192.168.0.0/24", "192.168.15.0/24"]
    nets = asyncio.run(samsung_lan.scan_networks())
    assert [str(n) for n in nets] == ["10.0.0.0/24", "192.168.0.0/24", "192.168.15.0/24"]


def test_tv_in_standby_is_shown_off_even_if_the_cloud_says_on(tmp_path):
    """A SmartThings segue dizendo 'ligada' depois que a TV entra em espera; a TV, pela rede, corrige."""

    class StaleCloud(FlakyCloud):
        async def get_state(self, device):
            return DeviceState(online=True, power=True, attributes={"volume": 12})

    hub, _ = make_hub(tmp_path, StaleCloud(fails=0),
                      info=lambda host: {"host": host, "name": TV_NAME, "model": "", "mac": "AA", "power": False},
                      config={"host": "10.0.0.10", "mac": "AA:BB:CC:DD:EE:01"})
    state = asyncio.run(hub.get_state("tv-sala"))
    assert state.power is False and state.attributes["volume"] == 12


def test_tv_without_power_state_is_woken_instead_of_assumed_on(tmp_path):
    """TV que responde na rede mas não informa PowerState pode estar em espera: acorda e espera a nuvem."""
    cloud = FlakyCloud(fails=2)
    lan = [{"host": "10.0.0.18", "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:FF", "power": True, "power_known": False}]
    hub, woken = make_hub(tmp_path, cloud, lan=lan)
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert result.ok and woken and "acordei pela rede" in result.message


def test_tv_without_power_state_does_not_show_on_when_cloud_says_offline(tmp_path):
    hub, _ = make_hub(tmp_path, FlakyCloud(fails=99),
                      info=lambda host: {"host": host, "name": TV_NAME, "model": "", "mac": "AA", "power": True, "power_known": False},
                      config={"host": "10.0.0.18", "mac": "AA:BB:CC:DD:EE:01"})
    state = asyncio.run(hub.get_state("tv-sala"))
    assert state.online is False


def test_stale_ip_is_replaced_from_the_last_scan(tmp_path):
    """Roteador trocado: a TV guardada em 10.0.0.10 agora está em 192.168.0.40."""
    lan = [{"host": "192.168.0.40", "name": TV_NAME, "model": "", "mac": "AA:BB:CC:DD:EE:01", "power": True}]
    hub, _ = make_hub(tmp_path, FlakyCloud(fails=99), lan=lan,
                      info=lambda host: lan[0] if host == "192.168.0.40" else None,
                      config={"host": "10.0.0.10", "mac": "AA:BB:CC:DD:EE:01"})
    asyncio.run(hub.recovery.learn(hub))  # varredura já feita
    state = asyncio.run(hub.get_state("tv-sala"))
    assert state.online and state.power
    assert hub.registry.get("tv-sala").config["host"] == "192.168.0.40"


def test_tv_reachable_but_mute_about_power_gets_the_power_key(tmp_path):
    """TV responde em espera sem informar energia e ignora o Wake-on-LAN: a tecla POWER pela rede acorda."""

    class LocalWithKey(LocalTV):
        def __init__(self):
            super().__init__()
            self.keys = []

        async def power_key(self, device):
            self.keys.append(device.id)

    cloud = FlakyCloud(fails=2)
    lan = [{"host": "192.168.0.217", "name": TV_NAME, "model": "", "mac": "64:1C:B0:0F:B9:3A", "power": True, "power_known": False}]
    local = LocalWithKey()
    hub, woken = make_hub(tmp_path, cloud, lan=lan, local=local)
    result = asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert result.ok and woken and local.keys == ["tv-sala"]


def test_unpaired_tv_is_paired_while_on_and_key_waits_for_pairing(tmp_path):
    """Sem autorização, a tecla pela rede não é tentada; com a TV ligada na nuvem, a central pede a autorização."""

    class LocalPairing(LocalTV):
        def __init__(self):
            super().__init__()
            self.keys, self.pairs, self.is_paired = [], [], False

        def paired(self, device):
            return self.is_paired

        async def pair(self, device):
            self.pairs.append(device.id)
            self.is_paired = True
            return True

        async def power_key(self, device):
            self.keys.append(device.id)

    class OnlineCloud(FlakyCloud):
        async def get_state(self, device):
            return DeviceState(online=True, power=True)

    local = LocalPairing()
    lan = [{"host": "192.168.0.217", "name": TV_NAME, "model": "", "mac": "64:1C:B0:0F:B9:3A", "power": True, "power_known": False}]
    hub, _ = make_hub(tmp_path, FlakyCloud(fails=2), lan=lan, local=local,
                      info=lambda host: lan[0], config={"host": "192.168.0.217", "mac": "64:1C:B0:0F:B9:3A"})
    asyncio.run(hub.execute("tv-sala", Command.TURN_ON))
    assert local.keys == []  # não pareada: não tenta a tecla

    hub.set_adapter(OnlineCloud(fails=0))

    async def flow():
        await hub.get_state("tv-sala")
        await asyncio.gather(*hub.recovery._pair_tasks.values())

    asyncio.run(flow())
    assert local.pairs == ["tv-sala"] and local.is_paired
