"""Perspective rendering for quadrilateral spatial exports (without a Qt app)."""

import io
import math

import pymupdf
from PIL import Image

from chtec.spatial import fitted_text, spatial_font


def perspective_coefficients(destination, source):
    # Solve the eight projective coefficients mapping output pixels to source pixels.
    rows = []
    values = []
    for (x, y), (u, v) in zip(destination, source):
        rows.extend([[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]])
        values.extend([u, v])
    for col in range(8):
        pivot = max(range(col, 8), key=lambda i: abs(rows[i][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        values[col], values[pivot] = values[pivot], values[col]
        divisor = rows[col][col]
        if abs(divisor) < 1e-12:
            raise ValueError("Degenerate quadrilateral")
        rows[col] = [v / divisor for v in rows[col]]
        values[col] /= divisor
        for i in range(8):
            if i == col:
                continue
            factor = rows[i][col]
            rows[i] = [a - factor * b for a, b in zip(rows[i], rows[col])]
            values[i] -= factor * values[col]
    return values


def quad_text_png(block, width, height):
    _, _, w, h = block.geometry(width, height)
    lines, size = fitted_text(block.text, w, h)
    with pymupdf.open() as document:
        page = document.new_page(width=w, height=h)
        page.insert_font(fontname="quad", fontbuffer=spatial_font().buffer)
        for i, line in enumerate(lines):
            page.insert_text((0, size * (1 + i * 1.2)), line, fontname="quad", fontsize=size)
        zoom = min(2, 4096 / max(w, h))
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
        image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGBA")
    x0, y0, x1, y1 = block.rect
    left, top = math.floor(x0 * width), math.floor(y0 * height)
    out_size = (max(1, math.ceil(x1 * width) - left), max(1, math.ceil(y1 * height) - top))
    destination = [(x - left, y - top) for x, y in block.corners(width, height)]
    coefficients = perspective_coefficients(
        destination, [(0, 0), (image.width, 0), (image.width, image.height), (0, image.height)]
    )
    result = image.transform(
        out_size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC
    )
    buffer = io.BytesIO()
    result.save(buffer, "PNG")
    return buffer.getvalue(), (left, top, left + out_size[0], top + out_size[1])


def insert_quad_text_layer(page, block, width, height, scale):
    """Searchable text; the visible image carries exact perspective glyphs."""
    _, _, w, h = block.geometry(width, height)
    lines, size = fitted_text(block.text, w, h)
    a, b, c, d, e, f, g, k = perspective_coefficients(
        [(0, 0), (w, 0), (w, h), (0, h)], block.corners(width, height)
    )

    def mapped(x, y):
        den = g * x + k * y + 1
        return ((a * x + b * y + c) / den * scale, (d * x + e * y + f) / den * scale)

    for i, line in enumerate(lines):
        y = size * (1 + i * 1.2)
        x0, y0 = mapped(0, y)
        x1, y1 = mapped(w, y)
        x2, y2 = mapped(0, y + size)
        point = pymupdf.Point(x0, y0)
        matrix = pymupdf.Matrix(
            (x1 - x0) / w, -(y1 - y0) / w, -(x2 - x0) / size, (y2 - y0) / size, 0, 0
        )
        page.insert_text(
            point, line, fontname="spatial", fontsize=size, render_mode=3, morph=(point, matrix)
        )
