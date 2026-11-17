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
