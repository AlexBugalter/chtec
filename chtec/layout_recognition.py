import io
from dataclasses import replace

from PIL import Image

from chtec.domain import Page
from chtec.layout import parse_layout
from chtec.regions import ImageRegion, RegionExtractor


class LayoutRecognitionService:
    def __init__(self, model):
        self.model = model

    def recognize(self, page):
        return [replace(block, verified=False) for block in parse_layout(self.model.layout(page))]

    def refine(self, page, block):
        image = RegionExtractor().extract(page, ImageRegion(*block.rect))
        data = image.image_png
        if block.angle:
            with Image.open(io.BytesIO(data)) as source:
                rotated = source.convert("RGB").rotate(block.angle, expand=True, fillcolor="white")
                output = io.BytesIO()
                rotated.save(output, "PNG")
                data = output.getvalue()
        return self.model.recognize(
            Page(data, page.source, page.number),
            "Прочитай только этот участок. Он повёрнут для удобства чтения. "
            "Сохраняй язык, имена, числа и переносы; не дописывай по смыслу.",
        )
