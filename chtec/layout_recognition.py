import io
import json
import re
from dataclasses import replace

from PIL import Image, ImageDraw

from chtec.domain import AppError, Page
from chtec.layout import parse_layout
from chtec.regions import RegionExtractor
from chtec.zone_detection import check_cancel


def refinement_text(answer):
    """Extract OCR text from common structured replies without changing plain text."""
    if not isinstance(answer, str):
        raise AppError("Модель вернула ответ в неподдерживаемом формате.")
    candidate = answer.strip()
    fenced = candidate.startswith("```")
    if fenced:
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n?```\s*", candidate, re.I | re.S)
        if not match:
            raise AppError("Не удалось прочитать ответ модели. Повторите уточнение.")
        candidate = match.group(1).strip()
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        if fenced or candidate.startswith('{') or re.match(r'^\[\s*\{', candidate):
            raise AppError("Не удалось прочитать ответ модели. Повторите уточнение.")
        return answer

    def extract(value):
        if isinstance(value, dict):
            if 'text' in value:
                if not isinstance(value['text'], str):
                    raise ValueError('text')
                return [value['text']]
            for key in ('regions', 'blocks'):
                if key in value:
                    return extract(value[key])
            raise ValueError('missing text')
        if isinstance(value, list) and value:
            return [text for item in value for text in extract(item)]
        raise ValueError('invalid structure')

    if not isinstance(payload, (dict, list)) and not fenced:
        return answer
    try:
        return '\n'.join(extract(payload))
    except ValueError as exc:
        raise AppError("В ответе модели не найден текст. Повторите уточнение.") from exc


class LayoutRecognitionService:
    def __init__(self, model, detector=None):
        self.model = model
        self.detector = detector

    def recognize(self, page, cancel=None, progress=None):
        check_cancel(cancel)
        if self.detector is None:
            return [
                replace(block, verified=False) for block in parse_layout(self.model.layout(page))
            ]
        zones = page.blocks or self.detector.detect(page, cancel=cancel, progress=progress)
        result = []
        for start in range(0, len(zones), 6):
            check_cancel(cancel)
            batch = zones[start : start + 6]
            if progress:
                progress(start, len(zones), f"Чтение зон {start + 1}–{start + len(batch)}")
            sheet = self.contact_sheet(page, batch)
            ids = [block.id for block in batch]
            answer = self.model.read_regions(sheet, ids)
            check_cancel(cancel)
            try:
                payload = json.loads(
                    answer.strip().removeprefix("```json").removesuffix("```").strip()
                )
                regions = payload["regions"]
                if not isinstance(regions, list) or len(regions) != len(ids):
                    raise ValueError("count")
                texts = {}
                for region in regions:
                    identifier, text = region["id"], region["text"]
                    if identifier not in ids or identifier in texts or not isinstance(text, str):
                        raise ValueError("id/text")
                    texts[identifier] = text
                if set(texts) != set(ids):
                    raise ValueError("missing")
            except (ValueError, KeyError, TypeError) as exc:
                raise AppError(
                    "Модель вернула неверные ID зон. Предыдущий результат сохранён."
                ) from exc
            result.extend(replace(block, text=texts[block.id], verified=False) for block in batch)
        return result

    @staticmethod
    def contact_sheet(page, blocks):
        crops = []
        for block in blocks:
            data = RegionExtractor().extract_block(page, block).image_png
            with Image.open(io.BytesIO(data)) as source:
                crop = source.convert("RGB").rotate(block.angle, expand=True, fillcolor="white")
                crop.thumbnail((1800, 800))
                crops.append((block.id, crop))
        width = max(crop.width for _, crop in crops) + 24
        height = sum(crop.height + 40 for _, crop in crops)
        sheet = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sheet)
        y = 0
        for identifier, crop in crops:
            draw.text((12, y + 4), identifier, fill="black", font_size=22)
            sheet.paste(crop, (12, y + 32))
            y += crop.height + 40
            draw.line((0, y - 2, width, y - 2), fill="#888888", width=2)
        sheet.thumbnail((2400, 2400))
        output = io.BytesIO()
        sheet.save(output, "PNG")
        return output.getvalue()

    def refine(self, page, block):
        image = RegionExtractor().extract_block(page, block)
        data = image.image_png
        if block.angle:
            with Image.open(io.BytesIO(data)) as source:
                rotated = source.convert("RGB").rotate(block.angle, expand=True, fillcolor="white")
                output = io.BytesIO()
                rotated.save(output, "PNG")
                data = output.getvalue()
        answer = self.model.recognize(
            Page(data, page.source, page.number),
            "Прочитай только этот участок. Он повёрнут для удобства чтения. "
            "Сохраняй язык, имена, числа и переносы; не дописывай по смыслу. "
            "Верни только распознанный текст, без JSON, координат и пояснений.",
        )
        return refinement_text(answer)
