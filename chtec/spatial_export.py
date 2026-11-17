import base64
import html
import tempfile
from pathlib import Path

import pymupdf

from chtec.domain import Document, Page
from chtec.spatial import fitted_text, image_size, spatial_font


def page_svg(page):
    width, height = image_size(page)
    elements = []
    for block in page.blocks:
        if block.quad is not None:
            from chtec.quad_render import quad_text_png

            data, (x0, y0, x1, y1) = quad_text_png(block, width, height)
            encoded = base64.b64encode(data).decode("ascii")
            elements.append(
                f'<image data-block="{html.escape(block.id, quote=True)}" '
                f'x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" '
                f'href="data:image/png;base64,{encoded}"><title>'
                f"{html.escape(block.text)}</title></image>"
            )
            continue
        cx, cy, w, h = block.geometry(width, height)
        lines, size = fitted_text(block.text, w, h)
        spans = "".join(
            f'<tspan x="{-w / 2:.4f}" y="{-h / 2 + size * (1 + i * 1.2):.4f}">'
            f"{html.escape(line)}</tspan>"
            for i, line in enumerate(lines)
        )
        elements.append(
            f'<text data-block="{html.escape(block.id, quote=True)}" '
            f'transform="translate({cx:.4f} {cy:.4f}) rotate({block.angle:g})" '
            f'font-family="Arial,DejaVu Sans,sans-serif" font-size="{size:.4f}" '
            f'xml:space="preserve">{spans}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        'style="display:block;width:100%;background:white" role="img" '
        'aria-label="Цифровая страница">' + "".join(elements) + "</svg>"
    )


def write_spatial_pdf(document, path):
    from chtec.formats.exporters import PdfExporter

    result = pymupdf.open()
    font = spatial_font()

    def append_linear(page):
        with tempfile.TemporaryDirectory(prefix="chtec-export-") as folder:
            target = Path(folder) / "text.pdf"
            PdfExporter().write(Document([page]), target)
            with pymupdf.open(target) as source:
                result.insert_pdf(source)

    try:
        for source in document.pages:
            if not source.blocks:
                append_linear(source)
                continue
            width, height = image_size(source)
            scale = min(1, 842 / max(width, height))
            page = result.new_page(width=width * scale, height=height * scale)
            page.insert_font(fontname="spatial", fontbuffer=font.buffer)
            for block in source.blocks:
                if block.quad is not None:
                    from chtec.quad_render import insert_quad_text_layer, quad_text_png

                    data, bounds = quad_text_png(block, width, height)
                    page.insert_image(
                        pymupdf.Rect(*(v * scale for v in bounds)),
                        stream=data,
                        keep_proportion=False,
                    )
                    insert_quad_text_layer(page, block, width, height, scale)
                    continue
                cx, cy, w, h = block.geometry(width, height)
                lines, size = fitted_text(block.text, w, h)
                centre = pymupdf.Point(cx * scale, cy * scale)
                for i, line in enumerate(lines):
                    point = pymupdf.Point(
                        (cx - w / 2) * scale, (cy - h / 2 + size * (1 + i * 1.2)) * scale
                    )
                    page.insert_text(
                        point,
                        line,
                        fontname="spatial",
                        fontsize=size * scale,
                        morph=(centre, pymupdf.Matrix(-block.angle)),
                    )
            if source.unplaced_text:
                append_linear(
                    Page(source.image_png, source.source, source.number, source.unplaced_text)
                )
        result.save(path, garbage=4, deflate=True)
    finally:
        result.close()
