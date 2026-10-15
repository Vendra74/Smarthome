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

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .adapters.base import Adapter
from .adapters.lg_thinq import LGThinQAdapter
from .adapters.lg_tv import LGTVAdapter
from .adapters.samsung_tv import SamsungTVAdapter
from .adapters.smartthings import SmartThingsAdapter
from .adapters.virtual import VirtualAdapter
from .hub import Hub
from .registry import DeviceRegistry


class SmartThingsConfig(BaseModel):
    # O token nunca fica no arquivo: o arquivo diz de qual variável de ambiente lê-lo.
    token_env: str = "SMARTTHINGS_TOKEN"


class LGThinQConfig(BaseModel):
    token_env: str = "LG_THINQ_TOKEN"
    country: str = "BR"


class HomeConfig(BaseModel):
    id: str
    name: str
    rooms: list[str] = Field(default_factory=list)
    adapters: list[str] = Field(
        default_factory=lambda: ["virtual", "samsung_tv", "lg_tv", "smartthings", "lg_thinq"]
    )
    smartthings: SmartThingsConfig = Field(default_factory=SmartThingsConfig)
    lg_thinq: LGThinQConfig = Field(default_factory=LGThinQConfig)


def home_dir(home_id: str, data_dir: Path | None = None) -> Path:
    base = data_dir or Path(os.environ.get("SMARTHOME_DATA", "data"))
    return base / "homes" / home_id


def load_home(home_id: str, data_dir: Path | None = None) -> HomeConfig:
    path = home_dir(home_id, data_dir) / "home.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return HomeConfig.model_validate({"id": home_id, "name": home_id, **(raw or {})})


def build_adapters(home: HomeConfig) -> list[Adapter]:
    adapters: list[Adapter] = []
    if "virtual" in home.adapters:
        adapters.append(VirtualAdapter())
    if "samsung_tv" in home.adapters:
        adapters.append(SamsungTVAdapter())
    if "lg_tv" in home.adapters:
        adapters.append(LGTVAdapter())
    if "smartthings" in home.adapters and (token := os.environ.get(home.smartthings.token_env)):
        adapters.append(SmartThingsAdapter(token))
    if "lg_thinq" in home.adapters and (token := os.environ.get(home.lg_thinq.token_env)):
        adapters.append(LGThinQAdapter(token, country=home.lg_thinq.country))
    return adapters


def build_hub(home_id: str | None = None, data_dir: Path | None = None) -> Hub:
    # Chaves (ANTHROPIC_API_KEY, SMARTTHINGS_TOKEN...) podem vir de um .env na pasta
    # de onde a central é iniciada; variáveis já definidas no ambiente têm prioridade.
    load_dotenv(Path(".env"), override=False)
    home = load_home(home_id or os.environ.get("SMARTHOME_HOME", "casa"), data_dir)
    registry = DeviceRegistry(home_dir(home.id, data_dir) / "devices.yaml")
    return Hub(registry, build_adapters(home), home=home)
