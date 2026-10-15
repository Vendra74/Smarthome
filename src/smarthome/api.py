"""API HTTP da central: base para o app, para a voz e para a skill da Alexa."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
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


class HomeInfo(BaseModel):
    id: str
    name: str
    rooms: list[str]


class Integration(BaseModel):
    id: str
    label: str
    status: str  # active, off, missing_token, error
    detail: str


class DeviceView(Device):
    state: DeviceState | None = None


WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger(__name__)


def create_app(hub: Hub | None = None, agent: HomeAgent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.hub = hub or build_hub()
        app.state.agent = agent
        yield

    app = FastAPI(title="Central da Casa", version="0.1.0", lifespan=lifespan)

    def _hub() -> Hub:
        return app.state.hub

    @app.get("/home", response_model=HomeInfo)
    async def home_info() -> HomeInfo:
        home = _hub().home
        rooms = sorted({d.room for d in _hub().registry.all() if d.room})
        if home is None:
            return HomeInfo(id="casa", name="Casa", rooms=rooms)
        # cômodos do home.yaml primeiro, na ordem dada; depois os que só aparecem nos dispositivos
        return HomeInfo(id=home.id, name=home.name, rooms=home.rooms + [r for r in rooms if r not in home.rooms])

    @app.get("/devices/with-state", response_model=list[DeviceView])
    async def devices_with_state() -> list[DeviceView]:
        """Tudo que o app precisa para desenhar a tela numa chamada só."""
        return [
            DeviceView(**d.model_dump(), state=await _hub().get_state(d.id)) for d in _hub().registry.all()
        ]

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

    @app.get("/integrations", response_model=list[Integration])
    async def integrations() -> list[Integration]:
        out = []
        for item in _hub().integrations:
            error = _hub().discovery_errors.get(item["id"])
            if item["status"] == "active" and error:
                item = {**item, "status": "error", "detail": error}
            out.append(Integration(**item))
        return out

    @app.get("/discover", response_model=list[DiscoveredDevice])
    async def discover(adapter: str | None = None) -> list[DiscoveredDevice]:
        return await _hub().discover(adapter)

    @app.post("/agent", response_model=AgentResponse)
    async def talk(body: AgentRequest) -> AgentResponse:
        try:
            if app.state.agent is None:
                app.state.agent = HomeAgent(_hub())
            reply = await app.state.agent.handle(body.text, body.session_id)
        except anthropic.AuthenticationError as exc:
            raise HTTPException(503, "Chave da API do agente inválida: confira ANTHROPIC_API_KEY") from exc
        except anthropic.CredentialsError as exc:
            raise HTTPException(503, "Agente sem chave da API: defina ANTHROPIC_API_KEY") from exc
        except TypeError as exc:
            # o SDK levanta TypeError quando não encontra nenhuma credencial
            if "authentication" not in str(exc):
                raise
            raise HTTPException(503, "Agente sem chave da API: defina ANTHROPIC_API_KEY") from exc
        except anthropic.APIConnectionError as exc:
            raise HTTPException(503, "Agente sem conexão com a internet") from exc
        except anthropic.APIStatusError as exc:
            log.warning("Erro da API do agente: %s", exc)
            raise HTTPException(502, "O agente falhou ao responder, tente de novo") from exc
        return AgentResponse(reply=reply)

    # O app web fica na raiz; precisa vir depois das rotas da API.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
