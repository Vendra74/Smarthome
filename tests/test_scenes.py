import asyncio

import pytest

from smarthome.models import Command, DeviceState
from smarthome.scenes import Scene, SceneAction, SceneStore, actions_from_state


def run(coro):
    return asyncio.run(coro)


def test_store_persists_and_finds_by_name(tmp_path):
    store = SceneStore(tmp_path / "scenes.yaml")
    store.add(Scene(id="modo-filme", name="Modo filme", aliases=["cinema"], actions=[
        SceneAction(device_id="luz-sala", command=Command.SET_BRIGHTNESS, params={"level": 20}),
    ]))
    again = SceneStore(tmp_path / "scenes.yaml")
    assert [s.id for s in again.all()] == ["modo-filme"]
    assert again.find_by_name("Cinema")[0].id == "modo-filme"
    assert again.find_by_name("filme")[0].id == "modo-filme"
    assert again.remove("modo-filme") and not again.remove("modo-filme")
    with pytest.raises(ValueError):
        store.add(Scene(id="modo-filme", name="x"))


def test_actions_from_state_reproduce_what_is_on_and_off():
    caps = [Command.TURN_ON, Command.TURN_OFF, Command.SET_TEMPERATURE, Command.SET_MODE, Command.SET_FAN_SPEED]
    on = DeviceState(power=True, attributes={"mode": "cool", "target_temperature": 23, "fan_speed": "low", "temperature": 27})
    actions = actions_from_state("ar", on, caps)
    assert [(a.command, a.params) for a in actions] == [
        (Command.TURN_ON, {}),
        (Command.SET_MODE, {"mode": "cool"}),
        (Command.SET_TEMPERATURE, {"celsius": 23}),
        (Command.SET_FAN_SPEED, {"speed": "low"}),
    ]
    assert [a.command for a in actions_from_state("ar", DeviceState(power=False), caps)] == [Command.TURN_OFF]
    # sensor (sem comandos) não gera ação
    assert actions_from_state("s", on, []) == []


def test_hub_creates_runs_and_snapshots_scenes(hub):
    scene = hub.create_scene("Modo filme", [
        SceneAction(device_id="luz-sala", command=Command.SET_BRIGHTNESS, params={"level": 20}),
        SceneAction(device_id="ar-quarto", command=Command.TURN_ON),
        SceneAction(device_id="ar-quarto", command=Command.SET_TEMPERATURE, params={"celsius": 22}),
    ])
    assert scene.id == "modo-filme"
    assert hub.create_scene("Modo filme", scene.actions).id == "modo-filme-2"

    result = run(hub.run_scene("modo-filme"))
    assert result.ok and len(result.results) == 3
    assert run(hub.get_state("luz-sala")).attributes["brightness"] == 20
    assert run(hub.get_state("ar-quarto")).attributes["target_temperature"] == 22

    with pytest.raises(ValueError):
        hub.create_scene("Errada", [SceneAction(device_id="luz-sala", command=Command.SET_TEMPERATURE)])
    with pytest.raises(ValueError):
        hub.create_scene("Errada", [SceneAction(device_id="nada", command=Command.TURN_ON)])

    # salvar o estado atual: luz acesa a 20%, ar ligado a 22
    snap = run(hub.snapshot_scene("Como está", ["luz-sala", "ar-quarto"]))
    assert {(a.device_id, a.command) for a in snap.actions} >= {
        ("luz-sala", Command.SET_BRIGHTNESS), ("ar-quarto", Command.SET_TEMPERATURE)
    }
    assert not run(hub.run_scene("inexistente")).ok


def test_scene_stops_adjusting_a_device_that_failed_to_turn_on(hub):
    # create_scene valida os ids; monta direto no store para simular um aparelho removido depois
    hub.scenes.add(Scene(id="falha", name="Falha", actions=[
        SceneAction(device_id="nada", command=Command.TURN_ON),
        SceneAction(device_id="nada", command=Command.SET_TEMPERATURE, params={"celsius": 20}),
        SceneAction(device_id="luz-sala", command=Command.TURN_ON),
    ]))
    result = run(hub.run_scene("falha"))
    assert not result.ok
    assert [r.device_id for r in result.results] == ["nada", "luz-sala"]  # a segunda ação de "nada" nem roda
