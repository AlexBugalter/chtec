import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chtec.layout import TextBlock
from chtec.ui.layout import BlockDialog, LayoutPane
from tests.test_layout import page


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


def pane_with_zone(app):
    p = page()
    p.replace_blocks([TextBlock("a", "Word", (0.2, 0.2, 0.5, 0.4), 4)])
    pane = LayoutPane()
    pane.resize(1100, 750)
    pane.set_page(p)
    pane.show()
    app.processEvents()
    return pane, p


@pytest.mark.parametrize("side", ["source", "digital"])
def test_drag_geometry_syncs_preview_and_commits_one_revision(app, side):
    pane, p = pane_with_zone(app)
    pane.set_edit_mode(True)
    canvas = getattr(pane, side)
    start = canvas.mapFromScene(QPointF(280, 300))
    end = start + QPoint(25, 20)
    before = len(p.history)
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), end)
    assert len(p.history) == before
    assert pane.source.block_items["a"].block.rect == pane.digital.block_items["a"].block.rect
    assert pane.source.block_items["a"].block.rect != p.blocks[0].rect
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert len(p.history) == before + 1
    assert p.blocks[0].rect[0] > 0.2 and p.blocks[0].rect[1] > 0.2
    assert p.blocks[0].verified
    assert pane.source.selected_ids == pane.digital.selected_ids == ["a"]
    assert p.undo() and p.blocks[0].rect == (0.2, 0.2, 0.5, 0.4)


@pytest.mark.parametrize("side", ["source", "digital"])
def test_resize_handle_keeps_opposite_corner_and_syncs(app, side):
    pane, p = pane_with_zone(app)
    pane.set_edit_mode(True)
    pane.select_ids(["a"])
    canvas = getattr(pane, side)
    corner = canvas.mapFromScene(QPointF(400, 400))
    end = corner + QPoint(20, 20)
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=corner)
    QTest.mouseMove(canvas.viewport(), end)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert p.blocks[0].rect[:2] == (0.2, 0.2)
    assert p.blocks[0].rect[2] > 0.5 and p.blocks[0].rect[3] > 0.4
    assert pane.source.block_items["a"].block.rect == p.blocks[0].rect
    assert pane.digital.block_items["a"].block.rect == p.blocks[0].rect


def test_delete_selected_zone_from_digital_and_undo(app):
    pane, p = pane_with_zone(app)
    pane.select_ids(["a"])
    pane.digital.setFocus()
    QTest.keyClick(pane.digital, Qt.Key.Key_Delete)
    assert p.blocks == [] and pane.source.block_items == {} and pane.digital.block_items == {}
    pane.undo()
    assert p.blocks[0].id == "a"


def test_properties_include_precise_physical_bounds(app):
    block = TextBlock("a", "Text", (0.1, 0.2, 0.4, 0.5), 25)
    dialog = BlockDialog(block)
    assert dialog.rect_value() == block.rect
    dialog.coordinates["left"].setValue(15)
    assert dialog.rect_value() == (0.15, 0.2, 0.4, 0.5)


def test_new_zone_draw_adds_empty_template_field_without_losing_text(app):
    pane, p = pane_with_zone(app)
    pane.start_new_zone()
    pane.source.area_chosen.emit((0.6, 0.7, 0.8, 0.8))
    app.processEvents()
    assert len(p.blocks) == 2
    assert p.blocks[-1].rect == (0.6, 0.7, 0.8, 0.8) and p.blocks[-1].text == ""
    assert p.blocks[0].text == "Word"


def test_escape_rolls_back_both_previews_without_revision(app):
    pane, p = pane_with_zone(app)
    pane.set_edit_mode(True)
    canvas = pane.digital
    before = len(p.history)
    original = p.blocks[0].rect
    start = canvas.mapFromScene(QPointF(280, 300))
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), start + QPoint(30, 20))
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(30, 20))
    assert len(p.history) == before and p.blocks[0].rect == original
    assert pane.source.block_items["a"].block.rect == original
    assert pane.digital.block_items["a"].block.rect == original


def test_template_preview_cancel_is_atomic_and_apply_preserves_old_text(app, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from chtec.ui.layout import LayoutProposalDialog
    from chtec.zone_templates import ZoneTemplate

    pane, p = pane_with_zone(app)
    template = ZoneTemplate("Form", 0.8, (TextBlock("field", "", (0.6, 0.6, 0.8, 0.8), 90),))
    before = len(p.history)
    monkeypatch.setattr(LayoutProposalDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    pane.apply_template(template)
    assert p.blocks[0].id == "a" and len(p.history) == before
    monkeypatch.setattr(LayoutProposalDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    pane.apply_template(template)
    assert p.blocks[0].id == "field" and p.blocks[0].text == ""
    assert p.unplaced_text == "Word" and len(p.history) == before + 1
    pane.undo()
    assert p.blocks[0].text == "Word" and p.unplaced_text == ""


def test_template_approval_uses_edited_preview_unplaced_text(app, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from chtec.ui.layout import LayoutProposalDialog
    from chtec.zone_templates import ZoneTemplate

    pane, p = pane_with_zone(app)
    template = ZoneTemplate("Form", 0.8, (TextBlock("field", "", (0.6, 0.6, 0.8, 0.8)),))

    def accept(dialog):
        dialog.draft.edit_block("field", "New transcription")
        dialog.pane.unlink("field")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(LayoutProposalDialog, "exec", accept)
    pane.apply_template(template)
    assert "New transcription" in p.unplaced_text and "Word" in p.unplaced_text


def test_add_block_commits_coordinates_from_dialog(app, monkeypatch):
    from PySide6.QtWidgets import QDialog

    pane, p = pane_with_zone(app)
    pane.area = (0.1, 0.1, 0.3, 0.3)
    before = len(p.history)

    def accept(dialog):
        dialog.coordinates["left"].setValue(15)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BlockDialog, "exec", accept)
    pane.add_block()
    assert p.blocks[-1].rect == (0.15, 0.1, 0.3, 0.3)
    assert len(p.history) == before + 1
    pane.undo()
    assert len(p.blocks) == 1


@pytest.mark.parametrize("side", ["source", "digital"])
@pytest.mark.parametrize(
    "rect,handle",
    [
        ((0.999, 0.5, 1, 0.6), "se"),
        ((0, 0.5, 0.001, 0.6), "nw"),
        ((0.5, 0.999, 0.6, 1), "se"),
        ((0.5, 0, 0.6, 0.001), "nw"),
    ],
)
def test_tiny_edge_zone_resize_stays_valid(app, side, rect, handle):
    pane, p = pane_with_zone(app)
    p.replace_blocks([TextBlock("a", "", rect)])
    pane.set_page(p)
    pane.set_edit_mode(True)
    pane.select_ids(["a"])
    canvas = getattr(pane, side)
    corner = canvas.mapFromScene(canvas.handle_points()[handle])
    end = corner + QPoint(30 if handle == "se" else -30, 30 if handle == "se" else -30)
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=corner)
    QTest.mouseMove(canvas.viewport(), end)
    pane.source.block_items["a"].block.validated()
    pane.digital.block_items["a"].block.validated()
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    p.blocks[0].validated()


def test_edit_handles_do_not_paint_over_source_photo(app):
    from PySide6.QtGui import QColor

    from tests.test_layout_ui import coloured_page

    pane = LayoutPane()
    pane.resize(1100, 750)
    pane.set_page(coloured_page())
    pane.show()
    pane.set_edit_mode(True)
    pane.select_ids(["a"])
    app.processEvents()
    center = pane.source.mapFromScene(QPointF(160, 150))
    pixel = pane.source.viewport().grab().toImage().pixelColor(center)
    assert pixel != QColor("white")
