"""Validated spatial blocks. Rectangles are normalized physical source areas."""

import json
import math
from dataclasses import dataclass

from chtec.domain_errors import AppError


@dataclass(frozen=True)
class TextBlock:
    id: str
    text: str
    rect: tuple[float, float, float, float]
    angle: float = 0
    verified: bool = False

    def validated(self):
        try:
            if not isinstance(self.id, str) or not self.id.strip() or len(self.id) > 128:
                raise ValueError
            if not isinstance(self.text, str) or len(self.text) > 100_000:
                raise ValueError
            if len(self.rect) != 4 or not all(
                type(v) in (int, float) and math.isfinite(v) for v in self.rect
            ):
                raise ValueError
            x0, y0, x1, y1 = self.rect
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ValueError
            if (
                type(self.angle) not in (int, float)
                or not math.isfinite(self.angle)
                or not -360 <= self.angle <= 360
            ):
                raise ValueError
            if type(self.verified) is not bool:
                raise ValueError
            return TextBlock(self.id, self.text, tuple(self.rect), self.angle, self.verified)
        except (ValueError, TypeError) as exc:
            raise AppError("Некорректный текстовый блок: ID, координаты или угол.") from exc

    def geometry(self, width, height):
        x0, y0, x1, y1 = self.rect
        w, h = (x1 - x0) * width, (y1 - y0) * height
        c, s = abs(math.cos(math.radians(self.angle))), abs(math.sin(math.radians(self.angle)))
        determinant = c * c - s * s
        if abs(determinant) > 1e-6:
            recovered_w = (w * c - h * s) / determinant
            recovered_h = (h * c - w * s) / determinant
            if recovered_w > 1e-6 and recovered_h > 1e-6:
                return (x0 + x1) * width / 2, (y0 + y1) * height / 2, recovered_w, recovered_h
        local_w, local_h = (h, w) if s > c else (w, h)
        scale = min(w / (local_w * c + local_h * s), h / (local_w * s + local_h * c))
        return (x0 + x1) * width / 2, (y0 + y1) * height / 2, local_w * scale, local_h * scale

    def corners(self, width, height):
        cx, cy, w, h = self.geometry(width, height)
        c, s = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        return [
            (cx + x * c - y * s, cy + x * s + y * c)
            for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))
        ]


def validate_blocks(blocks):
    if not isinstance(blocks, list) or len(blocks) > 2000:
        raise AppError("Допустимо не более 2000 текстовых блоков на странице.")
    result = [block.validated() for block in blocks]
    if len({b.id for b in result}) != len(result):
        raise AppError("Повторяющиеся ID текстовых блоков.")
    return result


def blocks_from_data(rows):
    try:
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError
        return validate_blocks([TextBlock(**row) for row in rows])
    except (ValueError, TypeError) as exc:
        raise AppError("Некорректный список текстовых блоков.") from exc


def parse_layout(text):
    try:
        text = text.strip()
        if text.startswith("```") and text.endswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)
        blocks = blocks_from_data(data["blocks"])
        if not blocks:
            raise AppError("Модель не выделила текстовые блоки.")
        return blocks
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise AppError(
            "Модель не вернула корректную JSON-разметку. Текущий текст сохранён."
        ) from exc
