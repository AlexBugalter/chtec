"""Image-free and transcription-free reusable zone geometry."""

import json
import math
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from chtec.domain import AppError
from chtec.layout import TextBlock, validate_blocks
from chtec.spatial import image_size


@dataclass(frozen=True)
class ZoneTemplate:
    name: str
    aspect: float
    zones: tuple[TextBlock, ...]

    def validated(self):
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or len(self.name) > 120
            or type(self.aspect) not in (int, float)
            or not math.isfinite(self.aspect)
            or not 0.01 <= self.aspect <= 100
            or not self.zones
        ):
            raise AppError("Некорректный шаблон зон.")
        validate_blocks(list(self.zones))
        return self

    @classmethod
    def from_page(cls, page, name):
        width, height = image_size(page)
        zones = tuple(
            TextBlock(f"zone-{i}", "", b.rect, b.angle) for i, b in enumerate(page.blocks, 1)
        )
        return cls(name.strip(), width / height, zones).validated()

    def to_data(self):
        self.validated()
        return {
            "format": "chtec-zone-template",
            "version": 1,
            "name": self.name,
            "aspect": self.aspect,
            "zones": [{"id": b.id, "rect": list(b.rect), "angle": b.angle} for b in self.zones],
        }

    def apply(self, page):
        self.validated()
        blocks = [replace(b, text="", verified=False) for b in self.zones]
        page.replace_blocks(blocks, "Применение шаблона " + self.name, unplaced_text=page.text)

    def aspect_differs(self, page):
        width, height = image_size(page)
        return abs(width / height / self.aspect - 1) > 0.1

    def save(self, path):
        path = Path(path)
        temporary = None
        try:
            data = self.to_data()
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        except OSError as exc:
            raise AppError("Не удалось сохранить шаблон зон.") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path):
        try:
            path = Path(path)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                data["format"] != "chtec-zone-template"
                or type(data["version"]) is not int
                or data["version"] != 1
                or set(data) != {"format", "version", "name", "aspect", "zones"}
                or not isinstance(data["zones"], list)
                or len(data["zones"]) > 2000
            ):
                raise ValueError
            zones = []
            for row in data["zones"]:
                if set(row) != {"id", "rect", "angle"}:
                    raise ValueError
                zones.append(TextBlock(row["id"], "", tuple(row["rect"]), row["angle"]))
            return cls(data["name"], data["aspect"], tuple(zones)).validated()
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise AppError("Не удалось прочитать корректный шаблон зон версии1.") from exc
