import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from chtec.domain import (
    MAX_FILE_BYTES,
    MAX_PAGES,
    AppError,
    Document,
    Page,
    validate_image_budget,
)


class Importer(ABC):
    extensions: tuple[str, ...]
    label: str

    def page_count(self, path: Path) -> int | None:
        """Optional cheap preflight; plugins with multi-page data should implement it."""
        return None

    @abstractmethod
    def read(self, path: Path) -> list[Page]: ...


class Exporter(ABC):
    extensions: tuple[str, ...]
    label: str

    @abstractmethod
    def write(self, document: Document, path: Path) -> None: ...


class FormatRegistry:
    def __init__(self):
        self.importers: dict[str, Importer] = {}
        self.exporters: dict[str, Exporter] = {}

    def register_importer(self, importer: Importer) -> None:
        self._register(self.importers, importer)

    def register_exporter(self, exporter: Exporter) -> None:
        self._register(self.exporters, exporter)

    @staticmethod
    def _register(mapping, handler):
        for extension in handler.extensions:
            if not extension.startswith(".") or extension != extension.lower():
                raise ValueError("Расширение должно начинаться с точки и быть в нижнем регистре.")
            if extension in mapping:
                raise ValueError(f"Формат {extension} уже зарегистрирован.")
        mapping.update(dict.fromkeys(handler.extensions, handler))

    def import_file(self, path: Path, max_pages: int = MAX_PAGES) -> list[Page]:
        path = Path(path)
        try:
            if max_pages <= 0:
                raise AppError(f"В документе может быть не более {MAX_PAGES} страниц.")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise AppError("Файл превышает лимит 100 MiB. Разделите его на части.")
            importer = self.importers.get(path.suffix.lower())
            if importer is None:
                raise AppError(f"Формат {path.suffix} не поддерживается. Установите расширение.")
            count = importer.page_count(path)
            if count is not None and not 0 < count <= min(max_pages, MAX_PAGES):
                raise AppError(
                    f"Превышен лимит {MAX_PAGES} страниц. "
                    f"Можно добавить страниц: {min(max_pages, MAX_PAGES)}."
                )
            pages = importer.read(path)
            if not pages or len(pages) > min(max_pages, MAX_PAGES):
                raise AppError(f"Допустимо от 1 до {MAX_PAGES} страниц.")
            validate_image_budget(pages)
            return pages
        except AppError:
            raise
        except Exception as exc:
            raise AppError(f"Не удалось прочитать «{path.name}»: {exc}") from exc

    def export_file(self, document: Document, path: Path) -> None:
        path = Path(path)
        exporter = self.exporters.get(path.suffix.lower())
        if exporter is None:
            raise AppError(f"Экспорт {path.suffix} не поддерживается.")
        if not document.pages:
            raise AppError("Сначала добавьте страницы.")
        for page in document.pages:
            if page.source_path and Path(page.source_path).resolve() == path.resolve():
                raise AppError("Нельзя заменить оригинал. Выберите другое имя выходного файла.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".chtec-", suffix=path.suffix, delete=False
            ) as stream:
                temporary = Path(stream.name)
            exporter.write(document, temporary)
            os.replace(temporary, path)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(f"Не удалось сохранить файл: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
