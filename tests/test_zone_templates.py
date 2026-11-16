import pytest

from chtec.domain import AppError
from chtec.layout import TextBlock
from tests.test_layout import page


def marked_page():
    p = page()
    p.replace_blocks(
        [TextBlock("a", "private text", (0.1, 0.2, 0.4, 0.3), 3)], unplaced_text="remainder"
    )
    return p


def test_geometry_change_is_atomic_verified_and_undoable():
    p = marked_page()
    before = len(p.history)
    p.update_block_geometry("a", (0.2, 0.3, 0.5, 0.4), angle=8)
    assert len(p.history) == before + 1
    assert p.blocks[0].verified
    assert p.blocks[0].text == "private text"
    assert p.blocks[0].angle == 8
    assert p.undo()
    assert p.blocks[0].rect == (0.1, 0.2, 0.4, 0.3)
    before = list(p.blocks)
    with pytest.raises(AppError):
        p.update_block_geometry("a", (-1, 0, 0.2, 0.3))
    assert p.blocks == before
    with pytest.raises(AppError):
        p.update_block_geometry("missing", (0.1, 0.1, 0.2, 0.2))


def test_delete_zones_and_unlink_are_explicit_and_undoable():
    p = marked_page()
    p.delete_blocks(["a"])
    assert p.blocks == [] and p.text == "remainder"
    assert p.undo() and p.blocks[0].text == "private text"
    p.delete_blocks(["a"], keep_text=True)
    assert p.blocks == [] and "private text" in p.text
    assert p.undo() and len(p.blocks) == 1


def test_template_is_geometry_only_and_preserves_target_text(tmp_path):
    from chtec.zone_templates import ZoneTemplate

    original = marked_page()
    template = ZoneTemplate.from_page(original, "Metric book")
    path = tmp_path / "form.json"
    template.save(path)
    assert "private text" not in path.read_text()
    assert "image_png" not in path.read_text()
    loaded = ZoneTemplate.load(path)
    target = page()
    target.text = "New page transcript"
    loaded.apply(target)
    assert target.blocks[0].text == ""
    assert target.blocks[0].rect == original.blocks[0].rect
    assert target.blocks[0].angle == 3
    assert not target.blocks[0].verified
    assert target.unplaced_text == "New page transcript"
    assert target.undo() and target.text == "New page transcript" and target.blocks == []


def test_template_validation_rejects_duplicate_ids_and_invalid_bounds(tmp_path):
    import json

    from chtec.zone_templates import ZoneTemplate

    template = ZoneTemplate.from_page(marked_page(), "Form")
    data = template.to_data()
    for zones in ([dict(data["zones"][0], rect=[0, 0, 2, 1])], data["zones"] * 2):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(dict(data, zones=zones)))
        with pytest.raises(AppError):
            ZoneTemplate.load(path)
