"""Shared text fitting for spatial screen and export representations."""

import io
from pathlib import Path

import pymupdf
from PIL import Image


def image_size(page):
    with Image.open(io.BytesIO(page.image_png)) as image:
        return image.size


def spatial_font():
    for path in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).is_file():
            return pymupdf.Font(fontfile=path)
    return pymupdf.Font("cjk")


def fitted_text(text, width, height):
    font = spatial_font()
    advances = {char: font.text_length(char, fontsize=1) for char in set(text) if char != "\n"}

    def wrap(size):
        result = []
        for paragraph in text.split("\n"):
            line = ""
            used = 0
            for char in paragraph:
                advance = advances[char] * size
                if line and used + advance > width:
                    result.append(line)
                    line = ""
                    used = 0
                line += char
                used += advance
            result.append(line)
        return result

    low, high = 0.05, min(100, height / 1.2)
    for _ in range(15):
        mid = (low + high) / 2
        if len(wrap(mid)) * mid * 1.2 <= height:
            low = mid
        else:
            high = mid
    return wrap(low), low
