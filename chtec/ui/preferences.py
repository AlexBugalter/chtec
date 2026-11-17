"Appearance settings are independent of connection profiles and documents."

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from chtec.domain_errors import AppError


@dataclass
class UiPreferences:
    theme: str = "light"
    language: str = "ru"
    scale: int = 100
    pages_visible: bool = True
    assistant_visible: bool = True
    mode: int = 0
    focus: str = "both"
    ocr_mode: str = "layout"
    sizes: dict = field(default_factory=dict)

    def validated(self):
        if (
            self.theme not in ("light", "dark")
            or self.language not in ("ru", "en")
            or type(self.scale) is not int
            or not 80 <= self.scale <= 200
            or type(self.mode) is not int
            or self.mode not in (0, 1)
            or self.focus not in ("both", "source", "digital")
            or self.ocr_mode not in ("layout", "page", "all")
            or type(self.pages_visible) is not bool
            or type(self.assistant_visible) is not bool
            or not isinstance(self.sizes, dict)
        ):
            raise AppError("Некорректные настройки интерфейса.")
        for key, values in self.sizes.items():
            if (
                key not in ("workspace", "text", "layout", "assistant")
                or not isinstance(values, list)
                or not 2 <= len(values) <= 3
                or any(type(v) is not int or not 0 <= v <= 100000 for v in values)
                or sum(values) <= 0
            ):
                raise AppError("Некорректные размеры панелей.")
        return self


class UiPreferencesStore:
    def __init__(self, path):
        self.path = Path(path)
        self.warning = ""

    def load(self):
        self.warning = ""
        if not self.path.exists():
            return UiPreferences()
        try:
            if self.path.stat().st_size > 65536:
                raise ValueError("size")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return UiPreferences(**data).validated()
        except (OSError, ValueError, TypeError, AppError):
            self.warning = "Настройки интерфейса сброшены. Настройки ИИ сохранены."
            return UiPreferences()

    def save(self, preferences):
        preferences.validated()
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(preferences), stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        except OSError as exc:
            raise AppError("Не удалось сохранить настройки интерфейса.") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
