import json
from threading import Event

import pytest

from chtec.domain import AppError
from chtec.layout import TextBlock
from chtec.layout_recognition import LayoutRecognitionService
from tests.test_layout import page


def test_quad_geometry_respects_rotation_and_normalized_physical_bounds():
    from chtec.zone_detection import block_from_quad

    block = block_from_quad("a", [(800, 100), (820, 800), (780, 800), (760, 100)], 1000, 1000)
    assert block.rect == (0.76, 0.1, 0.82, 0.8)
    assert 85 < block.angle < 95
    assert not block.verified and block.text == ""
    with pytest.raises(AppError):
        block_from_quad("bad", [(0, 0)] * 4, 1000, 1000)


def test_column_rule_splits_cross_column_text_box():
    from chtec.zone_detection import split_on_rules

    regions = [TextBlock("line", "", (0.1, 0.2, 0.9, 0.25), 0)]
    result = split_on_rules(regions, [(0.5, 0.1, 0.5, 0.9)])
    assert len(result) == 2
    assert result[0].rect[2] == 0.5 and result[1].rect[0] == 0.5


def test_line_grouping_keeps_columns_and_vertical_notes_separate():
    from chtec.zone_detection import group_lines

    blocks = [
        TextBlock("a", "", (0.1, 0.2, 0.4, 0.23)),
        TextBlock("b", "", (0.1, 0.24, 0.4, 0.27)),
        TextBlock("c", "", (0.6, 0.24, 0.9, 0.27)),
        TextBlock("v", "", (0.92, 0.2, 0.95, 0.8), 90),
    ]
    result = group_lines(blocks, [(0.5, 0, 0.5, 1)])
    assert len(result) == 3
    assert any(b.rect == (0.1, 0.2, 0.4, 0.27) for b in result)
    assert any(b.angle == 90 for b in result)


def test_detector_reading_uses_fixed_geometry_and_exact_ids():
    zones = [
        TextBlock("a", "", (0.1, 0.1, 0.4, 0.2), 5),
        TextBlock("b", "", (0.6, 0.5, 0.8, 0.7), 0),
    ]

    class Detector:
        def detect(self, p, cancel=None, progress=None):
            return zones

    class Model:
        def read_regions(self, image_png, ids):
            assert image_png.startswith(b"\x89PNG") and ids == ["a", "b"]
            return json.dumps(
                {"regions": [{"id": "b", "text": "Second"}, {"id": "a", "text": "First"}]}
            )

    p = page()
    result = LayoutRecognitionService(Model(), detector=Detector()).recognize(p)
    assert [b.text for b in result] == ["First", "Second"]
    assert [b.rect for b in result] == [b.rect for b in zones]
    assert result[0].angle == 5 and p.blocks == []


def test_cancel_before_detection_does_not_call_model():
    class Detector:
        def detect(self, *a, **k):
            pytest.fail("cancelled before detector")

    stop = Event()
    stop.set()
    with pytest.raises(AppError, match="отмен"):
        LayoutRecognitionService(object(), detector=Detector()).recognize(page(), cancel=stop)


@pytest.mark.parametrize(
    "answer",
    [
        {"regions": []},
        {"regions": [{"id": "unknown", "text": "x"}]},
        {"regions": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]},
    ],
)
def test_missing_or_duplicate_crop_ids_do_not_replace_text(answer):
    class Detector:
        def detect(self, *a, **k):
            return [TextBlock("a", "", (0.1, 0.1, 0.4, 0.2))]

    class Model:
        def read_regions(self, *a):
            return json.dumps(answer)

    p = page()
    before = p.text
    with pytest.raises(AppError):
        LayoutRecognitionService(Model(), detector=Detector()).recognize(p)
    assert p.text == before and p.blocks == []


def test_read_existing_template_geometry_without_redetecting():
    class Detector:
        def detect(self, *args, **kwargs):
            pytest.fail("Existing edited zones must be used")

    class Model:
        def read_regions(self, image, ids):
            return json.dumps({"regions": [{"id": i, "text": "Read"} for i in ids]})

    p = page()
    p.replace_blocks([TextBlock("field", "", (0.1, 0.1, 0.4, 0.2), 90)])
    result = LayoutRecognitionService(Model(), Detector()).recognize(p)
    assert result[0].rect == p.blocks[0].rect and result[0].angle == 90
    assert result[0].text == "Read" and p.blocks[0].text == ""


def test_detector_handles_current_opencv_flat_hough_shape(monkeypatch):
    import cv2
    import numpy as np
    import rapidocr

    from chtec.zone_detection import TextZoneDetector

    class Output:
        boxes = None

    monkeypatch.setattr(rapidocr, "RapidOCR", lambda **kwargs: lambda *args, **kw: Output())
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kw: np.array([[400, 0, 400, 900]]))
    assert TextZoneDetector().detect(page()) == []


def test_detector_table_cuts_are_valid_domain_python_numbers(monkeypatch):
    import cv2
    import numpy as np
    import rapidocr

    from chtec.zone_detection import TextZoneDetector

    class Output:
        boxes = np.array([[[100, 200], [700, 200], [700, 240], [100, 240]]], dtype=np.float32)

    monkeypatch.setattr(rapidocr, "RapidOCR", lambda **kwargs: lambda *args, **kw: Output())
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kw: np.array([[400, 0, 400, 900]]))
    blocks = TextZoneDetector().detect(page())
    assert blocks
    for block in blocks:
        block.validated()
