"""Contas de fabricante ligadas à casa, sem chave nem token digitado pelo usuário.

Decisão de produto (ver docs/ARQUITETURA.md, "Configuração zero"): o usuário
final só faz "entrar com a conta". Cada conta ligada vira um arquivo JSON em
`data/homes/<id>/accounts/<provedor>.json` (fora do git, só o dono lê), com os
tokens que o fabricante devolveu. Os adaptadores leem e renovam dali.

Caminhos por fabricante:
- SmartThings: OAuth. A empresa registra uma SmartApp uma vez (client id e
  secret no .env); o usuário clica em "Conectar", entra na Samsung e volta.
- Tuya (Smart Life / Tuya Smart): QR code lido no app, pelo SDK de
  compartilhamento de dispositivos, o mesmo caminho do Home Assistant.
- LG ThinQ: só token pessoal, colado numa tela guiada pelo agente.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

PROVIDERS = {
    "smartthings": {"label": "SmartThings (Samsung)", "method": "oauth"},
    "tuya": {"label": "Smart Life / Tuya (inclui Izy Smart)", "method": "qr"},
    "lg_thinq": {"label": "LG ThinQ", "method": "token"},
}


class AccountStore:
    """Um arquivo JSON por provedor, na pasta da casa."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, provider: str) -> Path:
        if provider not in PROVIDERS:
            raise KeyError(provider)
        return self.directory / f"{provider}.json"

    def load(self, provider: str) -> dict[str, Any] | None:
        path = self._path(provider)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, provider: str, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(provider)
        path.write_text(json.dumps({**data, "saved_at": int(time.time())}, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def delete(self, provider: str) -> bool:
        path = self._path(provider)
        if not path.exists():
            return False
        path.unlink()
        return True

    def connected(self) -> list[str]:
        return [p for p in PROVIDERS if self._path(p).exists()]


# ---------- SmartThings OAuth ----------

ST_AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
ST_TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
ST_SCOPES = "r:devices:* x:devices:* r:locations:*"
# A SmartThings só devolve a autorização para um endereço https público (localhost e
# IPs da casa dão 403). A ponte é uma página estática que repassa o código para a
# central, usando o endereço local guardado no "state".
ST_BRIDGE_URL = "https://vendra74.github.io/Smarthome/auth/smartthings/"


class SmartThingsOAuth:
    """Fluxo OAuth de uma SmartApp registrada pela empresa (uma vez, não pelo usuário).

    Registro: `smartthings apps:create` (CLI da SmartThings) do tipo API_ONLY com
    os escopos acima e o redirect da ponte (ST_BRIDGE_URL ou SMARTTHINGS_REDIRECT_URI).
    A ponte devolve o navegador para `<SMARTHOME_PUBLIC_URL>/auth/smartthings/callback`.
    O client id e o secret vão em SMARTTHINGS_CLIENT_ID / SMARTTHINGS_CLIENT_SECRET.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.AsyncBaseTransport | None = None,
        return_url: str | None = None,
    ) -> None:
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.return_url = return_url or redirect_uri  # para onde a ponte manda o navegador
        self._client = httpx.AsyncClient(timeout=15.0, transport=transport)
        self._pending: dict[str, float] = {}  # state -> quando foi criado

    @classmethod
    def from_env(cls, public_url: str | None = None) -> SmartThingsOAuth | None:
        client_id = os.environ.get("SMARTTHINGS_CLIENT_ID")
        secret = os.environ.get("SMARTTHINGS_CLIENT_SECRET")
        if not client_id or not secret:
            return None
        base = (public_url or os.environ.get("SMARTHOME_PUBLIC_URL") or "http://localhost:8000").rstrip("/")
        redirect = os.environ.get("SMARTTHINGS_REDIRECT_URI") or ST_BRIDGE_URL
        return cls(client_id, secret, redirect, return_url=f"{base}/auth/smartthings/callback")

    def authorize_url(self) -> str:
        # state = nonce + "." + base64url(endereço local): a ponte lê a segunda parte
        # para saber a qual central devolver; a central só confere o state inteiro.
        encoded = base64.urlsafe_b64encode(self.return_url.encode()).decode().rstrip("=")
        state = f"{secrets.token_urlsafe(16)}.{encoded}"
        self._pending[state] = time.monotonic()
        # Sem redirect_uri de propósito: a Samsung devolve 403 quando ele vem na URL de
        # autorização (mesmo igual ao registrado) e usa o único redirect do app quando
        # falta. O redirect registrado precisa ser o desta central.
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": ST_SCOPES,
            "state": state,
        }
        return str(httpx.URL(ST_AUTHORIZE_URL, params=params))

    def _auth_header(self) -> dict[str, str]:
        raw = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._client.post(ST_TOKEN_URL, data=data, headers=self._auth_header())
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OAuthError(f"SmartThings recusou ({exc.response.status_code}): {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise OAuthError(f"Sem resposta da SmartThings: {exc}") from exc
        tokens = response.json()
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": time.time() + float(tokens.get("expires_in", 86400)),
            "scope": tokens.get("scope"),
        }

    async def exchange(self, code: str, state: str) -> dict[str, Any]:
        # O state prova que o retorno veio de um "Conectar" iniciado aqui (vale 10 min).
        started = self._pending.pop(state, None)
        if started is None or time.monotonic() - started > 600:
            raise OAuthError("Esse link de conexão expirou; clique em Conectar de novo")
        return await self._token_request(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri}
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})


class OAuthError(Exception):
    pass


class SmartThingsTokens:
    """Entrega ao adaptador um access token válido, renovando e salvando quando vence."""

    def __init__(self, store: AccountStore, oauth: SmartThingsOAuth | None) -> None:
        self.store, self.oauth = store, oauth

    async def __call__(self, force: bool = False) -> str:
        account = self.store.load("smartthings")
        if not account:
            raise OAuthError("Conta SmartThings não conectada")
        expiring = account.get("expires_at", 0) - time.time() < 120
        if (force or expiring) and account.get("refresh_token") and self.oauth:
            fresh = await self.oauth.refresh(account["refresh_token"])
            account = {**account, **fresh}
            self.store.save("smartthings", account)
        return account["access_token"]


# ---------- Tuya por QR code (Smart Life / Tuya Smart) ----------

# Credenciais públicas do caminho "Home Assistant" do SDK de compartilhamento da Tuya.
TUYA_CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
TUYA_SCHEMA = "haauthorize"


class TuyaQRLogin:
    """Gera o QR que o app Smart Life lê e espera a confirmação."""

    def __init__(self, control: Any = None) -> None:
        self._control = control

    def _login(self):
        if self._control is None:
            from tuya_sharing import LoginControl

            self._control = LoginControl()
        return self._control

    def start(self, user_code: str) -> dict[str, Any]:
        """Devolve o token do QR e a imagem (SVG em data URI) para a tela."""
        response = self._login().qr_code(TUYA_CLIENT_ID, TUYA_SCHEMA, user_code)
        if not response.get("success"):
            raise OAuthError(f"Smart Life não aceitou o código de usuário: {response.get('msg') or response}")
        token = response["result"]["qrcode"]
        content = f"tuyaSmart--qrLogin?token={token}"
        return {"token": token, "qr": _qr_data_uri(content)}

    def poll(self, token: str, user_code: str) -> dict[str, Any] | None:
        """None enquanto a pessoa não confirmou no app; a conta quando confirmou."""
        ok, info = self._login().login_result(token, TUYA_CLIENT_ID, user_code)
        if not ok:
            return None
        return {
            "user_code": user_code,
            "terminal_id": info["terminal_id"],
            "endpoint": info["endpoint"],
            "token_response": {
                k: info.get(k) for k in ("access_token", "refresh_token", "uid", "expire_time", "t")
            },
        }


def _qr_data_uri(content: str) -> str:
    import segno

    return segno.make(content, error="m").svg_data_uri(scale=6, border=2)
