"""API HTTP da central: base para o app, para a voz e para a skill da Alexa."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .accounts import PROVIDERS, OAuthError, SmartThingsOAuth, TuyaQRLogin
from .agent import HomeAgent
from .config import account_adapter, build_hub, integration_status
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


class Account(BaseModel):
    id: str
    label: str
    method: str  # oauth, qr, token
    status: str  # connected, available, unavailable
    detail: str
    removable: bool = True  # False quando entrou por chave do .env (sem "Desconectar")


class TuyaQRRequest(BaseModel):
    user_code: str


class TuyaPollRequest(BaseModel):
    token: str
    user_code: str


class TokenAccount(BaseModel):
    token: str


WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Mostra as linhas INFO da central (comandos, busca na rede) junto com as do uvicorn."""
    root = logging.getLogger("smarthome")
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False


def create_app(hub: Hub | None = None, agent: HomeAgent | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _setup_logging()
        app.state.hub = hub or build_hub()
        app.state.agent = agent
        yield

    app = FastAPI(title="Central da Casa", version="0.1.0", lifespan=lifespan)
    app.state.oauth = None
    app.state.tuya_login = None

    def _hub() -> Hub:
        return app.state.hub

    def _accounts():
        if _hub().accounts is None:
            raise HTTPException(503, "Esta central não guarda contas (sem pasta da casa)")
        return _hub().accounts

    def _oauth() -> SmartThingsOAuth:
        if app.state.oauth is None:
            app.state.oauth = SmartThingsOAuth.from_env()
        if app.state.oauth is None:
            raise HTTPException(
                503, "Conexão com a Samsung ainda não liberada nesta central (faltam SMARTTHINGS_CLIENT_ID e SECRET)"
            )
        return app.state.oauth

    def _tuya_login() -> TuyaQRLogin:
        if app.state.tuya_login is None:
            app.state.tuya_login = TuyaQRLogin()
        return app.state.tuya_login

    def _activate(provider: str) -> None:
        """Conta salva: liga o adaptador dela agora, sem reiniciar a central."""
        hub = _hub()
        adapter = account_adapter(provider, hub.accounts, hub.home) if hub.home else None
        if adapter is not None:
            hub.set_adapter(adapter)
        if hub.home:
            hub.integrations = integration_status(hub.home, hub.accounts)

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
        """Tudo que o app precisa para desenhar a tela numa chamada só (consultas em paralelo)."""
        devices = _hub().registry.all()
        states = await asyncio.gather(*(_hub().get_state(d.id) for d in devices))
        return [DeviceView(**d.model_dump(), state=s) for d, s in zip(devices, states)]

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

    @app.get("/accounts", response_model=list[Account])
    async def accounts() -> list[Account]:
        """Contas de fabricante: o que está conectado e como conectar o resto."""
        connected = _hub().accounts.connected() if _hub().accounts else []
        out = []
        for key, meta in PROVIDERS.items():
            removable = True
            if key in connected:
                status, detail = "connected", "conectada"
            elif key in _hub().adapters:
                status, detail, removable = "connected", "conectada por chave da central", False
            elif key == "smartthings" and SmartThingsOAuth.from_env() is None:
                status, detail = "unavailable", "ainda não liberada nesta central"
            else:
                status, detail = "available", "não conectada"
            out.append(Account(id=key, status=status, detail=detail, removable=removable, **meta))
        return out

    @app.post("/accounts/smartthings/start")
    async def smartthings_start() -> dict[str, str]:
        _accounts()
        return {"url": _oauth().authorize_url()}

    @app.get("/auth/smartthings/callback")
    async def smartthings_callback(code: str = "", state: str = "", error: str = ""):
        if error or not code:
            return RedirectResponse(f"/?conta=smartthings&erro={error or 'sem_codigo'}")
        try:
            tokens = await _oauth().exchange(code, state)
        except OAuthError as exc:
            log.warning("OAuth SmartThings falhou: %s", exc)
            return RedirectResponse("/?conta=smartthings&erro=recusado")
        _accounts().save("smartthings", tokens)
        _activate("smartthings")
        return RedirectResponse("/?conta=smartthings")

    @app.post("/accounts/tuya/qr")
    async def tuya_qr(body: TuyaQRRequest) -> dict[str, str]:
        _accounts()
        try:
            return await asyncio.to_thread(_tuya_login().start, body.user_code.strip())
        except OAuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Smart Life não respondeu: {exc}") from exc

    @app.post("/accounts/tuya/poll")
    async def tuya_poll(body: TuyaPollRequest) -> dict[str, Any]:
        try:
            account = await asyncio.to_thread(_tuya_login().poll, body.token, body.user_code.strip())
        except Exception as exc:
            raise HTTPException(502, f"Smart Life não respondeu: {exc}") from exc
        if account is None:
            return {"status": "pending"}
        _accounts().save("tuya", account)
        _activate("tuya")
        return {"status": "connected"}

    @app.post("/accounts/lg_thinq", response_model=Account)
    async def lg_thinq_account(body: TokenAccount) -> Account:
        _accounts().save("lg_thinq", {"token": body.token.strip()})
        _activate("lg_thinq")
        return Account(id="lg_thinq", status="connected", detail="conectada", **PROVIDERS["lg_thinq"])

    @app.delete("/accounts/{provider}", status_code=204)
    async def disconnect(provider: str) -> None:
        if provider not in PROVIDERS:
            raise HTTPException(404, "Conta desconhecida")
        _accounts().delete(provider)
        _hub().remove_adapter(provider)
        if _hub().home:
            _hub().integrations = integration_status(_hub().home, _hub().accounts)

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

    @app.middleware("http")
    async def revalidate_web_files(request, call_next):
        # Sem isso o navegador guarda o app antigo depois de um git pull.
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # O app web fica na raiz; precisa vir depois das rotas da API.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

    return app


app = create_app()
