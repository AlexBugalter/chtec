import io
import threading

import pymupdf
import pytest
from PIL import Image

from chtec.domain import AppError
from chtec.formats import default_registry
from chtec.regions import ImageRegion, RegionExtractor, RegionRecognitionService


def test_crop_uses_original_detail_not_downscaled_preview(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (4000, 3000), "white").save(path)
    page = default_registry().import_file(path)[0]
    reading = RegionExtractor().extract(page, ImageRegion(0.25, 0.25, 0.5, 0.5))
    assert reading.used_original
    assert Image.open(io.BytesIO(reading.image_png)).size == (1000, 750)


def test_missing_original_uses_saved_copy_with_explicit_flag(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (4000, 3000), "white").save(path)
    page = default_registry().import_file(path)[0]
    path.unlink()
    reading = RegionExtractor().extract(page, ImageRegion(0.25, 0.25, 0.5, 0.5))
    assert not reading.used_original
    assert Image.open(io.BytesIO(reading.image_png)).size == (600, 450)


def test_changed_original_is_not_silently_used(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), "red").save(path)
    page = default_registry().import_file(path)[0]
    Image.new("RGB", (100, 100), "blue").save(path)
    with pytest.raises(AppError, match="измен"):
        RegionExtractor().extract(page, ImageRegion(0, 0, 0.5, 0.5))


def test_tiff_crop_uses_correct_frame(tmp_path):
    path = tmp_path / "scan.tiff"
    Image.new("RGB", (100, 100), "red").save(
        path, save_all=True, append_images=[Image.new("RGB", (100, 100), "blue")]
    )
    page = default_registry().import_file(path)[1]
    reading = RegionExtractor().extract(page, ImageRegion(0, 0, 0.5, 0.5))
    assert Image.open(io.BytesIO(reading.image_png)).getpixel((1, 1)) == (0, 0, 255)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pdf_crop_follows_displayed_coordinates(tmp_path, rotation):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=300, height=200)
        p.draw_rect(pymupdf.Rect(0, 0, 150, 200), color=None, fill=(1, 0, 0))
        p.draw_rect(pymupdf.Rect(150, 0, 300, 200), color=None, fill=(0, 0, 1))
        p.set_rotation(rotation)
        pdf.save(path)
    page = default_registry().import_file(path)[0]
    region = ImageRegion(0.1, 0.1, 0.3, 0.3)
    reading = RegionExtractor().extract(page, region)
    crop = Image.open(io.BytesIO(reading.image_png))
    preview = Image.open(io.BytesIO(page.image_png))
    expected = preview.getpixel((int(preview.width * 0.2), int(preview.height * 0.2)))
    assert crop.getpixel((crop.width // 2, crop.height // 2)) == expected
    assert reading.used_original


@pytest.mark.parametrize("coords", [(0, 0, 0, 1), (-1, 0, 1, 1), (0, 0, 2, 1)])
def test_invalid_region_is_rejected(coords):
    with pytest.raises(AppError):
        ImageRegion(*coords).validated()


def test_review_reads_four_regions_then_proposes_without_mutation(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (400, 400), "white").save(path)
    page = default_registry().import_file(path)[0]
    page.text = "Имя: [неразборчиво]"

    class Model:
        calls = 0

        def recognize(self, fragment, instruction):
            self.calls += 1
            return "Имя: Мария"

        def edit(self, text, instruction):
            assert "Мария" in instruction
            return "Имя: Мария"

    model = Model()
    result = RegionRecognitionService(model).review(page, threading.Event(), lambda *args: None)
    assert model.calls == 4
    assert result.text == "Имя: Мария"
    assert len(result.readings) == 4
    assert page.text == "Имя: [неразборчиво]"


def test_review_cancels_before_merge(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), "white").save(path)
    page = default_registry().import_file(path)[0]
    stop = threading.Event()

    class Model:
        def recognize(self, fragment, instruction):
            stop.set()
            return "Часть"

        def edit(self, text, instruction):
            pytest.fail("Cancelled review must not merge")

    assert RegionRecognitionService(Model()).review(page, stop, lambda *args: None) is None


def test_exif_orientation_matches_selected_preview(tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (200, 100), "red")
    image.paste("blue", (100, 0, 200, 100))
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif, quality=100)
    page = default_registry().import_file(path)[0]
    reading = RegionExtractor().extract(page, ImageRegion(0.1, 0.1, 0.8, 0.4))
    with Image.open(io.BytesIO(reading.image_png)) as crop:
        red, green, blue = crop.getpixel((crop.width // 2, crop.height // 2))
    assert red > 240 and blue < 10
