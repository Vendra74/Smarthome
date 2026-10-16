import asyncio
import time

from smarthome.adapters import samsung_lan
from smarthome.diagnostics import diagnose
from smarthome.models import Command, Device, DeviceState, DeviceType
from smarthome.routines import TimeTrigger
from smarthome.scenes import Scene, SceneAction


def run(coro):
    return asyncio.run(coro)


def codes(report):
    return [f.code for f in report.findings]


def test_all_good(hub, monkeypatch):
    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: False)
    report = run(diagnose(hub, fresh=True))
    assert report.findings == [] and report.summary.startswith("Tudo em ordem")


def test_inactive_adapter_offline_device_and_broken_scene(hub, monkeypatch):
    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: True)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: None)
    hub.registry.add(Device(id="tv-sala", name="TV da sala", type=DeviceType.TV, adapter="smartthings"))
    hub.scenes.add(Scene(id="velha", name="Velha", actions=[SceneAction(device_id="sumiu", command=Command.TURN_ON)]))
    hub.create_routine("R", TimeTrigger(at="10:00"), "velha")
    hub.scenes.remove("velha")
    for _ in range(3):
        hub._note_error("luz-sala", "turn_on", "SmartThings recusou (409 Conflict)")
    report = run(diagnose(hub, fresh=True))
    found = codes(report)
    assert found[0] == "lan_blocked"  # crítico primeiro
    assert "adapter_inactive" in found and "routine_missing_scene" in found and "repeated_failures" in found
    failures = next(f for f in report.findings if f.code == "repeated_failures")
    assert "3 vezes" in failures.title and "espere" in failures.action
    assert "ponto(s) de atenção" in report.summary


def test_tv_standby_vs_unknown_and_other_subnet(hub, monkeypatch):
    import ipaddress

    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: False)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: ipaddress.ip_network("192.168.0.0/24"))

    class Offline:
        name = "smartthings"

        async def execute(self, device, command, params):
            return DeviceState(online=False)

        async def get_state(self, device):
            return DeviceState(online=False)

        async def discover(self):
            return []

    hub.set_adapter(Offline())
    hub.registry.add(Device(id="tv-a", name="TV A", type=DeviceType.TV, adapter="smartthings", config={"mac": "AA", "host": "10.0.0.18"}))
    hub.registry.add(Device(id="tv-b", name="TV B", type=DeviceType.TV, adapter="smartthings"))
    hub.recovery._scan = lambda: asyncio.sleep(0, result=[])  # sem varredura real
    report = run(diagnose(hub, fresh=True))
    by_device = {f.device_id: f.code for f in report.findings if f.code != "other_subnet"}
    assert by_device["tv-a"] == "tv_standby" and by_device["tv-b"] == "tv_unknown_lan"
    assert any(f.code == "other_subnet" and f.device_id == "tv-a" for f in report.findings)


def test_uses_cached_states_unless_fresh(hub, monkeypatch):
    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: False)
    run(hub.get_state("luz-sala"))
    hub.last_states["luz-sala"] = (time.monotonic(), DeviceState(online=False))
    assert "offline" in codes(run(diagnose(hub)))  # usa o cache (offline)
    assert "offline" not in codes(run(diagnose(hub, fresh=True)))  # consulta de novo (virtual está online)


def test_groups_similar_tvs_and_merges_repeated_failures(hub, monkeypatch):
    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: False)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: None)

    class Offline:
        name = "smartthings"

        async def execute(self, device, command, params):
            return DeviceState(online=False)

        async def get_state(self, device):
            return DeviceState(online=False)

        async def discover(self):
            return []

    hub.set_adapter(Offline())
    for n in ("55", "50", "43"):
        hub.registry.add(Device(id=f"tv-{n}", name=f"Samsung ({n})", type=DeviceType.TV, adapter="smartthings"))
    hub.registry.add(Device(id="ar-sala", name="Ar sala", type=DeviceType.AC, adapter="smartthings"))
    for _ in range(6):
        hub._note_error("ar-sala", "turn_off", "NOT_CONNECTED_DEVICE (1222)")
    hub.recovery._scan = lambda: asyncio.sleep(0, result=[])
    report = run(diagnose(hub, fresh=True))
    grouped = next(f for f in report.findings if f.code == "tv_unknown_lan")
    assert grouped.title.startswith("3 TVs") and set(grouped.device_ids) == {"tv-55", "tv-50", "tv-43"}
    assert grouped.concerns("tv-50")
    offline = next(f for f in report.findings if f.code == "offline")
    assert offline.device_id == "ar-sala" and "6 vezes" in offline.detail and "1222" in offline.detail
    assert "repeated_failures" not in codes(report)


def test_lan_tv_offline_is_standby(hub, monkeypatch):
    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: False)
    monkeypatch.setattr(samsung_lan, "local_network", lambda: None)

    class Lan:
        name = "samsung_tv"

        async def execute(self, device, command, params):
            return DeviceState(online=False)

        async def get_state(self, device):
            return DeviceState(online=False)

        async def discover(self):
            return []

    hub.set_adapter(Lan())
    hub.registry.add(Device(id="tv85", name='85" Crystal', type=DeviceType.TV, adapter="samsung_tv", config={"host": "10.0.0.19", "mac": "4C"}))
    report = run(diagnose(hub, fresh=True))
    assert codes(report) == ["tv_standby"] and report.summary.startswith("Tudo em ordem")


def test_registry_unescapes_old_names(tmp_path):
    from smarthome.registry import DeviceRegistry

    path = tmp_path / "devices.yaml"
    path.write_text('devices:\n  - id: tv\n    name: "85&quot; Crystal UHD"\n    type: tv\n    adapter: samsung_tv\n', encoding="utf-8")
    assert DeviceRegistry(path).get("tv").name == '85" Crystal UHD'


def test_errors_are_forgotten_when_device_comes_back(hub, monkeypatch):
    monkeypatch.setattr(samsung_lan, "lan_blocked", lambda: False)
    for _ in range(3):
        hub._note_error("luz-sala", "turn_on", "NOT_CONNECTED_DEVICE (1222)")
    hub.last_states["luz-sala"] = (time.monotonic(), DeviceState(online=False))
    assert {"offline"} <= set(codes(run(diagnose(hub))))
    run(hub.get_state("luz-sala"))  # virtual responde online
    assert codes(run(diagnose(hub))) == []
