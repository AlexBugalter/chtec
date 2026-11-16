import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from chtec.backends.catalog import ModelEntry
from chtec.backends.profiles import default_profiles
from chtec.formats import default_registry
from chtec.ui.dialogs import SettingsDialog
from chtec.ui.window import MainWindow


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    for widget in app.topLevelWidgets():
        if hasattr(widget, "dirty"):
            widget.dirty = False
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def idle(app, window):
    deadline = time.monotonic() + 5
    while window.worker and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert window.worker is None


def test_unsloth_selector_is_enabled_and_maps_model_to_file(app):
    profile = default_profiles().profiles["unsloth"]
    dialog = SettingsDialog(profile.settings, profile=profile)
    assert dialog.ocr.isEnabled()
    dialog.show_catalog([ModelEntry("E:/new.gguf", "New", "E:/new.gguf", True)])
    dialog.ocr.setCurrentIndex(0)
    assert dialog.profile_value().model_path == "E:/new.gguf"


def test_platform_and_model_selection_automatically_activate_once(app, monkeypatch):
    events = []

    class Manager:
        def deactivate(self):
            events.append("release")

        def activate(self, profile, cancel, progress):
            events.append(("activate", profile.name, profile.model_path))
            return replace(profile.settings, ocr_model="resident")

    monkeypatch.setattr(
        "chtec.ui.window.catalog",
        lambda p, **kwargs: [
            ModelEntry("E:/one.gguf", "One", "E:/one.gguf", True),
            ModelEntry("E:/two.gguf", "Two", "E:/two.gguf", True),
        ],
    )
    profiles = default_profiles()
    window = MainWindow(
        default_registry(), profiles.active.settings, profiles=profiles, backend_manager=Manager()
    )
    window.backend_selector.setCurrentIndex(window.backend_selector.findData("unsloth"))
    idle(app, window)
    assert events == [("activate", "unsloth", "E:/one.gguf")]
    assert window.runtime_settings.ocr_model == "resident"
    window.model_selector.setCurrentIndex(1)
    idle(app, window)
    assert events[-1] == ("activate", "unsloth", "E:/two.gguf")
    assert len(events) == 2


def test_failed_activation_does_not_show_ready(app, monkeypatch):
    from chtec.domain import AppError

    class Manager:
        def activate(self, *args):
            raise AppError("Test failure")

    monkeypatch.setattr(
        "chtec.ui.window.catalog", lambda p, **kwargs: [ModelEntry("vision", "Vision", vision=True)]
    )
    profiles = default_profiles()
    window = MainWindow(
        default_registry(), profiles.active.settings, profiles=profiles, backend_manager=Manager()
    )
    monkeypatch.setattr(window, "show_error", lambda s: None)
    window.activate_backend()
    idle(app, window)
    assert window.runtime_settings is None
    assert "Готово" not in window.backend_status.text()


def test_catalog_refresh_preserves_working_connection(app, monkeypatch):
    profiles = default_profiles()
    profiles.active.settings = replace(profiles.active.settings, ocr_model="vision")
    window = MainWindow(
        default_registry(), profiles.active.settings, profiles=profiles, backend_manager=object()
    )
    runtime = replace(profiles.active.settings, ocr_model="owned-instance")
    window.runtime_settings = runtime
    monkeypatch.setattr(
        "chtec.ui.window.catalog", lambda p, **kwargs: [ModelEntry("vision", "Vision")]
    )
    window.refresh_models()
    idle(app, window)
    assert window.runtime_settings is runtime


def test_initial_catalog_does_not_silently_select_unloaded_model(app, monkeypatch):
    profiles = default_profiles()
    window = MainWindow(
        default_registry(), profiles.active.settings, profiles=profiles, backend_manager=object()
    )
    monkeypatch.setattr(
        "chtec.ui.window.catalog", lambda p, **kwargs: [ModelEntry("vision", "Vision")]
    )
    window.refresh_models()
    idle(app, window)
    assert window.model_selector.currentIndex() == -1
    assert window.settings.ocr_model == ""


def test_settings_catalog_refresh_preserves_current_manual_choices(app):
    settings = replace(default_profiles().active.settings, ocr_model="old", edit_model="editor-old")
    dialog = SettingsDialog(settings)
    dialog.show_catalog([ModelEntry("old", "Old"), ModelEntry("new", "New")])
    dialog.ocr.setCurrentIndex(1)
    dialog.edit.setEditText("editor-new")
    dialog.show_catalog([ModelEntry("old", "Old"), ModelEntry("new", "New")])
    assert dialog.value().ocr_model == "new"
    assert dialog.value().edit_model == "editor-new"


def test_cloud_setup_requires_explicit_model_choice(app, monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox

    profile = default_profiles().profiles["openai"]
    dialog = SettingsDialog(profile.settings, profile=profile)
    dialog.key.setText("test-key")
    dialog.show_catalog([ModelEntry("model-one", "One")])
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    dialog.save()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_closing_during_catalog_cancel_does_not_open_error_dialog(app, monkeypatch):
    profiles = default_profiles()
    window = MainWindow(
        default_registry(), profiles.active.settings, profiles=profiles, backend_manager=object()
    )
    errors = []
    monkeypatch.setattr(window, "show_error", errors.append)

    def slow_catalog(profile, cancel=None):
        cancel.wait(2)
        return []

    monkeypatch.setattr("chtec.ui.window.catalog", slow_catalog)
    window.show()
    window.refresh_models()
    window.close()
    idle(app, window)
    app.processEvents()
    assert errors == []
    assert not window.isVisible()
