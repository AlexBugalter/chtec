import json
from dataclasses import replace

import pytest

from chtec.domain import AppError


def test_cloud_requires_provider_official_https_endpoint():
    from chtec.providers import CloudSettings

    assert CloudSettings().validated().base_url == "https://api.openai.com/v1"
    for url in (
        "http://api.openai.com/v1",
        "https://example.org/v1",
        "https://api.openai.com.evil.test/v1",
        "https://api.openai.com/v1?x=y",
    ):
        with pytest.raises(AppError):
            CloudSettings(base_url=url).validated()


def test_key_is_redacted_from_settings_repr():
    from chtec.providers import CloudSettings

    assert "private-secret" not in repr(CloudSettings(api_key="private-secret"))


def test_profiles_migrate_v2_and_keep_cloud_settings_without_plain_key(tmp_path):
    from chtec.backends.profiles import default_profiles
    from chtec.config import SettingsStore
    from chtec.providers import CloudSettings

    profiles = default_profiles()
    assert len(profiles.profiles) == 8
    profiles.switch("anthropic")
    profiles.active.settings = CloudSettings(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        ocr_model="test-vision",
        api_key="private-key",
    )
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save_profiles(profiles)
    assert "private-key" not in path.read_text()
    restored = store.load_profiles()
    assert restored.active.settings.api_key == "private-key"
    assert restored.active.settings.ocr_model == "test-vision"
    old = json.loads(path.read_text())
    old["version"] = 2
    old["selected"] = "lmstudio"
    old["profiles"] = {k: v for k, v in old["profiles"].items() if k in ("lmstudio", "unsloth")}
    path.write_text(json.dumps(old))
    assert len(store.load_profiles().profiles) == 8


def test_credential_encryption_is_scoped_and_not_plaintext(tmp_path):
    from chtec.credentials import CredentialStore

    store = CredentialStore(tmp_path / "keys")
    store.set("openai", "https://api.openai.com/v1", "test-secret")
    assert store.get("openai", "https://api.openai.com/v1") == "test-secret"
    assert store.get("anthropic", "https://api.openai.com/v1") == ""
    assert all(b"test-secret" not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    store.set("openai", "https://api.openai.com/v1", "")
    assert store.get("openai", "https://api.openai.com/v1") == ""


def test_unreadable_cloud_key_does_not_reset_profiles_or_delete_ciphertext(tmp_path):
    from chtec.backends.profiles import default_profiles
    from chtec.config import SettingsStore

    profiles = default_profiles()
    profiles.active.settings = replace(profiles.active.settings, ocr_model="keep-local")
    profiles.profiles["openai"].settings = replace(
        profiles.profiles["openai"].settings, api_key="test-secret"
    )
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save_profiles(profiles)
    blob = next((tmp_path / "settings-keys").glob("*.dpapi"))
    blob.write_bytes(b"invalid cipher")
    reopened = SettingsStore(path)
    restored = reopened.load_profiles()
    assert restored.active.settings.ocr_model == "keep-local"
    assert restored.profiles["openai"].settings.api_key == ""
    assert reopened.warnings
    reopened.save_profiles(restored)
    assert blob.read_bytes() == b"invalid cipher"
