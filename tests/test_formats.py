import io
import json
from pathlib import Path

import pymupdf
import pytest
from docx import Document as WordDocument
from PIL import Image

from chtec.domain import AppError, Document, Page
from chtec.formats import default_registry
from chtec.formats.base import Exporter


def png(color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def document():
    return Document(pages=[Page(png(), "рукопись.png", 1, "Привет, мир!\nВторая строка.")])


def test_tiff_imports_all_frames(tmp_path):
    path = tmp_path / "pages.TIFF"
    Image.new("RGB", (80, 120), "red").save(
        path, save_all=True, append_images=[Image.new("RGB", (80, 120), "blue")]
    )
    pages = default_registry().import_file(path)
    assert len(pages) == 2
    assert pages[1].number == 2
    assert Image.open(io.BytesIO(pages[0].image_png)).getpixel((0, 0)) == (255, 0, 0)


def test_pdf_import_renders_every_page(tmp_path):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page(width=10000, height=100)
        pdf.save(path)
    pages = default_registry().import_file(path)
    assert len(pages) == 2
    assert max(Image.open(io.BytesIO(pages[1].image_png)).size) <= 2400


def test_json_roundtrip_preserves_images_text_and_history(tmp_path, document):
    document.pages[0].replace_text("Исправленный текст")
    path = tmp_path / "project.json"
    registry = default_registry()
    registry.export_file(document, path)
    restored = registry.import_file(path)
    assert restored[0].text == "Исправленный текст"
    assert restored[0].image_png == document.pages[0].image_png
    assert restored[0].history[-1].text == "Привет, мир!\nВторая строка."


@pytest.mark.parametrize("suffix", ["txt", "md", "html", "docx", "pdf"])
def test_unicode_export(tmp_path, document, suffix):
    path = tmp_path / f"result.{suffix}"
    default_registry().export_file(document, path)
    if suffix == "pdf":
        with pymupdf.open(path) as pdf:
            text = "".join(page.get_text() for page in pdf)
            assert len(pdf) >= 1
    elif suffix == "docx":
        text = "\n".join(p.text for p in WordDocument(path).paragraphs)
    else:
        text = path.read_text(encoding="utf-8")
    assert "Привет, мир!" in text


def test_html_escapes_user_text(tmp_path, document):
    document.pages[0].text = '<script>alert("x")</script>'
    path = tmp_path / "result.html"
    default_registry().export_file(document, path)
    assert "<script>" not in path.read_text(encoding="utf-8")


def test_invalid_image_is_actionable(tmp_path):
    path = tmp_path / "bad.png"
    path.write_bytes(b"not an image")
    with pytest.raises(AppError, match="прочитать"):
        default_registry().import_file(path)


def test_encrypted_pdf_is_rejected(tmp_path):
    path = tmp_path / "locked.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret")
    with pytest.raises(AppError, match="парол"):
        default_registry().import_file(path)


def test_page_limit(tmp_path):
    path = tmp_path / "many.pdf"
    with pymupdf.open() as pdf:
        for _ in range(201):
            pdf.new_page(width=10, height=10)
        pdf.save(path)
    with pytest.raises(AppError, match="200"):
        default_registry().import_file(path)


def test_invalid_project_does_not_restore_arbitrary_data(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"version": 1, "pages": [{"image_png": "nonsense"}]}))
    with pytest.raises(AppError):
        default_registry().import_file(path)


def test_failed_export_preserves_previous_file(tmp_path, document):
    class BrokenExporter(Exporter):
        extensions = (".broken",)
        label = "Broken"

        def write(self, document: Document, path: Path):
            path.write_text("partial")
            raise AppError("Disk failure")

    registry = default_registry()
    registry.register_exporter(BrokenExporter())
    path = tmp_path / "previous.broken"
    path.write_text("original")
    with pytest.raises(AppError):
        registry.export_file(document, path)
    assert path.read_text() == "original"


def test_long_pdf_does_not_clip_text(tmp_path, document):
    document.pages[0].text = "Строка рукописи.\n" * 400 + "ФИНАЛЬНЫЙ МАРКЕР"
    path = tmp_path / "long.pdf"
    default_registry().export_file(document, path)
    with pymupdf.open(path) as pdf:
        assert len(pdf) > 1
        assert "ФИНАЛЬНЫЙ МАРКЕР" in "".join(p.get_text() for p in pdf)


def test_undo_restores_manual_edits(document):
    page = document.pages[0]
    previous = page.text
    page.replace_text("Новый текст")
    assert page.undo()
    assert page.text == previous


def test_export_cannot_overwrite_original_scan(tmp_path):
    path = tmp_path / "original.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    original = path.read_bytes()
    registry = default_registry()
    document = Document(registry.import_file(path))
    with pytest.raises(AppError, match="оригинал"):
        registry.export_file(document, path)
    assert path.read_bytes() == original


def test_remaining_page_budget_is_checked_before_pdf_render(tmp_path, monkeypatch):
    path = tmp_path / "two.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(path)

    def unexpected_render(*args, **kwargs):
        pytest.fail("Page limit must be checked before rendering")

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", unexpected_render)
    with pytest.raises(AppError, match="страниц"):
        default_registry().import_file(path, max_pages=1)


def test_document_image_budget_does_not_mutate_existing_document(monkeypatch):
    from chtec import domain

    monkeypatch.setattr(domain, "MAX_DOCUMENT_IMAGE_BYTES", 5)
    first = Page(b"abc", "first", 1)
    document = Document([first])
    with pytest.raises(AppError, match="60 MiB"):
        document.append([Page(b"def", "second", 1)])
    assert document.pages == [first]
