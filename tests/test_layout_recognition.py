import io
import json

import pytest
from PIL import Image

from chtec.domain import AppError
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


@pytest.mark.parametrize(('answer', 'expected'), [
    (
        '```json\n[{"bbox":"(58,289),(965,710)","category":"handwritten",'
        '"text":"МЕТРИЧЕСКОЙ КНИГИ"}]\n```',
        'МЕТРИЧЕСКОЙ КНИГИ',
    ),
    ('{"regions":[{"text":"Первая"},{"text":"Вторая"}]}', 'Первая\nВторая'),
    ('{"text":"Строка\\nдругая"}', 'Строка\nдругая'),
    ('Обычный текст\nв две строки', 'Обычный текст\nв две строки'),
    ('[неразборчиво]', '[неразборчиво]'),
])
def test_refine_extracts_transcription_from_structured_response(answer, expected):
    class Model:
        def recognize(self, fragment, instruction):
            return answer

    block = TextBlock('a', '', (0.1, 0.1, 0.5, 0.5))
    result = LayoutRecognitionService(Model()).refine(page(), block)
    assert result == expected


@pytest.mark.parametrize('answer', ['```json\n{"bbox": [1, 2]}\n```', '```json\n{"text":\n```'])
def test_refine_rejects_invalid_structured_response(answer):
    class Model:
        def recognize(self, fragment, instruction):
            return answer

    with pytest.raises(AppError):
        LayoutRecognitionService(Model()).refine(page(), TextBlock('a', '', (0.1, 0.1, 0.5, 0.5)))
