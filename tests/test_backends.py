import json
from dataclasses import replace

import pytest

from chtec.backends.profiles import default_profiles
from chtec.config import SettingsStore
from chtec.domain import AppError
from chtec.llm import LocalSettings


def test_profiles_do_not_share_urls_models_or_keys():
    profiles = default_profiles(LocalSettings(ocr_model="vision", api_key="lm-secret"))
    profiles.switch("unsloth")
    assert profiles.active.settings.ocr_model == ""
    assert profiles.active.settings.api_key == ""
    assert profiles.active.settings.base_url == "http://127.0.0.1:8890/v1"
    profiles.active.settings = replace(
        profiles.active.settings, ocr_model="unsloth-model", api_key="u"
    )
    profiles.switch("lmstudio")
    assert profiles.active.settings.ocr_model == "vision"
    assert profiles.active.settings.api_key == "lm-secret"


def test_legacy_settings_migrate_without_losing_lm_model(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"base_url": "http://127.0.0.1:1234/v1", "ocr_model": "vision"}))
    profiles = SettingsStore(path).load_profiles()
    assert profiles.selected == "lmstudio"
    assert profiles.active.settings.ocr_model == "vision"


def test_profile_persistence_keeps_both_models_but_neither_key(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    profiles = default_profiles(LocalSettings(ocr_model="lm", api_key="lm-secret"))
    profiles.switch("unsloth")
    profiles.active.settings = replace(
        profiles.active.settings, ocr_model="u", api_key="unsloth-secret"
    )
    profiles.active.model_path = "E:/models/model.gguf"
    store.save_profiles(profiles)
    assert "lm-secret" not in path.read_text()
    assert "unsloth-secret" not in path.read_text()
    restored = store.load_profiles()
    assert restored.selected == "unsloth"
    assert restored.active.settings.ocr_model == "u"
    assert restored.profiles["lmstudio"].settings.ocr_model == "lm"
    assert restored.active.model_path.endswith("model.gguf")
    assert store.load().ocr_model == "u"


def test_same_endpoint_cannot_represent_both_backends():
    profiles = default_profiles()
    profiles.active.settings = replace(
        profiles.active.settings, base_url="http://localhost:8890/v1"
    )
    with pytest.raises(AppError, match="порты"):
        profiles.validated()


def test_cli_marker_parser_does_not_leak_key():
    from chtec.backends.manager import StartupOutput

    output = StartupOutput()
    output.feed("UNSLOTH_START_PORT: 8890")
    output.feed("UNSLOTH_START_API_KEY: sk-unsloth-private-value")
    output.feed("API Key: sk-unsloth-private-value")
    assert output.api_key == "sk-unsloth-private-value"
    assert output.port == 8890
    assert output.ready
    assert "sk-unsloth" not in "\n".join(output.safe_lines)
    assert "private-value" not in repr(output)


def test_unsloth_launch_requires_local_model_and_disabled_tools(tmp_path):
    from chtec.backends.manager import unsloth_arguments

    model = tmp_path / "local.gguf"
    model.write_bytes(b"GGUF")
    profile = default_profiles().profiles["unsloth"]
    profile.model_path = str(model)
    args = unsloth_arguments(profile)
    assert "--disable-tools" in args
    assert "--no-cloudflare" in args
    assert args[args.index("--host") + 1] == "127.0.0.1"
    profile.model_path = "unsloth/remote-repo"
    with pytest.raises(AppError, match="локальный"):
        unsloth_arguments(profile)


def test_cancelled_activation_never_launches_process():
    from threading import Event

    from chtec.backends.manager import BackendManager

    cancel = Event()
    cancel.set()
    with pytest.raises(AppError, match="отмен"):
        BackendManager().activate(default_profiles().active, cancel, lambda text: None)


def test_unsloth_catalog_is_not_the_loaded_model():
    from chtec.backends.manager import loaded_unsloth_model

    class Client:
        def _request(self, endpoint):
            assert endpoint == "/api/inference/loaded-models"
            return {
                "data": [
                    {"id": "a-unloaded", "loaded": False},
                    {"id": "z-vision", "loaded": True},
                    {"id": "z-vision:Q4_K_M", "loaded": True},
                ]
            }

    assert loaded_unsloth_model(Client()) == "z-vision"


def test_unsloth_missing_loaded_flag_fails_closed():
    from chtec.backends.manager import loaded_unsloth_model

    class Client:
        def _request(self, endpoint):
            return {"data": [{"id": "catalog-only"}]}

    with pytest.raises(AppError, match="загруженную"):
        loaded_unsloth_model(Client())


def test_late_lm_load_keeps_ownership_until_actual_completion(monkeypatch):
    import io
    import subprocess
    from threading import Event

    from chtec.backends.manager import BackendManager

    manager = BackendManager()
    manager.lm_instance = ("lms.exe", "chtec-owned")
    finish = Event()
    unloaded = []

    class Process:
        stdout = io.BytesIO()
        calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired("load", timeout)
            assert finish.wait(5)
            return b"loaded", None

    monkeypatch.setattr(manager, "_spawn", lambda *a, **k: Process())
    monkeypatch.setattr(manager, "_unload_instance", lambda *args: unloaded.append(args))
    with pytest.raises(AppError, match="продолжает контролировать"):
        manager._command(["lms.exe", "load"], Event(), timeout=-1, defer_cancel=True)
    assert manager.lm_instance == ("lms.exe", "chtec-owned")
    with pytest.raises(AppError, match="Дождитесь"):
        manager.deactivate()
    finish.set()
    assert manager.pending_load["done"].wait(5)
    manager.deactivate()
    assert unloaded == [("lms.exe", "chtec-owned")]
    assert manager.lm_instance is None


def test_cancelled_lm_load_completes_then_unloads_owned_instance(monkeypatch, tmp_path):
    from threading import Event

    from chtec.backends.manager import BackendManager

    cli = tmp_path / "lms.exe"
    cli.write_bytes(b"test")
    profile = default_profiles(LocalSettings(ocr_model="vision", edit_model="other")).active
    profile.cli_path = str(cli)
    profile.app_path = str(tmp_path / "missing.exe")
    manager = BackendManager()
    cancel = Event()
    commands = []

    def command(args, stop, timeout=120, defer_cancel=False):
        commands.append(args)
        if args[1] == "load":
            assert defer_cancel
            cancel.set()
        if args[1] == "ps":
            return json.dumps([{"identifier": manager.lm_instance[1]}])
        return ""

    monkeypatch.setattr(manager, "_command", command)
    with pytest.raises(AppError, match="отмен"):
        manager.activate(profile, cancel, lambda _: None)
    assert commands[-1][1] == "unload"
    assert commands[-1][2].startswith("chtec-")
    assert manager.lm_instance is None


def test_late_cleanup_error_can_be_retried(monkeypatch):
    from threading import Event

    from chtec.backends.manager import BackendManager

    manager = BackendManager()
    manager.lm_instance = ("lms.exe", "chtec-retained")
    done = Event()
    done.set()
    manager.pending_load = {"done": done, "error": AppError("temporary error")}
    retried = []
    monkeypatch.setattr(manager, "_unload_instance", lambda *args: retried.append(args))
    manager.deactivate()
    assert retried == [("lms.exe", "chtec-retained")]
    assert manager.pending_load is None
    assert manager.lm_instance is None


def test_windows_lifetime_closes_owned_process(tmp_path):
    import ctypes
    import os
    import subprocess
    import sys
    from ctypes import wintypes

    from chtec.backends.lifetime import ProcessLifetime

    if os.name != "nt":
        pytest.skip("Windows job lifetime")
    child_code = "import time; time.sleep(30)"
    code = (
        "import subprocess, sys, time; time.sleep(.2); "
        f"p=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print(p.pid, flush=True); time.sleep(30)"
    )
    process = subprocess.Popen([sys._base_executable, "-c", code], stdout=subprocess.PIPE)
    lifetime = ProcessLifetime()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    child_handle = None
    try:
        lifetime.attach(process)
        child_id = int(process.stdout.readline())
        child_handle = api.OpenProcess(0x00100000, False, child_id)
        assert child_handle
        lifetime.close()
        process.wait(timeout=10)
        assert api.WaitForSingleObject(child_handle, 10000) == 0
    finally:
        lifetime.close()
        if child_handle:
            api.CloseHandle(child_handle)
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
