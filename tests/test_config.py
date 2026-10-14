from smarthome.config import build_hub, load_home


def test_home_folder_defines_hub(tmp_path, monkeypatch):
    home = tmp_path / "homes" / "praia"
    home.mkdir(parents=True)
    (home / "home.yaml").write_text(
        "name: Casa de praia\nrooms: [varanda]\nadapters: [virtual, smartthings]\nsmartthings: {token_env: ST_PRAIA}\n"
    )
    (home / "devices.yaml").write_text(
        "devices:\n  - {id: luz-varanda, name: Luz da varanda, room: varanda, type: light, adapter: virtual}\n"
    )
    monkeypatch.delenv("ST_PRAIA", raising=False)
    hub = build_hub("praia", data_dir=tmp_path)
    assert hub.home.name == "Casa de praia"
    assert set(hub.adapters) == {"virtual"}  # sem token, sem SmartThings
    assert [d.id for d in hub.registry.all()] == ["luz-varanda"]

    monkeypatch.setenv("ST_PRAIA", "abc")
    assert set(build_hub("praia", data_dir=tmp_path).adapters) == {"virtual", "smartthings"}


def test_missing_home_gets_defaults(tmp_path):
    home = load_home("nova", data_dir=tmp_path)
    assert home.name == "nova" and "virtual" in home.adapters
