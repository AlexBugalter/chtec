import io
import json
from dataclasses import asdict

import pytest
from PIL import Image

from chtec.domain import AppError, Document, Page
from chtec.formats import default_registry
from chtec.layout import TextBlock, parse_layout


def page():
    data = io.BytesIO()
    Image.new("RGB", (800, 1000), "white").save(data, "PNG")
    return Page(data.getvalue(), "scan.png", 1, "старый текст")


def test_layout_edit_and_undo_restore_text_and_geometry():
    p = page()
    blocks = [
        TextBlock("a", "Дария", (0.1, 0.2, 0.5, 0.3)),
        TextBlock("b", "Дария", (0.7, 0.2, 0.8, 0.8), 90),
    ]
    p.replace_blocks(blocks)
    assert p.text == "Дария\n\nДария"
    p.edit_block("b", "Мария")
    assert p.blocks[0].text == "Дария"
    assert p.blocks[1].text == "Мария"
    assert p.undo()
    assert p.blocks == blocks
    assert p.undo()
    assert p.text == "старый текст"
    assert p.blocks == []


@pytest.mark.parametrize(
    "rect,angle",
    [
        ((-0.1, 0, 0.5, 0.5), 0),
        ((0, 0, 0, 0.5), 0),
        ((0, 0, 2, 1), 0),
        ((0, 0, 0.5, 0.5), float("nan")),
    ],
)
def test_invalid_geometry_is_rejected(rect, angle):
    with pytest.raises(AppError):
        TextBlock("a", "x", rect, angle).validated()


def test_rotated_block_stays_inside_source_area():
    block = TextBlock("vertical", "Подпись", (0.7, 0.1, 0.8, 0.9), 90).validated()
    corners = block.corners(800, 1000)
    assert min(x for x, y in corners) == pytest.approx(560)
    assert max(y for x, y in corners) == pytest.approx(900)
    assert block.geometry(800, 1000)[2:] == pytest.approx((800, 80))


def test_oblique_text_uses_local_dimensions_recovered_from_source_bounds():
    import math

    c, s = math.cos(math.radians(15)), math.sin(math.radians(15))
    w, h = 300 * c + 40 * s, 300 * s + 40 * c
    block = TextBlock(
        "angle",
        "Подпись",
        ((500 - w / 2) / 1000, (500 - h / 2) / 1000, (500 + w / 2) / 1000, (500 + h / 2) / 1000),
        15,
    )
    assert block.geometry(1000, 1000)[2:] == pytest.approx((300, 40))


def test_structured_layout_strictly_validates_ids_and_coordinates():
    data = {"blocks": [asdict(TextBlock("a", "Дария", (0.1, 0.2, 0.5, 0.3)))]}
    assert parse_layout(json.dumps(data))[0].text == "Дария"
    data["blocks"] *= 2
    with pytest.raises(AppError, match="ID"):
        parse_layout(json.dumps(data))


def test_legacy_v1_import_stays_editable(tmp_path):
    import base64

    p = page()
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "format": "chtec",
                "version": 1,
                "pages": [
                    {
                        "image_png": base64.b64encode(p.image_png).decode(),
                        "source": "old",
                        "number": 1,
                        "text": "Старый текст",
                        "history": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = default_registry().import_file(path)[0]
    assert loaded.blocks == []
    loaded.replace_text("Ручная правка")
    assert loaded.text == "Ручная правка"


def test_project_rejects_history_inconsistent_with_geometry(tmp_path):
    p = page()
    p.replace_blocks([TextBlock("a", "Дария", (0.1, 0.2, 0.5, 0.3))])
    p.edit_block("a", "Мария")
    path = tmp_path / "project.json"
    default_registry().export_file(Document([p]), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["pages"][0]["history"][-1]["text"] = "Подмена"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AppError, match="История"):
        default_registry().import_file(path)


def test_project_layout_roundtrip_and_history(tmp_path):
    p = page()
    p.replace_blocks([TextBlock("a", "Подпись", (0.7, 0.1, 0.8, 0.9), 90)])
    p.edit_block("a", "Иван")
    path = tmp_path / "project.json"
    registry = default_registry()
    registry.export_file(Document([p]), path)
    restored = registry.import_file(path)[0]
    assert restored.blocks == p.blocks
    assert restored.text == "Иван"
    restored.undo()
    assert restored.blocks[0].text == "Подпись"


def test_plain_replacement_cannot_silently_destroy_layout():
    p = page()
    p.replace_blocks([TextBlock("a", "Дария", (0.1, 0.2, 0.5, 0.3))])
    with pytest.raises(AppError, match="блок"):
        p.replace_text("другой текст")


def test_text_edit_never_confirms_model_geometry():
    p = page()
    p.replace_blocks([TextBlock("a", "Дария", (0.1, 0.2, 0.5, 0.3), verified=False)])
    p.edit_block("a", "Мария")
    assert not p.blocks[0].verified


def test_spatial_export_preserves_rotation_unicode_and_unplaced_text(tmp_path):
    import pymupdf

    p = page()
    p.replace_blocks(
        [
            TextBlock("v", "Дария", (0.7, 0.1, 0.8, 0.9), 90),
            TextBlock("h", "1883", (0.1, 0.2, 0.4, 0.3)),
        ],
        unplaced_text="Без привязки",
    )
    doc = Document([p])
    html_path = tmp_path / "page.html"
    pdf_path = tmp_path / "page.pdf"
    default_registry().export_file(doc, html_path)
    content = html_path.read_text(encoding="utf-8")
    assert "rotate(90" in content
    assert "Дария" in content
    default_registry().export_file(doc, pdf_path)
    with pymupdf.open(pdf_path) as pdf:
        assert len(pdf) == 2
        assert "Дария" in pdf[0].get_text()
        assert "1883" in pdf[0].get_text()
        assert "Без привязки" in pdf[1].get_text()
        lines = [
            line
            for block in pdf[0].get_text("dict")["blocks"]
            if "lines" in block
            for line in block["lines"]
        ]
        vertical = next(line for line in lines if any("Дария" in s["text"] for s in line["spans"]))
        assert vertical["dir"][0] == pytest.approx(0, abs=0.01)
        assert vertical["dir"][1] > 0.9
