import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from chtec.ui.layout import LayoutPane
from tests.test_layout import page


@pytest.mark.parametrize("editing", [False, True])
def test_drawing_overlay_is_not_owned_or_deleted_by_scene(editing):
    app = QApplication.instance() or QApplication([])
    pane = LayoutPane()
    pane.resize(1100, 750)
    original = page()
    pane.set_page(original)
    pane.show()
    pane.set_edit_mode(editing)
    app.processEvents()
    canvas = pane.source
    scene_items = list(canvas.scene().items())
    start = canvas.mapFromScene(QPointF(100, 120))
    end = canvas.mapFromScene(QPointF(350, 240))
    pane.start_new_zone()
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), end)
    # The transient rubber band must not enter the native scene lifetime/index.
    assert canvas.scene().items() == scene_items
    assert not canvas._area_rect.isEmpty()
    canvas.viewport().grab()
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    app.processEvents()
    assert len(original.blocks) == 1
    assert canvas._area_rect is None
    assert len(canvas.scene().items()) == 1  # Only the permanent BlockItem.
    assert pane.digital.selected_ids == pane.source.selected_ids == [original.blocks[0].id]
    pane.close()
    pane.deleteLater()


def test_cancel_pending_drawing_by_changing_page_does_not_keep_native_items():
    app = QApplication.instance() or QApplication([])
    pane = LayoutPane()
    pane.resize(1100, 750)
    first, second = page(), page()
    pane.set_page(first)
    pane.show()
    app.processEvents()
    canvas = pane.source
    pane.start_new_zone()
    start = canvas.mapFromScene(QPointF(100, 120))
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    pane.set_page(second)
    assert canvas._area_rect is None and canvas.anchor is None
    assert not first.blocks and not second.blocks
    pane.close()
    pane.deleteLater()
