import asyncio
from datetime import datetime

import pytest

from smarthome.models import Command
from smarthome.routines import Routine, RoutineStore, StateTrigger, TimeTrigger
from smarthome.scenes import SceneAction


def run(coro):
    return asyncio.run(coro)


def make_scene(hub):
    return hub.create_scene("Modo filme", [SceneAction(device_id="luz-sala", command=Command.SET_BRIGHTNESS, params={"level": 20})])


def test_store_persists(tmp_path):
    store = RoutineStore(tmp_path / "routines.yaml")
    store.add(Routine(id="noite", name="Boa noite", trigger=TimeTrigger(at="22:00", days=["sex", "sab"]), scene_id="dormir"))
    store.add(Routine(id="tv", name="TV", trigger=StateTrigger(device_id="tv-sala", equals=True), scene_id="filme"))
    again = RoutineStore(tmp_path / "routines.yaml")
    assert [r.id for r in again.all()] == ["noite", "tv"]
    assert again.get("noite").trigger.days == ["sex", "sab"]
    assert isinstance(again.get("tv").trigger, StateTrigger)
    assert again.find_by_name("boa noite")[0].id == "noite"


def test_time_routine_fires_once_per_minute_on_matching_days(hub):
    make_scene(hub)
    hub.create_routine("Filme às 22h", TimeTrigger(at="22:00", days=["sex"]), "modo-filme")
    friday = datetime(2026, 10, 16, 22, 0, 10)  # sexta
    assert run(hub.routine_runner.tick(hub, friday)) == ["filme-as-22h"]
    assert run(hub.get_state("luz-sala")).attributes["brightness"] == 20
    assert run(hub.routine_runner.tick(hub, friday.replace(second=40))) == []  # mesmo minuto
    assert run(hub.routine_runner.tick(hub, datetime(2026, 10, 17, 22, 0))) == []  # sábado
    assert hub.routines.get("filme-as-22h").last_run == "2026-10-16T22:00"
    # pausada não dispara
    r = hub.routines.get("filme-as-22h")
    hub.routines.update(r.model_copy(update={"enabled": False}))
    assert run(hub.routine_runner.tick(hub, datetime(2026, 10, 23, 22, 0))) == []


def test_state_routine_fires_on_edge_only(hub):
    make_scene(hub)
    hub.create_routine("TV ligou", StateTrigger(device_id="ar-quarto", equals=True), "modo-filme")
    # primeira observação só registra; ligar dispara; ligar de novo (sem mudar) não
    assert run(hub.get_state("ar-quarto")) is not None
    assert run(hub.execute("ar-quarto", Command.TURN_ON)).ok
    assert run(hub.get_state("luz-sala")).attributes["brightness"] == 20
    run(hub.execute("luz-sala", Command.SET_BRIGHTNESS, {"level": 80}))
    run(hub.execute("ar-quarto", Command.SET_TEMPERATURE, {"celsius": 21}))  # continua ligado
    assert run(hub.get_state("luz-sala")).attributes["brightness"] == 80
    run(hub.execute("ar-quarto", Command.TURN_OFF))
    run(hub.execute("ar-quarto", Command.TURN_ON))  # nova borda
    assert run(hub.get_state("luz-sala")).attributes["brightness"] == 20


def test_create_routine_validates(hub):
    with pytest.raises(ValueError):
        hub.create_routine("x", TimeTrigger(at="22:00"), "nao-existe")
    make_scene(hub)
    with pytest.raises(ValueError):
        hub.create_routine("x", TimeTrigger(at="25:99"), "modo-filme")
    with pytest.raises(ValueError):
        hub.create_routine("x", StateTrigger(device_id="nada"), "modo-filme")
