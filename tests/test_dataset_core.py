import io
import json
import runpy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from PIL import Image

from chtec.dataset import DatasetService
from chtec.domain import Page
from chtec.domain_errors import AppError
from chtec.layout import TextBlock


def page(color="white", text="Точный текст", bid="a"):
    stream = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(stream, "PNG")
    return Page(
        stream.getvalue(),
        "private-source",
        1,
        blocks=[TextBlock(bid, text, (0, 0, 1, 1), verified=True)],
    )


def approve(service, p, group=""):
    sample = service.capture(p, p.blocks[0], group)
    return service.approve(sample["id"], sample["fingerprint"])


def test_export_cannot_pollute_live_collection_directory(tmp_path):
    folder = tmp_path / "collection"
    service = DatasetService(folder)
    approve(service, page())
    with pytest.raises(AppError):
        service.export(folder / "export")
    assert len(DatasetService(folder).list_samples()) == 1


def test_capture_is_draft_persistent_and_verified_is_not_approval(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    sample = service.capture(p, p.blocks[0])
    assert sample["status"] == "draft"
    assert sample["source_model"] is None
    assert sample["image_path"].is_file()
    assert DatasetService(tmp_path).list_samples()[0] == sample
    assert service.capture(p, p.blocks[0])["id"] == sample["id"]


def test_edit_undo_delete_invalidate_and_preserve_immutable_versions(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    first = approve(service, p)
    original = first["image_path"].read_bytes()
    p.edit_block("a", "Исправление")
    service.sync_page(p)
    current = service.list_samples()[0]
    assert current["status"] == "stale"
    assert current["id"] != first["id"]
    assert first["image_path"].read_bytes() == original
    with pytest.raises(AppError):
        service.approve(first["id"], first["fingerprint"])
    with pytest.raises(AppError):
        service.approve(current["id"], first["fingerprint"])
    service.approve(current["id"], current["fingerprint"])
    p.undo()
    service.sync_page(p)
    assert service.list_samples()[0]["status"] == "stale"
    p.blocks = []
    service.sync_page(p)
    assert service.list_samples()[0]["status"] == "stale"
    with pytest.raises(AppError):
        current = service.list_samples()[0]
        service.approve(current["id"], current["fingerprint"])


def test_exclusion_persists_through_edits_and_threads(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    sample = approve(service, p)
    service.exclude(sample["id"])
    p.blocks = [replace(p.blocks[0], text="Другой текст")]
    with ThreadPoolExecutor() as pool:
        pool.submit(service.sync_page, p).result()
    assert service.list_samples()[0]["status"] == "excluded"


@pytest.mark.parametrize("text", ["", "  ", "[неразборчиво]", "слово [???]", "[illegible]"])
def test_blank_and_uncertain_cannot_be_approved(tmp_path, text):
    service = DatasetService(tmp_path)
    p = page(text=text)
    with pytest.raises(AppError):
        sample = service.capture(p, p.blocks[0])
        service.approve(sample["id"], sample["fingerprint"])


def test_mask_and_missing_original(tmp_path):
    service = DatasetService(tmp_path)
    p = page("black")
    p.source_path = str(tmp_path / "missing.png")
    p.blocks = [p.blocks[0].with_quad(((0.5, 0), (1, 0.5), (0.5, 1), (0, 0.5)))]
    sample = service.capture(p, p.blocks[0])
    image = Image.open(sample["image_path"])
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((40, 30)) == (0, 0, 0)
    assert sample["used_original"] is False


def test_export_deduplicates_roundtrip_and_contains_no_source_paths(tmp_path):
    service = DatasetService(tmp_path / "collection")
    p = page()
    approve(service, p, "book")
    p.blocks = [replace(p.blocks[0], id="b")]
    approve(service, p, "different-book")
    target = tmp_path / "export"
    report = service.export(target)
    assert report["exported"] == 1
    assert report["duplicates"] == 1
    assert report["warnings"]
    rows = [
        json.loads(line)
        for line in (target / "data.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["image"].startswith("images/")
    assert (target / rows[0]["image"]).is_file()
    loader = runpy.run_path(str(target / "load_dataset.py"))
    loaded = loader["load_dataset"](target / "messages.jsonl")
    assert isinstance(loaded[0]["messages"][0]["content"][1]["image"], Image.Image)
    for filename in ["data.jsonl", "messages.jsonl", "manifest.json", "report.json"]:
        content = (target / filename).read_text(encoding="utf-8")
        assert "private-source" not in content
        assert str(tmp_path) not in content
    with pytest.raises(AppError):
        service.export(target)


def test_conflicting_transcriptions_block_export(tmp_path):
    service = DatasetService(tmp_path / "collection")
    approve(service, page())
    p = page(text="Несовместимый ответ", bid="b")
    sample = service.capture(p, p.blocks[0])
    # Approval must reject already known conflicts as well as export validation.
    with pytest.raises(AppError):
        service.approve(sample["id"], sample["fingerprint"])


def test_split_is_reproducible_and_never_splits_a_page_or_book(tmp_path):
    service = DatasetService(tmp_path / "collection")
    for i in range(12):
        p = page((i * 15, 30, 90), text=f"Строка {i}")
        approve(service, p, f"book-{i}")
        p.blocks = [replace(p.blocks[0], id="second", rect=(0, 0, 0.5, 0.5), text=f"Иное {i}")]
        approve(service, p, "common" if i < 2 else f"other-{i}")
    for destination in ["one", "two"]:
        service.export(tmp_path / destination)
    a = json.loads((tmp_path / "one" / "manifest.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "two" / "manifest.json").read_text(encoding="utf-8"))
    assert a == b
    seen = {}
    for part, ids in a["splits"].items():
        for sid in ids:
            sample = next(s for s in service.list_samples() if s["id"] == sid)
            for key in [("page", sample["page_hash"]), ("group", sample["group"])]:
                assert seen.setdefault(key, part) == part
    assert sum(bool(ids) for ids in a["splits"].values()) == 3


def test_manual_revision_survives_sync_but_source_change_invalidates(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    sample = approve(service, p)
    revised = service.revise(sample["id"], sample["fingerprint"], "Ручная транскрипция", "Дело")
    assert revised["status"] == "draft"
    service.sync_page(p)
    assert service.list_samples()[0]["text"] == "Ручная транскрипция"
    service.approve(revised["id"], revised["fingerprint"])
    p.blocks = [replace(p.blocks[0], rect=(0, 0, 0.5, 1))]
    service.sync_page(p)
    assert service.list_samples()[0]["status"] == "stale"


def test_disabled_collection_invalidates_only_existing_samples(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    approve(service, p)
    p.blocks = [replace(p.blocks[0], text="Правка"), TextBlock("b", "Новый", (0, 0, 0.5, 0.5))]
    service.sync_page(p, capture_new=False)
    samples = service.list_samples()
    assert len(samples) == 1
    assert samples[0]["status"] == "stale"


def test_modified_blob_cannot_be_approved(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    sample = service.capture(p, p.blocks[0])
    Image.new("RGB", (80, 60), "red").save(sample["image_path"])
    with pytest.raises(AppError):
        service.approve(sample["id"], sample["fingerprint"])


def test_uncertain_manual_marker_prevents_approval(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    sample = service.capture(p, p.blocks[0])
    revised = service.revise(sample["id"], sample["fingerprint"], "Возможно верно", uncertain=True)
    with pytest.raises(AppError):
        service.approve(revised["id"], revised["fingerprint"])


def test_changed_original_revokes_current_approval_before_crop_error(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (80, 60), "white").save(source)
    p = page()
    p.source_path = str(source)
    service = DatasetService(tmp_path / "collection")
    approve(service, p)
    Image.new("RGB", (80, 60), "red").save(source)
    with pytest.raises(AppError, match="измен"):
        service.sync_page(p)
    assert service.list_samples()[0]["status"] == "stale"
    with pytest.raises(AppError):
        service.export(tmp_path / "export")


def test_export_failure_does_not_leave_partial_target(tmp_path):
    service = DatasetService(tmp_path / "collection")
    s = approve(service, page())
    s["image_path"].unlink()
    target = tmp_path / "export"
    with pytest.raises(AppError):
        service.export(target)
    assert not target.exists()


def test_rotated_rect_preserves_orientation_and_masks_outside_region(tmp_path):
    service = DatasetService(tmp_path)
    p = page("black")
    p.blocks = [replace(p.blocks[0], angle=30)]
    sample = service.capture(p, p.blocks[0])
    image = Image.open(sample["image_path"])
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((image.width // 2, image.height // 2)) == (0, 0, 0)
    assert sample["geometry"]["angle"] == 30


def test_collection_ignores_private_files_and_rejects_unrelated_folder(tmp_path):
    folder = tmp_path / "collection"
    DatasetService(folder)
    assert (folder / ".gitignore").read_text(encoding="utf-8").strip() == "*"
    DatasetService(folder)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "notes.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(AppError):
        DatasetService(unrelated)
    assert not (unrelated / ".gitignore").exists()
    assert not (unrelated / "dataset.sqlite3").exists()


def test_collection_rejects_repository_root_and_preserves_user_gitignore(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("existing rule\n", encoding="utf-8")
    with pytest.raises(AppError):
        DatasetService(tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "existing rule\n"


def test_export_creates_privacy_gitignore(tmp_path):
    service = DatasetService(tmp_path / "collection")
    approve(service, page())
    service.export(tmp_path / "export")
    assert (tmp_path / "export" / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_excluding_historical_revision_excludes_latest_logical_zone(tmp_path):
    service = DatasetService(tmp_path)
    p = page()
    displayed = approve(service, p)
    p.blocks = [replace(p.blocks[0], text='Новая редакция')]
    service.sync_page(p)
    current = service.list_samples()[0]
    assert current['id'] != displayed['id']
    excluded = service.exclude(displayed['id'])
    assert excluded['id'] == current['id']
    assert service.list_samples()[0]['status'] == 'excluded'
    p.blocks = [replace(p.blocks[0], text='Следующая редакция')]
    service.sync_page(p)
    assert service.list_samples()[0]['status'] == 'excluded'
