import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QSplitter

from chtec.formats import default_registry
from chtec.llm import LocalSettings
from chtec.ui.window import MainWindow
from tests.test_layout import page


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    w = MainWindow(default_registry(), LocalSettings())
    w.resize(1280, 800)
    w.show()
    app.processEvents()
    yield w
    w.dirty = False
    w.close()
    w.deleteLater()
    app.processEvents()


def test_workspace_has_menus_shared_sidebar_and_resizable_canvases(window):
    assert len(window.menuBar().actions()) >= 6
    assert isinstance(window.layout_pane.splitter, QSplitter)
    assert window.workspace_splitter.widget(0) is window.pages_pane
    assert not window.settings_button.isVisible()
    assert window.view_tabs.y() < 145
    window.add_pages([page()])
    window.view_tabs.setCurrentIndex(1)
    assert window.page_list.isVisible()


def test_project_menu_roundtrip(window, tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    def wait():
        for _ in range(500):
            QApplication.processEvents()
            if window.worker is None:
                return
            QTest.qWait(10)
        pytest.fail("Project command did not finish")

    initial = tmp_path / "original.json"
    copy = tmp_path / "copy.json"
    target = iter([initial, copy])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(next(target)), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(initial), ""))
    window.add_pages([page()])
    window.document.title = "Метрическая книга"
    window.editor.setPlainText("Первая запись")
    window.actions["save_project"].trigger()
    wait()
    assert window.project_path == initial and not window.dirty
    window.editor.setPlainText("Исправленный текст")
    window.actions["save_project"].trigger()
    wait()
    assert "Исправленный текст" in initial.read_text(encoding="utf-8")
    window.actions["save_project_as"].trigger()
    wait()
    assert window.project_path == copy
    window.actions["close_project"].trigger()
    assert not window.document.pages and window.project_path is None
    window.actions["open_project"].trigger()
    wait()
    assert window.project_path == initial and not window.dirty
    assert window.document.title == "Метрическая книга"
    assert window.editor.toPlainText() == "Исправленный текст"
    window.editor.setPlainText("Несохранённая правка")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Cancel)
    window.actions["close_project"].trigger()
    assert window.dirty and window.editor.toPlainText() == "Несохранённая правка"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Save)
    window.actions["close_project"].trigger()
    wait()
    QTest.qWait(10)
    assert not window.document.pages
    assert "Несохранённая правка" in initial.read_text(encoding="utf-8")


def test_open_invalid_project_keeps_current_document(window, tmp_path, monkeypatch):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    path = tmp_path / "invalid.json"
    path.write_text("{", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    original = page()
    window.add_pages([original])
    window.editor.setPlainText("Несохранённый оригинал")
    window.actions["open_project"].trigger()
    for _ in range(500):
        QApplication.processEvents()
        if window.worker is None:
            break
        QTest.qWait(10)
    assert window.worker is None
    assert window.current_page is original and window.dirty
    assert window.editor.toPlainText() == "Несохранённый оригинал"


def test_appearance_change_preserves_unsaved_document_and_restores_panels(window):
    p = page()
    window.add_pages([p])
    window.editor.setPlainText("Настройки: original document")
    before = (p.text, list(p.history), window.dirty)
    window.set_appearance(language="en", theme="dark", scale=150)
    assert window.actions["file"].text() == "File"
    assert window.editor.toPlainText() == "Настройки: original document"
    assert (p.text, p.history, window.dirty) == before
    window.show_pages(False)
    assert not window.pages_pane.isVisible()
    window.reset_panels()
    assert window.pages_pane.isVisible()


def test_large_scale_menu_and_primary_actions_fit_small_window(window):
    window.resize(1280, 720)
    window.set_appearance(language="en", scale=200)
    QApplication.processEvents()
    assert window.width() <= 1280
    assert window.export_button.mapTo(window, window.export_button.rect().bottomRight()).x() < 1280
    assert window.menuBar().isVisible()
    assert window.actions['file'].font().pointSizeF()==20
    window.actions["fit"].trigger()


def test_dynamic_status_and_menus_switch_back_without_touching_content(window):
    from chtec.ui.translations import tr

    window.add_pages([page()])
    window.set_appearance(language="en")
    assert window.pages_label.text() == "PAGES · 1"
    window.statusBar().showMessage(tr("Сохранено: {0}", "E:/Настройки.json"))
    assert window.statusBar().currentMessage() == "Saved: E:/Настройки.json"
    window.set_appearance(language="ru")
    assert window.pages_label.text() == "СТРАНИЦЫ · 1"


def test_ui_string_catalog_covers_translatable_literals():
    import ast
    from pathlib import Path

    from chtec.ui.translations import CATALOG

    missing = set()
    for path in (Path(__file__).parents[1] / "chtec/ui").glob("*.py"):
        if path.stem == "translations":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                source = node.args[0].value
                if any("\u0400" <= c <= "\u04ff" for c in source) and source not in CATALOG:
                    missing.add(source)
    assert missing == set()


@pytest.mark.parametrize("changes", [{"theme": "dark"}, {"language": "en"}, {"scale": 150}])
def test_loaded_photo_survives_live_appearance_change(window, changes):
    window.add_pages([page()])
    QApplication.processEvents()
    before = window.image.original.cacheKey()
    assert not window.image.label.pixmap().isNull()
    window.set_appearance(**changes)
    QApplication.processEvents()
    assert not window.image.label.pixmap().isNull()
    assert window.image.original.cacheKey() == before


def test_zone_menu_actions_follow_selection_and_scene_ui_language(window):
    from chtec.layout import TextBlock

    p = page()
    p.replace_blocks([TextBlock("a", "Настройки", (0.1, 0.1, 0.4, 0.2))])
    window.add_pages([p])
    window.layout_pane.select_ids(["a"])
    assert window.actions["edit_zone"].isEnabled()
    assert window.actions["delete_zone"].isEnabled()
    transform = window.layout_pane.digital.transform()
    window.set_appearance(language="en")
    assert window.layout_pane.source.block_items["a"].toolTip().startswith("Position needs review")
    assert window.layout_pane.digital.block_items["a"].block.text == "Настройки"
    assert window.layout_pane.digital.transform() == transform
    window.layout_pane.select_ids([])
    assert not window.actions["delete_zone"].isEnabled()


def test_empty_scene_message_translates_without_rebuilding(window):
    window.add_pages([page()])
    items = window.layout_pane.digital.scene().items()
    message = next(item for item in items if hasattr(item, "text"))
    window.set_appearance(language="en")
    assert message.text().startswith("No text coordinates")
