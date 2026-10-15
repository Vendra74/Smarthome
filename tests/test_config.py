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


def test_env_file_is_loaded_without_overriding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ST_DOTENV=do-arquivo\nST_JA_DEFINIDA=do-arquivo\n")
    monkeypatch.delenv("ST_DOTENV", raising=False)
    monkeypatch.setenv("ST_JA_DEFINIDA", "do-ambiente")
    build_hub("x", data_dir=tmp_path)
    import os

    assert os.environ["ST_DOTENV"] == "do-arquivo"
    assert os.environ["ST_JA_DEFINIDA"] == "do-ambiente"
    monkeypatch.delenv("ST_DOTENV")


def test_integration_status_explains_what_is_missing(tmp_path, monkeypatch):
    home = tmp_path / "homes" / "c"
    home.mkdir(parents=True)
    (home / "home.yaml").write_text("name: C\nadapters: [virtual, smartthings]\n")
    monkeypatch.delenv("SMARTTHINGS_TOKEN", raising=False)
    status = {i["id"]: i for i in build_hub("c", data_dir=tmp_path).integrations}
    assert status["smartthings"]["status"] == "missing_token"
    assert "Contas" in status["smartthings"]["detail"]
    assert status["lg_thinq"]["status"] == "off"

    monkeypatch.setenv("SMARTTHINGS_TOKEN", "x")
    status = {i["id"]: i for i in build_hub("c", data_dir=tmp_path).integrations}
    assert status["smartthings"]["status"] == "active"
