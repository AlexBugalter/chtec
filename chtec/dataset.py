"""Local, explicitly approved OCR training snapshots; no network or model inference."""

import io
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from PIL import Image

from chtec.domain_errors import AppError
from chtec.regions import RegionExtractor

INSTRUCTION = "Read the text in this image exactly. Preserve spelling and line breaks."


def digest(value):
    return sha256(value).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def validate_text(text, uncertain=False):
    if not isinstance(text, str) or not text.strip():
        raise AppError("Для обучения нужен непустой текст.")
    if uncertain or re.search(r"неразборчив|illegible|unreadable|\?{2,}", text, re.I):
        raise AppError("Сомнительный текст нельзя подтвердить для обучения.")


class DatasetStore:
    """One SQLite connection per operation; snapshots retain independent PNG blobs."""

    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.lock = threading.RLock()
        try:
            if self.folder.exists():
                entries = list(self.folder.iterdir())
                if any(
                    entry.name not in {"images", ".gitignore"}
                    and not entry.name.startswith("dataset.sqlite3")
                    for entry in entries
                ):
                    raise AppError(
                        "Выберите пустую папку или существующую папку коллекции датасета."
                    )
                ignore = self.folder / ".gitignore"
                if ignore.exists() and ignore.read_text(encoding="utf-8").strip() != "*":
                    raise AppError(
                        "В папке уже есть пользовательский .gitignore. "
                        "Выберите отдельную папку коллекции."
                    )
            self.folder.mkdir(parents=True, exist_ok=True)
            ignore = self.folder / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
            (self.folder / "images").mkdir(parents=True, exist_ok=True)
            with self.connection() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS revisions (
                        id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS current (
                        page_hash TEXT NOT NULL, block_id TEXT NOT NULL,
                        sample_id TEXT NOT NULL, PRIMARY KEY(page_hash, block_id));
                """)
        except OSError as exc:
            raise AppError(f"Не удалось открыть коллекцию: {exc}") from exc

    @contextmanager
    def connection(self):
        with self.lock:
            db = None
            try:
                db = sqlite3.connect(self.folder / "dataset.sqlite3", timeout=30)
                with db:
                    yield db
            except (sqlite3.Error, OSError) as exc:
                raise AppError(f"Ошибка хранения датасета: {exc}") from exc
            finally:
                if db is not None:
                    db.close()

    def save(self, db, sample):
        stored = dict(sample)
        stored.pop("image_path", None)
        db.execute(
            "INSERT OR REPLACE INTO revisions VALUES (?,?)",
            (sample["id"], json.dumps(stored, ensure_ascii=False)),
        )

    def decode(self, row):
        if row is None:
            raise AppError("Пример отсутствует в коллекции.")
        sample = json.loads(row[0])
        sample["image_path"] = self.folder / "images" / (sample["image_hash"] + ".png")
        return sample

    def get(self, db, sample_id):
        return self.decode(
            db.execute("SELECT payload FROM revisions WHERE id=?", (sample_id,)).fetchone()
        )

    def latest(self, db, page_hash, block_id):
        row = db.execute(
            "SELECT sample_id FROM current WHERE page_hash=? AND block_id=?", (page_hash, block_id)
        ).fetchone()
        return self.get(db, row[0]) if row else None

    def blob(self, data):
        image_hash = digest(data)
        target = self.folder / "images" / (image_hash + ".png")
        if not target.exists():
            temp = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                temp.write_bytes(data)
                os.replace(temp, target)
            except OSError as exc:
                raise AppError(f"Не удалось сохранить изображение: {exc}") from exc
            finally:
                temp.unlink(missing_ok=True)
        return image_hash


class DatasetService:
    def __init__(self, folder):
        self.store = DatasetStore(folder)
        self.folder = self.store.folder

    def capture(self, page, block, group=""):
        block = block.validated()
        if not block.text.strip():
            raise AppError("Пустая зона не является примером для обучения.")
        page_hash = digest(page.image_png)
        with Image.open(io.BytesIO(page.image_png)) as image:
            dimensions = list(image.size)
        crop_block = block
        if block.quad is None and block.angle:
            width, height = dimensions
            crop_block = block.with_quad(
                tuple((x / width, y / height) for x, y in block.corners(width, height))
            )
        try:
            region = RegionExtractor().extract_block(page, crop_block)
        except AppError:
            # A changed original must revoke approval even when extraction fails.
            with self.store.connection() as db:
                previous = self.store.latest(db, page_hash, block.id)
                if previous:
                    previous["source_invalid"] = True
                    if previous["status"] != "excluded":
                        previous["status"] = "stale"
                    self.store.save(db, previous)
            raise
        image_hash = self.store.blob(region.image_png)
        geometry = {"rect": block.rect, "quad": block.quad, "angle": block.angle}
        source_fp = fingerprint([page_hash, block.id, geometry, block.text, image_hash])
        with self.store.connection() as db:
            previous = self.store.latest(db, page_hash, block.id)
            if (
                previous
                and previous["source_fingerprint"] == source_fp
                and not previous.get("deleted")
                and not previous.get("source_invalid")
            ):
                return previous
            sample = {
                "id": uuid.uuid4().hex,
                "page_hash": page_hash,
                "block_id": block.id,
                "text": block.text,
                "group": group,
                "image_hash": image_hash,
                "geometry": geometry,
                "page_size": dimensions,
                "page_number": page.number,
                "source_fingerprint": source_fp,
                "source_model": None,
                "source_ocr": None,
                "used_original": region.used_original,
                "source_label": region.source_label,
                "status": "draft" if previous is None else "stale",
                "uncertain": False,
                "deleted": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "approved_at": None,
                "revision": 1 if previous is None else previous["revision"] + 1,
            }
            if previous and previous["status"] == "excluded":
                sample["status"] = "excluded"
            sample["fingerprint"] = fingerprint(
                [source_fp, sample["text"], group, False, sample["id"]]
            )
            self.store.save(db, sample)
            db.execute(
                "INSERT OR REPLACE INTO current VALUES (?,?,?)", (page_hash, block.id, sample["id"])
            )
            return self.store.get(db, sample["id"])

    def sync_page(self, page, group="", capture_new=True):
        page_hash = digest(page.image_png)
        live = {b.id for b in page.blocks if b.text.strip()}
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT sample_id, block_id FROM current WHERE page_hash=?", (page_hash,)
            ).fetchall()
            for sample_id, block_id in rows:
                if block_id not in live:
                    sample = self.store.get(db, sample_id)
                    sample["deleted"] = True
                    if sample["status"] != "excluded":
                        sample["status"] = "stale"
                    self.store.save(db, sample)
        existing = {block_id for _, block_id in rows}
        return [
            self.capture(page, block, group)
            for block in page.blocks
            if block.text.strip() and (capture_new or block.id in existing)
        ]

    def get(self, sample_id):
        with self.store.connection() as db:
            return self.store.get(db, sample_id)

    def list_samples(self):
        with self.store.connection() as db:
            rows = db.execute(
                "SELECT r.payload FROM revisions r JOIN current c ON c.sample_id=r.id "
                "ORDER BY c.page_hash,c.block_id"
            ).fetchall()
            return [self.store.decode(row) for row in rows]

    def _checked(self, db, sample_id, expected_fingerprint):
        sample = self.store.get(db, sample_id)
        latest = self.store.latest(db, sample["page_hash"], sample["block_id"])
        if (
            latest["id"] != sample_id
            or sample["fingerprint"] != expected_fingerprint
            or sample.get("deleted")
            or sample.get("source_invalid")
        ):
            raise AppError("Пример изменился или удалён. Откройте актуальную версию для проверки.")
        return sample

    def approve(self, sample_id, expected_fingerprint):
        with self.store.connection() as db:
            sample = self._checked(db, sample_id, expected_fingerprint)
            validate_text(sample["text"], sample["uncertain"])
            for row in db.execute(
                "SELECT r.payload FROM revisions r JOIN current c ON c.sample_id=r.id"
            ):
                other = self.store.decode(row)
                if (
                    other["id"] != sample_id
                    and other["status"] == "approved"
                    and other["image_hash"] == sample["image_hash"]
                    and other["text"] != sample["text"]
                ):
                    raise AppError(
                        "Конфликт транскрипций одного изображения. Исключите неверный вариант."
                    )
            try:
                if digest(sample["image_path"].read_bytes()) != sample["image_hash"]:
                    raise AppError("Контрольная сумма изображения не совпадает.")
                with Image.open(sample["image_path"]) as image:
                    image.verify()
            except (OSError, ValueError) as exc:
                raise AppError("Изображение примера повреждено или недоступно.") from exc
            sample["status"] = "approved"
            sample["approved_at"] = datetime.now(timezone.utc).isoformat()
            self.store.save(db, sample)
            return sample

    def revise(self, sample_id, expected_fingerprint, text, group="", uncertain=False):
        if not isinstance(text, str) or not text.strip():
            raise AppError("Текст примера не может быть пустым.")
        with self.store.connection() as db:
            sample = dict(self._checked(db, sample_id, expected_fingerprint))
            sample.update(
                id=uuid.uuid4().hex,
                text=text,
                group=group,
                uncertain=bool(uncertain),
                status="excluded" if sample["status"] == "excluded" else "draft",
                approved_at=None,
                revision=sample["revision"] + 1,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            sample["fingerprint"] = fingerprint(
                [sample["source_fingerprint"], text, group, uncertain, sample["id"]]
            )
            self.store.save(db, sample)
            db.execute(
                "UPDATE current SET sample_id=? WHERE page_hash=? AND block_id=?",
                (sample["id"], sample["page_hash"], sample["block_id"]),
            )
            return sample

    def exclude(self, sample_id):
        with self.store.connection() as db:
            historical = self.store.get(db, sample_id)
            # Exclusion applies to the logical zone even when the review dialog
            # shows a revision superseded by a pending document edit.
            sample = self.store.latest(db, historical["page_hash"], historical["block_id"])
            if sample is None:
                raise AppError("Пример отсутствует в коллекции.")
            sample["status"] = "excluded"
            self.store.save(db, sample)
            return sample

    def export(self, target, split=True):
        from chtec.dataset_export import DatasetExporter

        if Path(target).resolve().is_relative_to(self.store.folder):
            raise AppError("Сохраните экспорт вне рабочей папки коллекции.")
        return DatasetExporter().export(self.list_samples(), target, split)
