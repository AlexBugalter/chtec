import io
import os
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from chtec.domain import Page
from chtec.formats import default_registry
from chtec.llm import LocalSettings
from chtec.ui.dialogs import ProposalDialog, SettingsDialog
from chtec.ui.window import MainWindow


def test_backend_switch_releases_previous_and_keeps_settings_isolated(app, tmp_path, monkeypatch):
    from chtec.backends.catalog import ModelEntry
    from chtec.backends.profiles import default_profiles
    from chtec.config import SettingsStore

    profiles = default_profiles(LocalSettings(ocr_model="lm", api_key="lm-private"))
    profiles.profiles["unsloth"].model_path = "local.gguf"

    class Manager:
        def __init__(self):
            self.released = 0

        def deactivate(self):
            self.released += 1

        def activate(self, profile, cancel, progress):
            self.deactivate()
            progress("ready")
            return replace(profile.settings, ocr_model="u-loaded", api_key="u-private")

    manager = Manager()
    monkeypatch.setattr(
        "chtec.ui.window.catalog",
        lambda p, **kwargs: (
            [ModelEntry("local.gguf", "Local", "local.gguf", True)]
            if p.name == "unsloth"
            else [ModelEntry("lm", "LM", vision=True)]
        ),
    )
    store = SettingsStore(tmp_path / "config.json")
    window = MainWindow(default_registry(), profiles.active.settings, store, profiles, manager)
    window.runtime_settings = profiles.active.settings
    window.backend_selector.setCurrentIndex(1)
    assert not window.backend_selector.isEnabled()
    wait_until_idle(app, window)
    assert manager.released == 1
    assert window.settings.api_key == ""
    assert window.runtime_settings is not None
    assert window.model_settings.api_key == "u-private"
    assert window.model_settings.base_url.endswith(":8890/v1")
    assert window.settings.api_key == ""
    window.backend_selector.setCurrentIndex(0)
    wait_until_idle(app, window)
    assert manager.released == 2
    assert window.settings.api_key == "lm-private"
    assert "private" not in store.path.read_text()
    window.close()


def test_changed_unsloth_file_updates_displayed_model_name(app):
    from chtec.backends.profiles import default_profiles

    profile = default_profiles().profiles["unsloth"]
    profile.settings = replace(profile.settings, ocr_model="old")
    dialog = SettingsDialog(profile.settings, profile=profile)
    dialog.model_path.setText("E:/models/new-vision.gguf")
    updated = dialog.profile_value()
    assert updated.settings.ocr_model == "new-vision"
    assert updated.settings.edit_model == "new-vision"
    assert profile.settings.ocr_model == "old"
    dialog.close()


@pytest.fixture(scope="module")
def app():
    from PySide6.QtCore import QCoreApplication, QEvent

    application = QApplication.instance() or QApplication([])
    yield application
    # Dispose Qt windows while QApplication and Python callbacks still exist.
    # Interpreter-finalization order is not a valid GUI lifecycle test.
    for widget in application.topLevelWidgets():
        if isinstance(widget, MainWindow):
            widget.dirty = False
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


def test_manual_editor_navigation_and_undo(app):
    buffer = io.BytesIO()
    Image.new("RGB", (80, 100), "white").save(buffer, "PNG")
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages([Page(buffer.getvalue(), "scan", 1), Page(buffer.getvalue(), "scan", 2)])
    window.editor.setPlainText("Ручная правка")
    assert window.document.pages[0].text == "Ручная правка"
    window.page_list.setCurrentRow(1)
    assert window.editor.toPlainText() == ""
    window.editor.setPlainText("Страница 2")
    window.page_list.setCurrentRow(0)
    assert window.editor.toPlainText() == "Ручная правка"
    window.undo_text()
    assert window.document.pages[0].text == ""
    window.dirty = False
    window.close()


def test_llm_proposal_is_not_applied_implicitly(app):
    dialog = ProposalDialog("Исходный", "Предложенный")
    assert dialog.result() == 0
    assert dialog.proposed_text() == "Предложенный"
    dialog.proposed.setPlainText("Исправлено вручную")
    assert dialog.proposed_text() == "Исправлено вручную"
    dialog.reject()
    assert dialog.result() == 0


def test_settings_round_trip_does_not_drop_key_in_memory(app):
    dialog = SettingsDialog(LocalSettings(ocr_model="vision", api_key="session-secret"))
    assert dialog.value().ocr_model == "vision"
    assert dialog.value().api_key == "session-secret"


def wait_until_idle(app, window):
    deadline = time.monotonic() + 5
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.worker is None


def test_background_import_and_ocr_keep_ui_state(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QThread

    from chtec.ui import window as window_module

    path = tmp_path / "scan.png"
    Image.new("RGB", (100, 160), "white").save(path)
    window = MainWindow(default_registry(), LocalSettings(ocr_model="local-vision"))
    window.show_error = lambda message: pytest.fail(message)
    window.load_paths([str(path)])
    wait_until_idle(app, window)
    assert window.page_list.count() == 1

    class Model:
        def __init__(self, settings):
            pass

        def recognize(self, page, instruction):
            assert QThread.currentThread() != app.thread()
            return "Распознано в фоне"

    monkeypatch.setattr(window_module, "LocalModel", Model)
    window.recognize(True)
    assert window.editor.isReadOnly()
    assert window.task_timer.isActive()
    assert "Ожидание" in window.task_time.text()
    wait_until_idle(app, window)
    assert window.editor.toPlainText() == "Распознано в фоне"
    assert not window.editor.isReadOnly()
    assert not window.task_timer.isActive()
    window.dirty = False
    window.close()


def test_consecutive_undo_does_not_reapply_manual_change(app):
    buffer = io.BytesIO()
    Image.new("RGB", (80, 100), "white").save(buffer, "PNG")
    page = Page(buffer.getvalue(), "scan", 1)
    page.replace_text("OCR result", "OCR")
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages([page])
    window.editor.insertPlainText("manual ")
    window.undo_text()
    assert page.text == "OCR result"
    window.undo_text()
    assert page.text == ""
    window.dirty = False
    window.close()


def test_modified_window_has_no_qt_title_warning(app):
    from PySide6.QtCore import qInstallMessageHandler

    messages = []
    previous = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
    try:
        window = MainWindow(default_registry(), LocalSettings())
        window.show()
        window.dirty = True
        window.refresh_controls()
        app.processEvents()
        window.dirty = False
        window.close()
    finally:
        qInstallMessageHandler(previous)
    assert not any("setWindowModified" in message for message in messages)
    assert "[*]" in window.windowTitle()


def test_console_interrupt_requests_safe_cancellation_without_raising(app):
    import signal

    from PySide6.QtCore import QObject

    from chtec.ui.interrupts import ConsoleInterruptHandler

    class Window(QObject):
        worker = object()
        cancelled = False

        def cancel_task(self):
            self.cancelled = True

    window = Window()
    previous = signal.getsignal(signal.SIGINT)
    guard = ConsoleInterruptHandler(window)
    try:
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert not window.cancelled
        guard.dispatch()
        assert window.cancelled
    finally:
        guard.close()
    assert signal.getsignal(signal.SIGINT) == previous


def test_delete_selected_page_preserves_other_text_and_selection(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    buffer = io.BytesIO()
    Image.new("RGB", (80, 100), "white").save(buffer, "PNG")
    pages = [Page(buffer.getvalue(), "scan", i, f"Текст {i}") for i in range(1, 4)]
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages(pages)
    window.page_list.setCurrentRow(1)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    window.delete_page()
    assert [page.text for page in window.document.pages] == ["Текст 1", "Текст 3"]
    assert window.page_list.count() == 2
    assert window.page_list.currentRow() == 1
    assert window.editor.toPlainText() == "Текст 3"
    window.delete_page()
    window.delete_page()
    assert window.page_list.count() == 0
    assert window.editor.toPlainText() == ""
    assert window.image.original.isNull()
    assert not window.delete_button.isEnabled()
    window.dirty = False
    window.close()


def test_cancel_page_deletion_preserves_document(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    buffer = io.BytesIO()
    Image.new("RGB", (80, 100), "white").save(buffer, "PNG")
    page = Page(buffer.getvalue(), "scan", 1, "Сохранить")
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages([page])
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    window.delete_page()
    assert window.document.pages == [page]
    window.dirty = False
    window.close()


def test_close_during_task_waits_for_cancellation_then_closes(app):
    window = MainWindow(default_registry(), LocalSettings())
    window.show()
    window.run_task(lambda job: job.cancel.wait(2), lambda result: None)
    window.close()
    assert window.worker.cancel.is_set()
    wait_until_idle(app, window)
    app.processEvents()
    assert not window.isVisible()


@pytest.mark.parametrize("height", [426, 430, 436])
def test_preview_resize_stabilizes_at_scrollbar_threshold(app, height):
    from PySide6.QtTest import QTest

    from chtec.ui.widgets import ImagePreview

    class BoundedPreview(ImagePreview):
        def __init__(self):
            self.calls = 0
            super().__init__()

        def render(self):
            self.calls += 1
            # A broken layout must fail this test, not hang the test process.
            if self.calls <= 100:
                super().render()

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1046), "white").save(buffer, "PNG")
    preview = BoundedPreview()
    preview.resize(500, height)
    preview.set_image(buffer.getvalue())
    preview.show()
    QTest.qWait(100)
    calls = preview.calls
    preview.close()
    assert calls < 10, f"Resize feedback: {calls} renders for one stable size"


def test_preview_reuses_scaled_image_at_unchanged_size(app):
    from chtec.ui.widgets import ImagePreview

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1046), "white").save(buffer, "PNG")
    preview = ImagePreview()
    preview.set_image(buffer.getvalue())
    first_key = preview.label.pixmap().cacheKey()
    preview.render()
    assert preview.label.pixmap().cacheKey() == first_key


def test_cancel_additional_file_keeps_current_page_and_ui_responsive(app, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1046), "white").save(buffer, "PNG")
    page = Page(buffer.getvalue(), "first.png", 1, "Сохранённый текст первой страницы")
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages([page])
    window.image.setFixedSize(500, 430)
    window.show()
    # Windows returns precisely this pair on cancelling getOpenFileNames.
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([], ""))
    beats = []
    timer = QTimer()
    timer.timeout.connect(lambda: beats.append(True))
    timer.start(10)
    for _ in range(3):
        window.open_files()
        QTest.qWait(40)
    timer.stop()
    assert len(beats) >= 3
    assert window.worker is None
    assert window.document.pages == [page]
    assert window.editor.toPlainText() == page.text
    assert window.open_button.isEnabled()
    window.dirty = False
    window.close()


def test_window_title_identifies_running_version(app):
    from chtec import __version__

    window = MainWindow(default_registry(), LocalSettings())
    assert f"Чтец {__version__}" in window.windowTitle()
    window.close()


def make_gesture_preview(app):
    from chtec.ui.widgets import ImagePreview

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1046), "white").save(buffer, "PNG")
    preview = ImagePreview()
    preview.resize(500, 430)
    preview.set_image(buffer.getvalue())
    preview.show()
    app.processEvents()
    return preview


def test_wheel_zooms_instead_of_only_scrolling(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    preview = make_gesture_preview(app)
    for delta, expected in ((120, 1.2), (-120, 1.0)):
        event = QWheelEvent(
            QPointF(200, 200),
            QPointF(200, 200),
            QPoint(),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        app.sendEvent(preview.viewport(), event)
        app.processEvents()
        assert preview.zoom == pytest.approx(expected)
    preview.close()


def test_left_drag_pans_zoomed_image(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    preview = make_gesture_preview(app)
    preview.change_zoom(3)
    app.processEvents()
    before = preview.horizontalScrollBar().value()
    QTest.mousePress(preview.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(220, 200))
    QTest.mouseMove(preview.viewport(), QPoint(170, 160))
    QTest.mouseRelease(preview.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(170, 160))
    assert preview.horizontalScrollBar().value() > before
    preview.close()


def test_left_drag_pans_tall_page_at_fit_width(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from chtec.ui.widgets import ImagePreview

    buffer = io.BytesIO()
    Image.new("RGB", (1000, 3000), "white").save(buffer, "PNG")
    preview = ImagePreview()
    preview.resize(500, 430)
    preview.set_image(buffer.getvalue())
    preview.show()
    app.processEvents()
    assert preview.verticalScrollBar().maximum() > 0
    QTest.mousePress(preview.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(220, 200))
    QTest.mouseMove(preview.viewport(), QPoint(220, 100))
    QTest.mouseRelease(preview.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(220, 100))
    assert preview.verticalScrollBar().value() > 0
    preview.close()


def test_region_selection_maps_to_source_coordinates(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    preview = make_gesture_preview(app)
    preview.set_selection_mode(True)
    rect = preview.image_rect()
    start = QPoint(int(rect.x() + rect.width() * 0.2), int(rect.y() + rect.height() * 0.3))
    end = QPoint(int(rect.x() + rect.width() * 0.7), int(rect.y() + rect.height() * 0.8))
    QTest.mousePress(preview.label, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(preview.label, end)
    QTest.mouseRelease(preview.label, Qt.MouseButton.LeftButton, pos=end)
    assert preview.selected_region == pytest.approx((0.2, 0.3, 0.7, 0.8), abs=0.01)
    preview.close()


@pytest.mark.parametrize("accept", [False, True])
@pytest.mark.parametrize("leading", ["", "😀\r\n"])
def test_region_ocr_only_replaces_selected_text_after_confirmation(
    app, monkeypatch, accept, leading
):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QDialog

    from chtec.regions import RegionImage, RegionReading
    from chtec.ui import window as window_module

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buffer, "PNG")
    original = leading + "Имя: [неразборчиво]. Дата: 1883."
    page = Page(buffer.getvalue(), "scan", 1, original)
    window = MainWindow(default_registry(), LocalSettings(ocr_model="vision"))
    window.add_pages([page])
    cursor = window.editor.textCursor()
    visible = window.editor.toPlainText()
    start = len(visible[: visible.index("[")].encode("utf-16-le")) // 2
    cursor.setPosition(start)
    cursor.setPosition(start + 14, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    window.image.selected_region = (0.1, 0.1, 0.5, 0.5)
    window.region_selection_changed(window.image.selected_region)

    class Service:
        def __init__(self, model):
            pass

        def recognize(self, page, region):
            return RegionReading(region, RegionImage(buffer.getvalue(), True, "Оригинал"), "Мария")

    monkeypatch.setattr(window_module, "RegionRecognitionService", Service)
    monkeypatch.setattr(
        ProposalDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected,
    )
    window.recognize_region()
    assert page.text == original
    wait_until_idle(app, window)
    expected = leading.replace("\r\n", "\n") + "Имя: Мария. Дата: 1883." if accept else original
    assert page.text == expected
    window.dirty = False
    window.close()


@pytest.mark.parametrize("accept", [False, True])
def test_page_review_requires_explicit_application(app, monkeypatch, accept):
    from PySide6.QtWidgets import QDialog

    from chtec.regions import ImageRegion, RegionImage, RegionReading, ReviewResult
    from chtec.ui import window as window_module

    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buffer, "PNG")
    page = Page(buffer.getvalue(), "scan", 1, "Имя: [неразборчиво]")
    window = MainWindow(default_registry(), LocalSettings(ocr_model="vision"))
    window.add_pages([page])

    class Service:
        def __init__(self, model):
            pass

        def review(self, page, stop, progress):
            reading = RegionReading(
                ImageRegion(0, 0, 0.5, 0.5),
                RegionImage(buffer.getvalue(), True, "Оригинал"),
                "Мария",
            )
            progress(4, "Мария")
            return ReviewResult("Имя: Мария", [reading] * 4)

    monkeypatch.setattr(window_module, "RegionRecognitionService", Service)
    monkeypatch.setattr(
        ProposalDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected,
    )
    window.review_regions()
    assert page.text == "Имя: [неразборчиво]"
    wait_until_idle(app, window)
    assert page.text == ("Имя: Мария" if accept else "Имя: [неразборчиво]")
    window.dirty = False
    window.close()
