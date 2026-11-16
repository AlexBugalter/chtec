import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

from chtec.layout import TextBlock
from chtec.ui.layout import LayoutPane
from tests.test_layout import page


def coloured_page():
    import io

    from PIL import Image

    from chtec.domain import Page

    data = io.BytesIO()
    Image.new("RGB", (800, 1000), "#365a7c").save(data, "PNG")
    result = Page(data.getvalue(), "synthetic.png", 1)
    result.replace_blocks(
        [TextBlock("a", "A", (0.1, 0.1, 0.3, 0.2)), TextBlock("b", "B", (0.6, 0.6, 0.8, 0.7))]
    )
    return result


def source_colour(canvas):
    from PySide6.QtCore import QPointF

    point = canvas.mapFromScene(QPointF(400, 400))
    return canvas.viewport().grab().toImage().pixelColor(point)


def test_photo_survives_overlay_scene_rebuild(app):
    from PySide6.QtGui import QColor

    pane = LayoutPane()
    pane.resize(1000, 700)
    pane.set_page(coloured_page())
    pane.show()
    app.processEvents()
    assert source_colour(pane.source) == QColor("#365a7c")
    # Fault injection: losing scene items must not destroy the original photo layer.
    pane.source.scene().clear()
    app.processEvents()
    assert source_colour(pane.source) == QColor("#365a7c")


def test_repeated_zone_selection_keeps_rendered_photo(app):
    from PySide6.QtGui import QColor

    pane = LayoutPane()
    pane.resize(1000, 700)
    pane.set_page(coloured_page())
    pane.show()
    app.processEvents()
    for i in range(60):
        pane.select_ids(["a" if i % 2 else "b"])
        app.processEvents()
        assert source_colour(pane.source) == QColor("#365a7c")
    pane.set_page(None)
    app.processEvents()
    assert pane.source.scene().items() == []


def test_zone_selection_zoom_and_pan_keep_photo_background(app):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor

    pane = LayoutPane()
    pane.resize(1000, 700)
    pane.set_page(coloured_page())
    pane.show()
    app.processEvents()
    expected = QColor("#365a7c")
    for scale in (1.2, 2, 0.5):
        pane.source.scale(scale, scale)
        pane.source.zoom *= scale
        for identifier in ("a", "b", "a", "b"):
            pane.select_ids([identifier])
            app.processEvents()
            image = pane.source.viewport().grab().toImage()
            assert any(
                image.pixelColor(x, y) == expected
                for x in range(10, image.width() - 10, 30)
                for y in range(10, image.height() - 10, 30)
            )
    pane.source.centerOn(QPointF(700, 850))
    app.processEvents()
    pane.set_page(None)
    app.processEvents()
    image = pane.source.viewport().grab().toImage()
    assert image.pixelColor(image.width() // 2, image.height() // 2) != expected


def test_failed_photo_decode_preserves_current_display(app):
    from dataclasses import replace

    from PySide6.QtGui import QColor

    from chtec.domain import AppError

    pane = LayoutPane()
    p = coloured_page()
    pane.resize(1000, 700)
    pane.set_page(p)
    pane.show()
    app.processEvents()
    with pytest.raises(AppError, match="изображен"):
        pane.source.set_page(replace(p, image_png=b"invalid PNG"))
    app.processEvents()
    assert source_colour(pane.source) == QColor("#365a7c")


def test_reloading_page_leaves_area_mode_and_restores_panning(app):
    from PySide6.QtWidgets import QGraphicsView

    pane = LayoutPane()
    p = coloured_page()
    pane.set_page(p)
    pane.begin_area()
    pane.set_page(p)
    assert not pane.source.draw_area
    assert pane.source.dragMode() == QGraphicsView.DragMode.ScrollHandDrag


def test_rejected_page_does_not_change_pane_binding_or_selection(app):
    from dataclasses import replace

    from chtec.domain import AppError

    pane = LayoutPane()
    original = coloured_page()
    pane.set_page(original)
    pane.select_ids(["b"])
    area = (0.1, 0.1, 0.3, 0.3)
    pane.set_area(area)
    with pytest.raises(AppError):
        pane.set_page(replace(original, image_png=b"invalid PNG"))
    assert pane.page is original
    assert pane.selected_ids == ["b"]
    assert pane.area == area


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    yield app
    from PySide6.QtCore import QCoreApplication, QEvent

    for widget in app.topLevelWidgets():
        if hasattr(widget, "dirty"):
            widget.dirty = False
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_spatial_selection_uses_id_and_edit_keeps_angle(app):
    p = page()
    p.replace_blocks(
        [
            TextBlock("a", "Дария", (0.1, 0.2, 0.4, 0.3)),
            TextBlock("b", "Дария", (0.7, 0.1, 0.8, 0.9), 90),
        ]
    )
    pane = LayoutPane()
    pane.set_page(p)
    pane.select_ids(["b"])
    assert pane.source.selected_ids == ["b"]
    assert pane.digital.selected_ids == ["b"]
    assert pane.digital.block_items["b"].rotation() == 90
    pane.change_block("b", "Мария", 90)
    assert p.blocks[0].text == "Дария"
    assert p.blocks[1].text == "Мария"
    assert p.blocks[1].angle == 90


def test_menu_protects_busy_editor_and_has_requested_actions(app):
    from chtec.ui.widgets import RevisionEditor

    editor = RevisionEditor()
    editor.setPlainText("Пример")
    editor.selectAll()
    menu = editor.edit_menu()
    actions = {a.text().split("\t")[0]: a for a in menu.actions()}
    assert {"Редактировать", "Копировать", "Вставить", "Удалить"} <= actions.keys()
    actions["Удалить"].trigger()
    assert editor.toPlainText() == ""
    editor.setReadOnly(True)
    actions = {a.text().split("\t")[0]: a for a in editor.edit_menu().actions()}
    assert not actions["Вставить"].isEnabled()


def test_new_spatial_area_wins_over_old_selected_block_for_binding(app, monkeypatch):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QDialog

    from chtec.formats import default_registry
    from chtec.llm import LocalSettings
    from chtec.ui.layout import BlockDialog
    from chtec.ui.window import MainWindow

    p = page()
    a = (0.1, 0.1, 0.3, 0.2)
    b = (0.5, 0.5, 0.8, 0.8)
    p.replace_blocks([TextBlock("old", "Дария", a)], unplaced_text="Мария")
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages([p])
    window.layout_pane.select_ids(["old"])
    assert window.image.selected_region == a
    window.layout_pane.source.area_chosen.emit(b)
    cursor = window.editor.textCursor()
    start = len("Дария\n\n")
    cursor.setPosition(start)
    cursor.setPosition(start + len("Мария"), QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    monkeypatch.setattr(BlockDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.bind_text()
    assert p.blocks[-1].rect == b
    assert p.blocks[-1].text == "Мария"
    assert p.unplaced_text == ""
    assert p.undo()
    assert p.unplaced_text == "Мария"
    window.dirty = False
    window.close()


def test_partial_legacy_binding_preserves_unicode_remainder_and_undo(app, monkeypatch):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QDialog

    from chtec.formats import default_registry
    from chtec.llm import LocalSettings
    from chtec.ui.layout import BlockDialog
    from chtec.ui.window import MainWindow

    p = page()
    p.text = "До 😊 Дария после"
    window = MainWindow(default_registry(), LocalSettings())
    window.add_pages([p])
    window.region_selection_changed((0.2, 0.3, 0.6, 0.4))
    cursor = window.editor.textCursor()
    start = len("До 😊 ".encode("utf-16-le")) // 2
    cursor.setPosition(start)
    cursor.setPosition(start + 5, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    monkeypatch.setattr(BlockDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.bind_text()
    assert p.blocks[0].text == "Дария"
    assert p.unplaced_text == "До 😊  после"
    assert window.editor.isReadOnly()
    assert p.undo()
    assert p.text == "До 😊 Дария после"
    window.dirty = False
    window.close()


def test_layout_export_preview_is_spatial(app):
    from PySide6.QtWidgets import QTabWidget

    from chtec.domain import Document
    from chtec.formats import default_registry
    from chtec.ui.dialogs import ExportPreviewDialog

    p = page()
    p.replace_blocks([TextBlock("a", "Дария", (0.1, 0.2, 0.5, 0.3), 45)])
    dialog = ExportPreviewDialog(Document([p]), default_registry())
    tabs = dialog.findChild(QTabWidget)
    assert tabs.tabText(0) == "Расположение"
    dialog.close()
