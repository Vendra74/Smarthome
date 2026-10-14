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
