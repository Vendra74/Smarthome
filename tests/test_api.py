from fastapi.testclient import TestClient

from smarthome.api import create_app


def test_devices_and_commands(hub):
    with TestClient(create_app(hub=hub)) as client:
        assert {d["id"] for d in client.get("/devices").json()} == {"luz-sala", "ar-quarto"}
        assert [d["id"] for d in client.get("/devices", params={"room": "quarto"}).json()] == ["ar-quarto"]

        r = client.post("/devices/ar-quarto/commands", json={"command": "set_temperature", "params": {"celsius": 23}})
        assert r.json()["ok"] and r.json()["state"]["attributes"]["target_temperature"] == 23

        assert client.post("/devices/nada/commands", json={"command": "turn_on"}).status_code == 404

        r = client.post("/devices", json={"name": "Abajur", "type": "light", "adapter": "virtual", "room": "quarto"})
        assert r.status_code == 201 and r.json()["id"] == "abajur-quarto"

        assert client.post("/devices", json={"name": "X", "type": "light", "adapter": "zigbee"}).status_code == 400


def test_home_and_devices_with_state(hub):
    from smarthome.config import HomeConfig

    hub.home = HomeConfig(id="casa", name="Casa do Andre", rooms=["quarto", "cozinha"])
    with TestClient(create_app(hub=hub)) as client:
        home = client.get("/home").json()
        # cômodos do home.yaml primeiro, depois os que só existem nos dispositivos
        assert home == {"id": "casa", "name": "Casa do Andre", "rooms": ["quarto", "cozinha", "sala"]}

        client.post("/devices/luz-sala/commands", json={"command": "turn_on"})
        devices = {d["id"]: d for d in client.get("/devices/with-state").json()}
        assert devices["luz-sala"]["state"]["power"] is True
        assert devices["ar-quarto"]["state"]["power"] is False


def test_web_app_is_served(hub):
    with TestClient(create_app(hub=hub)) as client:
        page = client.get("/")
        assert page.status_code == 200 and "app.js" in page.text
        js = client.get("/app.js")
        assert js.status_code == 200 and js.headers["cache-control"] == "no-cache"


def test_agent_without_key_returns_503(hub, monkeypatch, tmp_path):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "anthropic"))
    with TestClient(create_app(hub=hub)) as client:
        r = client.post("/agent", json={"text": "acende a luz"})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_integrations_report_discovery_errors(hub):
    from smarthome.adapters.base import Adapter, AdapterError

    class Broken(Adapter):
        name = "smartthings"

        async def execute(self, device, command, params):
            raise AdapterError("x")

        async def get_state(self, device):
            raise AdapterError("x")

        async def discover(self):
            raise AdapterError("SmartThings: 401 Unauthorized")

    hub.adapters["smartthings"] = Broken()
    hub.integrations = [{"id": "smartthings", "label": "SmartThings", "status": "active", "detail": "conectada"}]
    with TestClient(create_app(hub=hub)) as client:
        assert client.get("/discover").json() == []
        assert client.get("/integrations").json() == [
            {"id": "smartthings", "label": "SmartThings", "status": "error", "detail": "SmartThings: 401 Unauthorized"}
        ]


def test_accounts_flow(hub, tmp_path, monkeypatch):
    from smarthome.accounts import AccountStore
    from smarthome.config import HomeConfig
    from tests.test_accounts import FakeLoginControl

    monkeypatch.delenv("SMARTTHINGS_CLIENT_ID", raising=False)
    hub.home = HomeConfig(id="casa", name="Casa")
    hub.accounts = AccountStore(tmp_path / "accounts")
    app = create_app(hub=hub)
    with TestClient(app) as client:
        from smarthome.accounts import TuyaQRLogin

        app.state.tuya_login = TuyaQRLogin(FakeLoginControl(confirmed_after=2))
        accounts = {a["id"]: a for a in client.get("/accounts").json()}
        assert accounts["smartthings"]["status"] == "unavailable"  # sem client id da empresa
        # ligada por chave do .env: aparece conectada, sem botão de desconectar
        hub.adapters["smartthings"] = hub.adapters["virtual"]
        st = {a["id"]: a for a in client.get("/accounts").json()}["smartthings"]
        assert st["status"] == "connected" and st["removable"] is False and st["can_connect"] is False
        monkeypatch.setenv("SMARTTHINGS_CLIENT_ID", "cid")
        monkeypatch.setenv("SMARTTHINGS_CLIENT_SECRET", "sec")
        st = {a["id"]: a for a in client.get("/accounts").json()}["smartthings"]
        assert st["can_connect"] is True and "entre com a conta" in st["detail"]
        monkeypatch.delenv("SMARTTHINGS_CLIENT_ID")
        monkeypatch.delenv("SMARTTHINGS_CLIENT_SECRET")
        del hub.adapters["smartthings"]
        assert accounts["tuya"]["status"] == "available" and accounts["tuya"]["method"] == "qr"
        assert client.post("/accounts/smartthings/start").status_code == 503

        qr = client.post("/accounts/tuya/qr", json={"user_code": "AB12"}).json()
        assert qr["qr"].startswith("data:image/svg+xml")
        poll = {"token": qr["token"], "user_code": "AB12"}
        assert client.post("/accounts/tuya/poll", json=poll).json() == {"status": "pending"}
        assert client.post("/accounts/tuya/poll", json=poll).json() == {"status": "connected"}
        assert "tuya" in hub.adapters and hub.accounts.load("tuya")["terminal_id"] == "t1"
        accounts = {a["id"]: a for a in client.get("/accounts").json()}
        assert accounts["tuya"]["status"] == "connected"
        assert {i["id"]: i["status"] for i in client.get("/integrations").json()}["tuya"] == "active"

        assert client.post("/accounts/tuya/qr", json={"user_code": "ruim"}).status_code == 400

        assert client.post("/accounts/lg_thinq", json={"token": " tok "}).json()["status"] == "connected"
        assert hub.accounts.load("lg_thinq")["token"] == "tok" and "lg_thinq" in hub.adapters

        assert client.delete("/accounts/tuya").status_code == 204
        assert "tuya" not in hub.adapters and hub.accounts.load("tuya") is None
        assert client.delete("/accounts/zigbee").status_code == 404


def test_smartthings_oauth_callback(hub, tmp_path, monkeypatch):
    import httpx

    from smarthome.accounts import AccountStore, SmartThingsOAuth
    from smarthome.config import HomeConfig
    from tests.test_accounts import oauth_transport

    monkeypatch.setenv("SMARTTHINGS_CLIENT_ID", "cid")
    monkeypatch.setenv("SMARTTHINGS_CLIENT_SECRET", "sec")
    hub.home = HomeConfig(id="casa", name="Casa")
    hub.accounts = AccountStore(tmp_path / "accounts")
    app = create_app(hub=hub)
    app.state.oauth = SmartThingsOAuth("cid", "sec", "https://ponte.example/auth/smartthings/", oauth_transport([]), return_url="http://localhost:8000/auth/smartthings/callback")
    with TestClient(app) as client:
        url = httpx.URL(client.post("/accounts/smartthings/start").json()["url"])
        assert url.host == "api.smartthings.com"
        r = client.get("/auth/smartthings/callback", params={"code": "c0de", "state": url.params["state"]}, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/?conta=smartthings"
        assert hub.accounts.load("smartthings")["access_token"] == "A1" and "smartthings" in hub.adapters
        r = client.get("/auth/smartthings/callback", params={"code": "x", "state": "falso"}, follow_redirects=False)
        assert "erro=recusado" in r.headers["location"]
        r = client.get("/auth/smartthings/callback", params={"error": "access_denied"}, follow_redirects=False)
        assert "erro=access_denied" in r.headers["location"]
