"""API HTTP da central: base para o app, para a voz e para a skill da Alexa."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent import HomeAgent
from .config import build_hub
from .hub import Hub
from .models import CommandRequest, CommandResult, Device, DeviceState, DeviceType, DiscoveredDevice


class NewDevice(BaseModel):
    name: str
    type: DeviceType
    adapter: str
    room: str | None = None
    config: dict[str, Any] = {}
    aliases: list[str] = []


class AgentRequest(BaseModel):
    text: str
    session_id: str = "default"


class AgentResponse(BaseModel):
    reply: str


def create_app(hub: Hub | None = None, agent: HomeAgent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = hub or build_hub()
        app.state.agent = agent
        yield

    app = FastAPI(title="Central da Casa", version="0.1.0", lifespan=lifespan)

    def _hub() -> Hub:
        return app.state.hub

    @app.get("/devices", response_model=list[Device])
    async def list_devices(room: str | None = None) -> list[Device]:
        return _hub().registry.in_room(room) if room else _hub().registry.all()

    @app.post("/devices", response_model=Device, status_code=201)
    async def add_device(body: NewDevice) -> Device:
        try:
            return _hub().register(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/devices/{device_id}", status_code=204)
    async def remove_device(device_id: str) -> None:
        _hub().registry.remove(device_id)

    @app.get("/devices/{device_id}/state", response_model=DeviceState)
    async def device_state(device_id: str) -> DeviceState:
        state = await _hub().get_state(device_id)
        if state is None:
            raise HTTPException(404, "Dispositivo não encontrado")
        return state

    @app.post("/devices/{device_id}/commands", response_model=CommandResult)
    async def send_command(device_id: str, body: CommandRequest) -> CommandResult:
        if _hub().registry.get(device_id) is None:
            raise HTTPException(404, "Dispositivo não encontrado")
        return await _hub().execute(device_id, body.command, body.params)

    @app.get("/discover", response_model=list[DiscoveredDevice])
    async def discover(adapter: str | None = None) -> list[DiscoveredDevice]:
        return await _hub().discover(adapter)

    @app.post("/agent", response_model=AgentResponse)
    async def talk(body: AgentRequest) -> AgentResponse:
        if app.state.agent is None:
            app.state.agent = HomeAgent(_hub())
        return AgentResponse(reply=await app.state.agent.handle(body.text, body.session_id))

    return app


app = create_app()
