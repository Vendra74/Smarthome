import base64
import json
import time

import httpx
import pytest

from smarthome.accounts import (
    AccountStore,
    OAuthError,
    SmartThingsOAuth,
    SmartThingsTokens,
    TuyaQRLogin,
)
from smarthome.adapters.base import AdapterError
from smarthome.adapters.smartthings import SmartThingsAdapter
from smarthome.models import Device, DeviceType


def test_store_saves_one_file_per_provider(tmp_path):
    store = AccountStore(tmp_path / "accounts")
    assert store.connected() == [] and store.load("tuya") is None
    store.save("tuya", {"user_code": "abc"})
    assert store.connected() == ["tuya"]
    assert store.load("tuya")["user_code"] == "abc" and "saved_at" in store.load("tuya")
    assert store.delete("tuya") and not store.delete("tuya")
    with pytest.raises(KeyError):
        store.save("zigbee", {})


def oauth_transport(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = dict(httpx.QueryParams(request.content.decode()))
        if body["grant_type"] == "authorization_code":
            assert body["code"] == "c0de" and body["redirect_uri"] == "https://ponte.example/auth/smartthings/"
            return httpx.Response(200, json={"access_token": "A1", "refresh_token": "R1", "expires_in": 86400})
        assert body["refresh_token"] == "R1"
        return httpx.Response(200, json={"access_token": "A2", "refresh_token": "R2", "expires_in": 86400})

    return httpx.MockTransport(handler)


def test_oauth_authorize_exchange_and_refresh():
    calls = []
    oauth = SmartThingsOAuth(
        "cid", "secret", "https://ponte.example/auth/smartthings/", oauth_transport(calls),
        return_url="http://localhost:8000/auth/smartthings/callback",
    )
    url = httpx.URL(oauth.authorize_url())
    assert url.params["client_id"] == "cid" and "x:devices:*" in url.params["scope"]
    assert "redirect_uri" not in url.params  # a Samsung recusa (403) quando ele vem na URL
    state = url.params["state"]
    # a ponte decodifica o endereço local a partir do state
    nonce, _, encoded = state.partition(".")
    assert base64.urlsafe_b64decode(encoded + "==").decode() == "http://localhost:8000/auth/smartthings/callback"

    import asyncio

    tokens = asyncio.run(oauth.exchange("c0de", state))
    assert tokens["access_token"] == "A1" and tokens["expires_at"] > time.time()
    assert calls[0].headers["authorization"].startswith("Basic ")
    # state só vale uma vez
    with pytest.raises(OAuthError):
        asyncio.run(oauth.exchange("c0de", state))
    assert asyncio.run(oauth.refresh("R1"))["access_token"] == "A2"


def test_adapter_refreshes_token_on_401(tmp_path):
    import asyncio

    store = AccountStore(tmp_path)
    store.save("smartthings", {"access_token": "A1", "refresh_token": "R1", "expires_at": time.time() + 3600})
    oauth = SmartThingsOAuth("cid", "secret", "http://x/cb", oauth_transport([]))
    seen = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        if request.headers["authorization"] == "Bearer A1":
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"components": {"main": {"switch": {"switch": {"value": "on"}}}}})

    adapter = SmartThingsAdapter(transport=httpx.MockTransport(api), token_provider=SmartThingsTokens(store, oauth))
    device = Device(id="luz", name="Luz", type=DeviceType.LIGHT, adapter="smartthings", config={"device_id": "d1"})
    state = asyncio.run(adapter.get_state(device))
    assert state.power is True
    assert seen[:2] == ["Bearer A1", "Bearer A2"] and "Bearer A1" not in seen[2:]  # depois do 401 só o token novo
    assert store.load("smartthings")["access_token"] == "A2"  # renovado e guardado

    store.delete("smartthings")
    adapter._state_cache.clear()
    with pytest.raises(AdapterError, match="não conectada"):
        asyncio.run(adapter.get_state(device))


class FakeLoginControl:
    def __init__(self, confirmed_after=1):
        self.polls, self.confirmed_after = 0, confirmed_after

    def qr_code(self, client_id, schema, user_code):
        if user_code == "ruim":
            return {"success": False, "msg": "user code invalid"}
        return {"success": True, "result": {"qrcode": "tok123"}}

    def login_result(self, token, client_id, user_code):
        self.polls += 1
        if self.polls < self.confirmed_after:
            return False, {"success": False}
        return True, {
            "terminal_id": "t1", "endpoint": "https://apigw.tuyaus.com",
            "access_token": "AT", "refresh_token": "RT", "uid": "u1", "expire_time": 7200, "t": 1,
        }


def test_tuya_qr_login():
    login = TuyaQRLogin(FakeLoginControl(confirmed_after=2))
    started = login.start("AB12")
    assert started["token"] == "tok123" and started["qr"].startswith("data:image/svg+xml")
    assert login.poll("tok123", "AB12") is None
    account = login.poll("tok123", "AB12")
    assert account["terminal_id"] == "t1" and account["token_response"]["access_token"] == "AT"
    with pytest.raises(OAuthError):
        login.start("ruim")
