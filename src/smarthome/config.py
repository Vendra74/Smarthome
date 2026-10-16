"""Monta a central de uma casa.

Cada casa tem sua pasta, com a descrição da casa e o cadastro de dispositivos:

    data/homes/<home_id>/home.yaml
    data/homes/<home_id>/devices.yaml

Nada específico de uma casa fica no código. Hoje a central roda uma casa por
processo (SMARTHOME_HOME); o mesmo formato serve para várias casas no futuro.
"""

from __future__ import annotations

import os
from pathlib import Path

import certifi
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .accounts import PROVIDERS, AccountStore, SmartThingsOAuth, SmartThingsTokens
from .adapters.base import Adapter
from .adapters.lg_thinq import LGThinQAdapter
from .adapters.lg_tv import LGTVAdapter
from .adapters.samsung_tv import SamsungTVAdapter
from .adapters.smartthings import SmartThingsAdapter
from .adapters.tuya import TuyaAdapter
from .adapters.tuya_sharing import TuyaSharingAdapter
from .adapters.virtual import VirtualAdapter
from .hub import Hub
from .registry import DeviceRegistry
from .scenes import SceneStore


class SmartThingsConfig(BaseModel):
    # O token nunca fica no arquivo: o arquivo diz de qual variável de ambiente lê-lo.
    token_env: str = "SMARTTHINGS_TOKEN"


class LGThinQConfig(BaseModel):
    token_env: str = "LG_THINQ_TOKEN"
    country: str = "BR"


class TuyaConfig(BaseModel):
    """Intelbras Izy Smart, Smart Life e Tuya Smart: projeto em https://iot.tuya.com."""

    access_id_env: str = "TUYA_ACCESS_ID"
    access_secret_env: str = "TUYA_ACCESS_SECRET"
    region: str = "us"  # data center do projeto Tuya; o Brasil costuma cair em "us"


class HomeConfig(BaseModel):
    id: str
    name: str
    rooms: list[str] = Field(default_factory=list)
    adapters: list[str] = Field(
        default_factory=lambda: ["virtual", "samsung_tv", "lg_tv", "smartthings", "lg_thinq", "tuya"]
    )
    smartthings: SmartThingsConfig = Field(default_factory=SmartThingsConfig)
    lg_thinq: LGThinQConfig = Field(default_factory=LGThinQConfig)
    tuya: TuyaConfig = Field(default_factory=TuyaConfig)


def home_dir(home_id: str, data_dir: Path | None = None) -> Path:
    base = data_dir or Path(os.environ.get("SMARTHOME_DATA", "data"))
    return base / "homes" / home_id


def load_home(home_id: str, data_dir: Path | None = None) -> HomeConfig:
    path = home_dir(home_id, data_dir) / "home.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return HomeConfig.model_validate({"id": home_id, "name": home_id, **(raw or {})})


CLOUD_INTEGRATIONS = {"smartthings": "SmartThings", "lg_thinq": "LG ThinQ", "tuya": "Tuya / Izy Smart"}


def _env_keys(home: HomeConfig, key: str) -> list[str]:
    section = getattr(home, key)
    return [section.access_id_env, section.access_secret_env] if key == "tuya" else [section.token_env]


def integration_status(home: HomeConfig, accounts: AccountStore | None = None) -> list[dict[str, str]]:
    """Situação das integrações de nuvem da casa, para o app explicar o que falta."""
    out = []
    connected = accounts.connected() if accounts else []
    for key, label in CLOUD_INTEGRATIONS.items():
        missing = [e for e in _env_keys(home, key) if not os.environ.get(e)]
        if key not in home.adapters:
            status, detail = "off", f"desligada no home.yaml (adicione {key} em adapters)"
        elif key in connected:
            status, detail = "active", "conta conectada"
        elif missing:
            status, detail = "missing_token", "conecte a conta em Contas"
        else:
            status, detail = "active", "conectada"
        out.append({"id": key, "label": label, "status": status, "detail": detail})
    return out


def account_adapter(provider: str, accounts: AccountStore, home: HomeConfig) -> Adapter | None:
    """Adaptador que usa a conta conectada do provedor, ou None se ela não existe."""
    account = accounts.load(provider)
    if not account:
        return None
    if provider == "smartthings":
        return SmartThingsAdapter(token_provider=SmartThingsTokens(accounts, SmartThingsOAuth.from_env()))
    if provider == "tuya":
        def on_token(token_info: dict) -> None:
            current = accounts.load("tuya") or account
            accounts.save("tuya", {**current, "token_response": {**current["token_response"], **token_info}})

        return TuyaSharingAdapter(account, on_token=on_token)
    if provider == "lg_thinq":
        return LGThinQAdapter(account["token"], country=home.lg_thinq.country)
    return None


def build_adapters(home: HomeConfig, accounts: AccountStore | None = None) -> list[Adapter]:
    """Monta os adaptadores: conta conectada em Contas vale mais que chave no .env."""
    adapters: list[Adapter] = []
    if accounts:
        for provider in PROVIDERS:
            if provider in home.adapters and (adapter := account_adapter(provider, accounts, home)):
                adapters.append(adapter)
    active = {a.name for a in adapters}
    if "virtual" in home.adapters:
        adapters.append(VirtualAdapter())
    if "samsung_tv" in home.adapters:
        adapters.append(SamsungTVAdapter())
    if "lg_tv" in home.adapters:
        adapters.append(LGTVAdapter())
    if "smartthings" in set(home.adapters) - active and (token := os.environ.get(home.smartthings.token_env)):
        adapters.append(SmartThingsAdapter(token))
    if "lg_thinq" in set(home.adapters) - active and (token := os.environ.get(home.lg_thinq.token_env)):
        adapters.append(LGThinQAdapter(token, country=home.lg_thinq.country))
    if "tuya" in set(home.adapters) - active:
        access_id = os.environ.get(home.tuya.access_id_env)
        secret = os.environ.get(home.tuya.access_secret_env)
        if access_id and secret:
            adapters.append(TuyaAdapter(access_id, secret, region=home.tuya.region))
    return adapters


def build_hub(home_id: str | None = None, data_dir: Path | None = None) -> Hub:
    # Chaves (ANTHROPIC_API_KEY, SMARTTHINGS_TOKEN...) podem vir de um .env na pasta
    # de onde a central é iniciada; variáveis já definidas no ambiente têm prioridade.
    load_dotenv(Path(".env"), override=False)
    # No macOS o Python do python.org/Homebrew não acha os certificados raiz do sistema;
    # apontar para o certifi vale para todas as bibliotecas (aiohttp, websockets...).
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    home = load_home(home_id or os.environ.get("SMARTHOME_HOME", "casa"), data_dir)
    folder = home_dir(home.id, data_dir)
    registry = DeviceRegistry(folder / "devices.yaml")
    accounts = AccountStore(folder / "accounts")
    hub = Hub(registry, build_adapters(home, accounts), home=home, scenes=SceneStore(folder / "scenes.yaml"))
    hub.accounts = accounts
    hub.integrations = integration_status(home, accounts)
    return hub
