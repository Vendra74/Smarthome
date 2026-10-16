"""Rotinas: uma cena disparada sozinha, por horário ("às 22h, boa noite") ou por
mudança de estado de um aparelho ("quando a TV da sala ligar, luz em 20%").

Ficam em data/homes/<id>/routines.yaml. O Hub avalia os gatilhos: o relógio
roda em segundo plano a cada 30 s e os gatilhos de estado são checados a cada
comando executado e a cada estado consultado (o app consulta a cada 15 s), com
detecção de borda: só dispara quando o valor muda para o esperado.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, Field

from .models import DeviceState
from .registry import _normalize

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]  # datetime.weekday(): 0 = segunda


class TimeTrigger(BaseModel):
    type: Literal["time"] = "time"
    at: str = Field(description="Hora local HH:MM, ex.: 22:00")
    days: list[str] = Field(default_factory=list, description="seg..dom; vazio = todos os dias")

    def matches(self, now: datetime) -> bool:
        if self.days and WEEKDAYS[now.weekday()] not in self.days:
            return False
        return now.strftime("%H:%M") == self.at


class StateTrigger(BaseModel):
    type: Literal["state"] = "state"
    device_id: str
    attribute: str = Field(default="power", description="power ou um atributo do estado, ex.: app")
    equals: Any = Field(default=True, description="Valor que dispara, ex.: true, 'netflix'")

    def value(self, state: DeviceState) -> Any:
        return state.power if self.attribute == "power" else state.attributes.get(self.attribute)


class Routine(BaseModel):
    id: str
    name: str
    enabled: bool = True
    trigger: TimeTrigger | StateTrigger = Field(discriminator="type")
    scene_id: str
    last_run: str | None = None  # ISO, informativo


class RoutineStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._routines: dict[str, Routine] = {}
        self._lock = threading.Lock()
        if path and path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for raw in data.get("routines", []):
                routine = Routine.model_validate(raw)
                self._routines[routine.id] = routine

    def all(self) -> list[Routine]:
        return list(self._routines.values())

    def get(self, routine_id: str) -> Routine | None:
        return self._routines.get(routine_id)

    def find_by_name(self, name: str) -> list[Routine]:
        target = _normalize(name)
        exact = [r for r in self._routines.values() if target in {_normalize(r.name), r.id}]
        return exact or [r for r in self._routines.values() if target in _normalize(r.name)]

    def add(self, routine: Routine) -> Routine:
        with self._lock:
            if routine.id in self._routines:
                raise ValueError(f"Já existe uma rotina com id '{routine.id}'")
            self._routines[routine.id] = routine
            self._save()
        return routine

    def update(self, routine: Routine) -> Routine:
        with self._lock:
            if routine.id not in self._routines:
                raise ValueError(f"Rotina '{routine.id}' não existe")
            self._routines[routine.id] = routine
            self._save()
        return routine

    def remove(self, routine_id: str) -> bool:
        with self._lock:
            existed = self._routines.pop(routine_id, None) is not None
            self._save()
        return existed

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"routines": [r.model_dump(mode="json") for r in self._routines.values()]}
        self._path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


class RoutineRunner:
    """Avalia gatilhos e dispara as cenas. Um por Hub."""

    def __init__(self, store: RoutineStore, clock: Any = datetime.now) -> None:
        self.store = store
        self._clock = clock
        self._last_minute: str | None = None  # "YYYY-MM-DD HH:MM" já avaliado
        self._seen: dict[str, Any] = {}  # (device_id, attribute) -> último valor visto

    async def tick(self, hub: Hub, now: datetime | None = None) -> list[str]:
        """Gatilhos de horário: roda no máximo uma vez por minuto."""
        now = now or self._clock()
        minute = now.strftime("%Y-%m-%d %H:%M")
        if minute == self._last_minute:
            return []
        self._last_minute = minute
        fired = []
        for routine in self.store.all():
            if routine.enabled and isinstance(routine.trigger, TimeTrigger) and routine.trigger.matches(now):
                await self._fire(hub, routine, now)
                fired.append(routine.id)
        return fired

    async def observe(self, hub: Hub, device_id: str, state: DeviceState | None) -> list[str]:
        """Gatilhos de estado: dispara quando o valor observado muda para o esperado."""
        if state is None or not state.online:
            return []
        fired = []
        for routine in self.store.all():
            trigger = routine.trigger
            if not isinstance(trigger, StateTrigger) or trigger.device_id != device_id:
                continue
            key = f"{device_id}/{trigger.attribute}"
            value = trigger.value(state)
            previous = self._seen.get(key, ...)
            self._seen[key] = value
            if previous is ... or previous == value or value != trigger.equals or not routine.enabled:
                continue
            await self._fire(hub, routine, self._clock())
            fired.append(routine.id)
        return fired

    async def _fire(self, hub: Hub, routine: Routine, now: datetime) -> None:
        log.info("Rotina %s disparou a cena %s", routine.id, routine.scene_id)
        try:
            result = await hub.run_scene(routine.scene_id)
            if not result.ok:
                log.warning("Rotina %s: cena %s com falhas: %s", routine.id, routine.scene_id,
                            "; ".join(r.message for r in result.failed))
        except Exception as exc:  # uma rotina nunca derruba a central
            log.warning("Rotina %s falhou: %s", routine.id, exc)
        try:
            self.store.update(routine.model_copy(update={"last_run": now.isoformat(timespec="minutes")}))
        except ValueError:
            pass  # removida enquanto rodava

    def start_background(self, hub: Hub, interval: float = 30.0) -> asyncio.Task:
        async def loop() -> None:
            while True:
                try:
                    await self.tick(hub)
                except Exception as exc:
                    log.warning("Relógio das rotinas falhou: %s", exc)
                await asyncio.sleep(interval)

        return asyncio.ensure_future(loop())
