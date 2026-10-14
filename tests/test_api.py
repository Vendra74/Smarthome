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
        assert client.get("/app.js").status_code == 200


def test_agent_without_key_returns_503(hub, monkeypatch, tmp_path):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "anthropic"))
    with TestClient(create_app(hub=hub)) as client:
        r = client.post("/agent", json={"text": "acende a luz"})
        assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
