import io
import json

from PIL import Image

from chtec.layout import TextBlock
from chtec.layout_recognition import LayoutRecognitionService
from tests.test_layout import page


def test_layout_model_geometry_is_always_unverified():
    class Model:
        def layout(self, p):
            return json.dumps(
                {
                    "blocks": [
                        {
                            "id": "a",
                            "text": "Иван",
                            "rect": [0.1, 0.2, 0.3, 0.4],
                            "angle": 90,
                            "verified": True,
                        }
                    ]
                }
            )

    result = LayoutRecognitionService(Model()).recognize(page())
    assert not result[0].verified


def test_refine_rotates_crop_but_keeps_source_block_geometry():
    p = page()
    block = TextBlock("v", "Подпись", (0.7, 0.1, 0.8, 0.9), 90)
    p.replace_blocks([block])

    class Model:
        def recognize(self, fragment, instruction):
            with Image.open(io.BytesIO(fragment.image_png)) as image:
                assert image.width > image.height
            return "Дмитрий"

    assert LayoutRecognitionService(Model()).refine(p, block) == "Дмитрий"
    assert p.blocks == [block]
