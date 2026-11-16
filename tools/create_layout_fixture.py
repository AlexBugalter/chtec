"""Deterministic, explicitly synthetic geometry fixture for manual UI/export QA."""

import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

from chtec.domain import Document, Page
from chtec.formats import default_registry
from chtec.layout import TextBlock

folder = Path("output/layout-check")
folder.mkdir(parents=True, exist_ok=True)
image = Image.new("RGB", (800, 1000), "white")
draw = ImageDraw.Draw(image)
font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 40)
draw.text((80, 150), "Дария 1883", font=font, fill="black", anchor="lt")
line = Image.new("RGB", (440, 50), "white")
ImageDraw.Draw(line).text((0, 0), "Подпись священника", font=font, fill="black", anchor="lt")
image.paste(line.rotate(-90, expand=True), (600, 300))
image.save(folder / "synthetic-layout.png")
data = io.BytesIO()
image.save(data, "PNG")
page = Page(data.getvalue(), "ТЕСТОВАЯ страница · поворот", 1)
page.replace_blocks(
    [
        TextBlock("name", "Дария 1883", (0.1, 0.15, 0.5, 0.20), verified=True),
        TextBlock("signature", "Подпись священника", (0.75, 0.3, 0.8125, 0.74), 90, True),
    ]
)
doc = Document([page], "Тест расположения и поворота — не архивный документ")
registry = default_registry()
for extension in (".json", ".html", ".pdf"):
    registry.export_file(doc, folder / ("synthetic-layout" + extension))

with pymupdf.open(folder / "synthetic-layout.pdf") as pdf:
    pdf[0].get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(folder / "synthetic-layout-pdf.png")
print("Synthetic project, HTML and rendered PDF created:", folder.resolve())
