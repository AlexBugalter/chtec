from chtec.config import SettingsStore
from chtec.llm import LocalSettings


def test_settings_never_write_api_key(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save(LocalSettings(ocr_model="local-vision", api_key="secret-not-on-disk"))
    assert "secret-not-on-disk" not in path.read_text()
    restored = store.load()
    assert restored.ocr_model == "local-vision"
    assert restored.api_key == ""


def test_missing_config_uses_defaults(tmp_path):
    assert SettingsStore(tmp_path / "missing.json").load() == LocalSettings()
