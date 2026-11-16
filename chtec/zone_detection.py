"""Local, detection-only OCR. Coordinates never come from the language model."""

import io
import math
from dataclasses import replace
from pathlib import Path

from PIL import Image

from chtec.domain import AppError
from chtec.layout import TextBlock


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise AppError("Поиск и чтение зон отменены.")


def block_from_quad(identifier, quad, width, height):
    if len(quad) != 4 or width <= 0 or height <= 0:
        raise AppError("Некорректная геометрия детектора.")
    points = [(float(x), float(y)) for x, y in quad]
    if not all(math.isfinite(v) for point in points for v in point):
        raise AppError("Некорректная геометрия детектора.")
    xs, ys = zip(*points)
    rect = (
        max(0, min(xs) / width),
        max(0, min(ys) / height),
        min(1, max(xs) / width),
        min(1, max(ys) / height),
    )
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        raise AppError("Пустая зона детектора.")
    dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
    angle = math.degrees(math.atan2(dy, dx))
    while angle > 90:
        angle -= 180
    while angle < -90:
        angle += 180
    return TextBlock(identifier, "", rect, angle, False)


def split_on_rules(blocks, rules):
    result = []
    for block in blocks:
        x0, y0, x1, y1 = block.rect
        cuts = []
        if abs(block.angle) < 15:
            for rx0, ry0, rx1, ry1 in rules:
                if min(ry0, ry1) <= (y0 + y1) / 2 <= max(ry0, ry1):
                    x = (rx0 + rx1) / 2
                    if x0 + 0.008 < x < x1 - 0.008:
                        cuts.append(x)
        edges = [x0, *sorted(set(cuts)), x1]
        for index, (left, right) in enumerate(zip(edges, edges[1:])):
            result.append(replace(block, id=f"{block.id}-{index}", rect=(left, y0, right, y1)))
    return result


def group_lines(blocks, rules=()):
    """Join at most four aligned lines, respecting table barriers and orientation."""
    groups = []
    for block in sorted(blocks, key=lambda b: (b.rect[1], b.rect[0])):
        match = None
        x0, y0, x1, y1 = block.rect
        for index, (candidate, count) in enumerate(groups):
            a, b, c, d = candidate.rect
            gap = y0 - d
            barrier = any(
                min(ry0, ry1) <= (y0 + d) / 2 <= max(ry0, ry1)
                and min(a, x0) < (rx0 + rx1) / 2 < max(c, x1)
                for rx0, ry0, rx1, ry1 in rules
            )
            if (
                count < 4
                and abs(block.angle) < 15
                and abs(candidate.angle) < 15
                and abs(block.angle - candidate.angle) < 5
                and -0.003 <= gap <= min(0.018, (y1 - y0) * 0.8)
                and abs(a - x0) < 0.025
                and min(c, x1) - max(a, x0) > 0.5 * min(c - a, x1 - x0)
                and not barrier
            ):
                match = index
                break
        if match is None:
            groups.append((block, 1))
        else:
            old, count = groups[match]
            a, b, c, d = old.rect
            groups[match] = (replace(old, rect=(min(a, x0), b, max(c, x1), max(d, y1))), count + 1)
    return [replace(block, id=f"zone-{i + 1}") for i, (block, _) in enumerate(groups)]


class TextZoneDetector:
    """Packaged PP-OCR detector on CPU; no recognition model or network requests."""

    def detect(self, page, cancel=None, progress=None):
        check_cancel(cancel)
        try:
            import cv2
            import numpy as np
            import rapidocr
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise AppError("Установите зависимости детектора: pip install -e .") from exc
        model_path = Path(rapidocr.__file__).parent / "models" / "PP-OCRv6_det_small.onnx"
        if not model_path.is_file():
            raise AppError("Не найдена локальная модель детектора PP-OCRv6.")
        engine = RapidOCR(
            params={
                "Global.use_cls": False,
                "Global.use_rec": False,
                "Global.max_side_len": 3000,
                "Global.log_level": "warning",
                "Det.model_path": str(model_path),
                "Det.limit_side_len": 1600,
                "Det.limit_type": "max",
                "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        with Image.open(io.BytesIO(page.image_png)) as source:
            image = cv2.cvtColor(np.array(source.convert("RGB")), cv2.COLOR_RGB2BGR)
        height, width = image.shape[:2]
        blocks = []
        for turn in (0, 1):
            check_cancel(cancel)
            if progress:
                progress(turn + 1, 3, "Поиск строк" if not turn else "Поиск вертикальных надписей")
            output = engine(
                np.ascontiguousarray(np.rot90(image, turn)),
                use_det=True,
                use_cls=False,
                use_rec=False,
            )
            check_cancel(cancel)
            if output.boxes is None:
                continue
            for quad in output.boxes:
                if turn:
                    quad = [(width - float(y), float(x)) for x, y in quad]
                block = block_from_quad(str(len(blocks)), quad, width, height)
                x0, y0, x1, y1 = block.rect
                if turn and (y1 - y0) * height < 2 * (x1 - x0) * width:
                    continue
                if (x1 - x0) * width < 8 or (y1 - y0) * height < 8:
                    continue
                blocks.append(block)
        # Remove horizontal fragments contained in an independently detected vertical note.
        vertical = [b for b in blocks if abs(b.angle) > 70]
        blocks = [
            b
            for b in blocks
            if b in vertical
            or not any(
                v.rect[0] <= (b.rect[0] + b.rect[2]) / 2 <= v.rect[2]
                and v.rect[1] <= (b.rect[1] + b.rect[3]) / 2 <= v.rect[3]
                for v in vertical
            )
        ]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 12
        )
        lines = cv2.HoughLinesP(
            binary, 1, np.pi / 180, threshold=100, minLineLength=int(height * 0.2), maxLineGap=30
        )
        rules = []
        if lines is not None:
            for x0, y0, x1, y1 in lines.reshape(-1, 4):
                if abs(x1 - x0) < 0.025 * width and abs(y1 - y0) > 0.2 * height:
                    rule = (x0 / width, y0 / height, x1 / width, y1 / height)
                    if not any(abs((r[0] + r[2] - rule[0] - rule[2]) / 2) < 0.006 for r in rules):
                        rules.append(rule)
        check_cancel(cancel)
        result = group_lines(split_on_rules(blocks, rules), rules)
        if len(result) > 2000:
            raise AppError("Найдено слишком много зон. Выберите меньший участок.")
        if progress:
            progress(3, 3, f"Найдено зон: {len(result)}")
        return result
