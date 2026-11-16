import base64
import html
import json
from dataclasses import asdict
from pathlib import Path

import pymupdf
from docx import Document as WordDocument
from docx.shared import Pt

from chtec.domain import MAX_FILE_BYTES, AppError, Document
from chtec.formats.base import Exporter


def document_html(document: Document) -> str:
    from chtec.spatial_export import page_svg

    sections = []
    for page in document.pages:
        if page.blocks:
            sections.append("<section>" + page_svg(page) + "</section>")
            if page.unplaced_text:
                sections.append(
                    "<section><p>Текст без привязки к области</p><pre>"
                    + html.escape(page.unplaced_text)
                    + "</pre></section>"
                )
        else:
            sections.append(f"<section><pre>{html.escape(page.text)}</pre></section>")
    sections = "\n".join(sections)
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f"<title>{html.escape(document.title)}</title>"
        "<style>body{max-width:760px;margin:48px auto;padding:0 24px;color:#24292e;}"
        "pre{font:12pt/1.65 sans-serif;white-space:pre-wrap;overflow-wrap:anywhere;}"
        "section+section{break-before:page;margin-top:32px;}</style>"
        f"</head><body>{sections}</body></html>"
    )


class TextExporter(Exporter):
    extensions = (".txt", ".md")
    label = "Текст / Markdown"

    def write(self, document: Document, path: Path) -> None:
        path.write_text(document.text, encoding="utf-8")


class HtmlExporter(Exporter):
    extensions = (".html",)
    label = "HTML"

    def write(self, document: Document, path: Path) -> None:
        path.write_text(document_html(document), encoding="utf-8")


class ProjectExporter(Exporter):
    extensions = (".json",)
    label = "Проект Чтец (оригиналы, текст, история)"

    def write(self, document: Document, path: Path) -> None:
        pages = []
        for page in document.pages:
            row = asdict(page)
            row["image_png"] = base64.b64encode(page.image_png).decode("ascii")
            pages.append(row)
        payload = json.dumps(
            {"format": "chtec", "version": 2, "title": document.title, "pages": pages},
            ensure_ascii=False,
        ).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise AppError("Проект превышает 100 MiB. Сохраните страницы отдельными проектами.")
        path.write_bytes(payload)


class DocxExporter(Exporter):
    extensions = (".docx",)
    label = "Word"

    def write(self, document: Document, path: Path) -> None:
        word = WordDocument()
        style = word.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(12)
        for index, page in enumerate(document.pages):
            if index:
                word.add_page_break()
            for line in page.text.split("\n"):
                word.add_paragraph(line)
        word.save(path)


class PdfExporter(Exporter):
    extensions = (".pdf",)
    label = "PDF (с расположением размеченных блоков)"

    def write(self, document: Document, path: Path) -> None:
        if any(p.blocks for p in document.pages):
            from chtec.spatial_export import write_spatial_pdf

            write_spatial_pdf(document, path)
            return
        # Story paginates all text and embeds local fallback fonts for Unicode.
        content = document_html(document)
        story = pymupdf.Story(html=content)
        writer = pymupdf.DocumentWriter(str(path))
        try:
            more = True
            while more:
                device = writer.begin_page(pymupdf.paper_rect("a4"))
                more, _ = story.place(pymupdf.Rect(48, 48, 547, 794))
                story.draw(device)
                writer.end_page()
        finally:
            writer.close()
