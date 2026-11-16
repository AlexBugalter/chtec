import base64
import io
import json
import warnings
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from chtec.domain import (
    MAX_IMAGE_SIDE,
    MAX_PAGES,
    MAX_PIXELS,
    AppError,
    Page,
    Revision,
    validate_image_budget,
)
from chtec.formats.base import Importer


def normalize_image(image: Image.Image) -> bytes:
    if image.width * image.height > MAX_PIXELS:
        raise AppError("Изображение превышает 40 млн пикселей. Уменьшите разрешение.")
    image = ImageOps.exif_transpose(image)
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    if "A" in image.getbands() or "transparency" in image.info:
        rgba = image.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, "white")
        rgb.paste(rgba, mask=rgba.getchannel("A"))
    else:
        rgb = image.convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer, format="PNG")
    return buffer.getvalue()


class ImageImporter(Importer):
    extensions = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif")
    label = "Изображения"

    def page_count(self, path: Path) -> int:
        with Image.open(path) as image:
            return getattr(image, "n_frames", 1)

    def __init__(self):
        # Pillow's AVIF decoder and an optional HEIF plugin extend this handler.
        try:
            import pillow_heif
        except ImportError:
            pass
        else:
            pillow_heif.register_heif_opener()
        Image.init()
        extra = tuple(
            suffix
            for suffix in (".avif", ".heic", ".heif")
            if suffix in Image.registered_extensions()
        )
        self.extensions = (*type(self).extensions, *extra)

    def read(self, path: Path) -> list[Page]:
        pages = []
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                count = getattr(image, "n_frames", 1)
                if count > MAX_PAGES:
                    raise AppError(f"Допустимо не более {MAX_PAGES} страниц.")
                for index in range(count):
                    image.seek(index)
                    pages.append(
                        Page(
                            normalize_image(image),
                            path.name,
                            index + 1,
                            source_path=str(path.resolve()),
                        )
                    )
                    validate_image_budget(pages)
        return pages


class PdfImporter(Importer):
    extensions = (".pdf",)
    label = "PDF"

    def page_count(self, path: Path) -> int:
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise AppError("PDF защищен паролем. Сохраните незашифрованную копию.")
            return len(pdf)

    def read(self, path: Path) -> list[Page]:
        pages = []
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise AppError("PDF защищен паролем. Сохраните незашифрованную копию.")
            if not 0 < len(pdf) <= MAX_PAGES:
                raise AppError(f"Допустимо от 1 до {MAX_PAGES} страниц.")
            for index, page in enumerate(pdf):
                side = max(page.rect.width, page.rect.height)
                if side <= 0:
                    raise AppError("PDF содержит страницу с неверным размером.")
                scale = min(2.5, (MAX_IMAGE_SIDE - 1) / side)
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                pages.append(
                    Page(
                        pixmap.tobytes("png"), path.name, index + 1, source_path=str(path.resolve())
                    )
                )
                validate_image_budget(pages)
        return pages


class ProjectImporter(Importer):
    extensions = (".json",)
    label = "Проект Чтец"

    def read(self, path: Path) -> list[Page]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != "chtec" or data.get("version") not in (1, 2):
                raise ValueError("Неизвестный формат проекта")
            rows = data["pages"]
            if not isinstance(rows, list) or not 0 < len(rows) <= MAX_PAGES:
                raise ValueError(f"Допустимо от 1 до {MAX_PAGES} страниц")
            pages = []
            for row in rows:
                image_png = base64.b64decode(row["image_png"], validate=True)
                with Image.open(io.BytesIO(image_png)) as image:
                    if image.format != "PNG":
                        raise ValueError("Изображение проекта должно быть PNG")
                    if max(image.size) > MAX_IMAGE_SIDE:
                        raise ValueError("Слишком большое изображение проекта")
                    image.verify()
                text, source, number = row["text"], row["source"], row["number"]
                if not isinstance(text, str) or not isinstance(source, str):
                    raise ValueError("Неверный текст страницы")
                if type(number) is not int or number < 1:
                    raise ValueError("Неверный номер страницы")
                history = []
                for item in row.get("history", [])[-50:]:
                    if not all(
                        isinstance(item.get(k), str) for k in ("text", "reason", "timestamp")
                    ):
                        raise ValueError("Неверная история изменений")
                    from chtec.layout import blocks_from_data

                    revision = dict(item)
                    revision["blocks"] = blocks_from_data(revision.get("blocks", []))
                    unplaced = revision.get("unplaced_text", "")
                    if not isinstance(unplaced, str):
                        raise ValueError("Некорректный текст истории")
                    if (revision["blocks"] or unplaced) and revision["text"] != "\n\n".join(
                        [b.text for b in revision["blocks"]] + ([unplaced] if unplaced else [])
                    ):
                        raise ValueError("История не согласована с блоками")
                    history.append(Revision(**revision))
                source_path = row.get("source_path", "")
                if not isinstance(source_path, str):
                    raise ValueError("Неверный путь оригинала")
                from chtec.layout import blocks_from_data

                blocks = blocks_from_data(row.get("blocks", []))
                unplaced = row.get("unplaced_text", "")
                if not isinstance(unplaced, str):
                    raise ValueError("Некорректный непривязанный текст")
                if (blocks or unplaced) and text != "\n\n".join(
                    [b.text for b in blocks] + ([unplaced] if unplaced else [])
                ):
                    raise ValueError("Текст не согласован с разметкой")
                pages.append(
                    Page(image_png, source, number, text, history, source_path, blocks, unplaced)
                )
                validate_image_budget(pages)
            return pages
        except (KeyError, ValueError, TypeError, AttributeError, OSError) as exc:
            raise AppError(f"Не удалось прочитать проект: {exc}") from exc
