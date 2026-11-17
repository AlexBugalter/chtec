import io
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image

from chtec.domain import AppError, Document
from chtec.formats import default_registry
from chtec.layout import TextBlock
from chtec.zone_templates import ZoneTemplate
from tests.test_layout import page

QUAD = ((0.2, 0.2), (0.6, 0.25), (0.55, 0.5), (0.25, 0.45))


def quad_block():
    return TextBlock("a", "Текст", (0.2, 0.2, 0.6, 0.5), quad=QUAD).validated()


def test_quad_corners_and_project_history_template_roundtrip(tmp_path):
    block = quad_block()
    assert block.corners(800, 1000) == [(x * 800, y * 1000) for x, y in QUAD]
    p = page()
    p.replace_blocks([block])
    p.update_block_quad("a", ((0.18, 0.21), *QUAD[1:]))
    assert p.blocks[0].rect == (0.18, 0.21, 0.6, 0.5)
    assert p.blocks[0].text == "Текст" and p.blocks[0].verified
    registry = default_registry()
    target = tmp_path / "project.json"
    registry.export_file(Document([p]), target)
    loaded = registry.import_file(target)[0]
    assert loaded.blocks == p.blocks
    assert loaded.undo() and loaded.blocks == [block]
    assert loaded.redo() and loaded.blocks == p.blocks
    template = ZoneTemplate.from_page(p, "Form")
    target = tmp_path / "template.json"
    template.save(target)
    restored = ZoneTemplate.load(target)
    assert restored.zones[0].quad == p.blocks[0].quad
    assert restored.zones[0].text == ""


@pytest.mark.parametrize(
    "quad",
    [
        ((0.2, 0.2), (0.6, 0.5), (0.6, 0.2), (0.2, 0.5)),
        ((0.2, 0.2), (0.6, 0.2), (0.3, 0.25), (0.2, 0.5)),
        ((-0.1, 0.2), *QUAD[1:]),
        ((0.2, 0.2), (0.2, 0.2), *QUAD[2:]),
        ((float("nan"), 0.2), *QUAD[1:]),
    ],
)
def test_invalid_quad_rejected_atomically(quad):
    p = page()
    p.replace_blocks([quad_block()])
    before = (list(p.blocks), list(p.history))
    with pytest.raises(AppError):
        p.update_block_quad("a", quad)
    assert (p.blocks, p.history) == before


def test_quad_crop_masks_neighboring_text():
    from chtec.regions import RegionExtractor

    p = page()
    image = Image.new("RGB", (800, 1000), "black")
    out = io.BytesIO()
    image.save(out, "PNG")
    p.image_png = out.getvalue()
    result = RegionExtractor().extract_block(p, quad_block())
    with Image.open(io.BytesIO(result.image_png)) as crop:
        assert crop.getpixel((0, crop.height - 1)) == (255, 255, 255)
        assert crop.getpixel((crop.width // 2, crop.height // 2)) == (0, 0, 0)


def test_quad_spatial_exports_keep_shape(tmp_path):
    from chtec.spatial_export import page_svg, write_spatial_pdf

    p = page()
    p.replace_blocks([quad_block()])
    svg = page_svg(p)
    assert 'data-block="a"' in svg and "data:image/png;base64," in svg
    target = tmp_path / "quad.pdf"
    write_spatial_pdf(Document([p]), target)
    import pymupdf

    with pymupdf.open(target) as pdf:
        assert pdf[0].get_images()
        assert "Текст" in pdf[0].get_text()


def test_tiny_quad_preserves_fractional_fitting_and_pdf_text(tmp_path):
    import pymupdf

    from chtec.spatial_export import write_spatial_pdf

    p = page()
    block = TextBlock("tiny", "W" * 1000, (0.1, 0.1, 0.11, 0.11)).with_quad(
        ((0.1, 0.1), (0.11, 0.1), (0.11, 0.11), (0.1, 0.11))
    )
    p.replace_blocks([block])
    target = tmp_path / "tiny.pdf"
    write_spatial_pdf(Document([p]), target)
    with pymupdf.open(target) as pdf:
        assert pdf[0].get_text().count("W") == 1000


def test_text_only_edit_keeps_quad():
    p = page()
    p.replace_blocks([quad_block()])
    p.edit_block("a", "Правка")
    assert p.blocks[0].quad == QUAD
    assert replace(p.blocks[0], text="Текст").quad == QUAD


@pytest.mark.parametrize("side", ["source", "digital"])
@pytest.mark.parametrize("index", range(4))
def test_independent_corner_drag_syncs_and_commits_once(side, index):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from chtec.ui.layout import LayoutPane

    app = QApplication.instance() or QApplication([])
    p = page()
    p.replace_blocks([quad_block()])
    pane = LayoutPane()
    pane.resize(1100, 750)
    pane.set_page(p)
    pane.show()
    pane.set_edit_mode(True)
    pane.select_ids(["a"])
    app.processEvents()
    canvas = getattr(pane, side)
    corners = p.blocks[0].corners(*canvas.page_size)
    for actual, expected in zip(canvas.handle_points().values(), corners):
        assert actual == QPointF(*expected)
    start = canvas.mapFromScene(QPointF(*corners[index]))
    end = start + QPoint(-15 if index in (0, 3) else 15, -12 if index < 2 else 12)
    before = len(p.history)
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), end)
    assert len(p.history) == before
    preview = pane.source.block_items["a"].block
    assert all(preview.quad[i] == QUAD[i] for i in range(4) if i != index)
    assert preview.quad[index] != QUAD[index]
    assert preview == pane.digital.block_items["a"].block
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert len(p.history) == before + 1 and p.blocks[0].quad == preview.quad
    assert p.undo() and p.blocks[0].quad == QUAD
    pane.close()
    pane.deleteLater()


def test_quad_properties_text_edit_preserves_precise_vertices():
    from PySide6.QtWidgets import QApplication

    from chtec.ui.layout import BlockDialog, LayoutPane

    app = QApplication.instance() or QApplication([])
    block = quad_block().with_quad(((0.213456789, 0.2), *QUAD[1:]))
    dialog = BlockDialog(block)
    assert dialog.rect_value() == block.rect
    assert not dialog.angle.isEnabled()
    p = page()
    p.replace_blocks([block])
    pane = LayoutPane()
    pane.set_page(p)
    pane.change_block("a", "Edited", dialog.angle.value(), dialog.rect_value())
    assert p.blocks[0].quad == block.quad
    pane.close()
    dialog.close()
    app.processEvents()


def test_quad_redo_cleared_by_new_edit():
    p = page()
    p.replace_blocks([quad_block()])
    p.update_block_quad("a", ((0.18, 0.21), *QUAD[1:]))
    assert p.undo()
    p.edit_block("a", "new text")
    assert not p.redo()


def test_rotated_legacy_handles_escape_invalid_drag_and_click():
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from chtec.ui.layout import LayoutPane

    app = QApplication.instance() or QApplication([])
    original = TextBlock("a", "Original text", (.2, .2, .6, .5), 24)
    p = page()
    p.replace_blocks([original])
    pane = LayoutPane()
    pane.resize(1100, 750)
    pane.set_page(p)
    pane.show()
    pane.set_edit_mode(True)
    pane.select_ids(["a"])
    app.processEvents()
    canvas = pane.source
    assert list(canvas.handle_points().values()) == [
        QPointF(*point) for point in original.corners(*canvas.page_size)]
    before = len(p.history)
    start = canvas.mapFromScene(canvas.handle_points()["nw"])
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert len(p.history) == before and p.blocks == [original]
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), start+QPoint(-15, -12))
    assert pane.digital.block_items["a"].block.quad is not None
    QTest.keyClick(canvas, Qt.Key.Key_Escape)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert pane.digital.block_items["a"].block == original and len(p.history) == before
    end = canvas.mapFromScene(canvas.handle_points()["se"])+QPoint(15, 15)
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas.viewport(), end)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert len(p.history) == before and p.blocks == [original]
    pane.close()
    pane.deleteLater()


def test_perspective_coefficients_map_all_four_corners():
    from chtec.quad_render import perspective_coefficients

    destination = [(x * 800, y * 1000) for x, y in QUAD]
    source = [(0, 0), (300, 0), (300, 200), (0, 200)]
    a, b, c, d, e, f, g, h = perspective_coefficients(destination, source)
    for (x, y), expected in zip(destination, source):
        assert (
            (a * x + b * y + c) / (g * x + h * y + 1),
            (d * x + e * y + f) / (g * x + h * y + 1),
        ) == pytest.approx(expected)
