"""Portable, validated and atomic exports for local vision fine tuning."""

import json
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from chtec.dataset import INSTRUCTION, digest, validate_text
from chtec.domain_errors import AppError

LOADER = '''"""Load portable vision messages, materializing local images before training."""
import json
from pathlib import Path
from PIL import Image

def load_dataset(jsonl_path):
    path = Path(jsonl_path).resolve()
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        for message in row["messages"]:
            for item in message["content"]:
                if item["type"] == "image":
                    image_path = (path.parent / item["image"]).resolve()
                    if not image_path.is_relative_to(path.parent):
                        raise ValueError("Image path escapes dataset")
                    with Image.open(image_path) as image:
                        item["image"] = image.convert("RGB").copy()
        result.append(row)
    return result
'''


class DatasetExporter:
    def export(self, samples, target, split=True):
        target = Path(target).resolve()
        if target.exists():
            raise AppError("Папка экспорта уже существует. Выберите новую папку.")
        approved = [s for s in samples if s["status"] == "approved" and not s.get("deleted")]
        if not approved:
            raise AppError("Нет подтверждённых примеров для экспорта.")
        hashes = {}
        unique = []
        duplicates = 0
        for sample in approved:
            validate_text(sample["text"], sample.get("uncertain", False))
            try:
                data = sample["image_path"].read_bytes()
                with Image.open(sample["image_path"]) as image:
                    image.verify()
            except (OSError, ValueError) as exc:
                raise AppError("Изображение примера недоступно или повреждено.") from exc
            if digest(data) != sample["image_hash"]:
                raise AppError("Контрольная сумма изображения не совпадает.")
            prior = hashes.get(sample["image_hash"])
            if prior is not None:
                if prior != sample["text"]:
                    raise AppError("Конфликт транскрипций одного изображения.")
                duplicates += 1
            else:
                hashes[sample["image_hash"]] = sample["text"]
                unique.append(sample)
        # Connected components bind page hashes, explicit document groups and duplicate
        # images transitively, even when one page was assigned multiple manual groups.
        parents = {}

        def find(key):
            parents.setdefault(key, key)
            if parents[key] != key:
                parents[key] = find(parents[key])
            return parents[key]

        def union(a, b):
            a, b = find(a), find(b)
            parents[max(a, b)] = min(a, b)

        for s in approved:
            p = "page:" + s["page_hash"]
            union(p, "image:" + s["image_hash"])
            if s["group"]:
                union(p, "group:" + s["group"])
        roots = sorted({find("page:" + s["page_hash"]) for s in unique})
        roots.sort(key=lambda value: digest(value.encode("utf-8")))
        warnings = []
        assignment = {root: "train" for root in roots}
        if split and len(roots) >= 3:
            reserve = max(1, len(roots) // 10)
            for root in roots[:reserve]:
                assignment[root] = "test"
            for root in roots[reserve : 2 * reserve]:
                assignment[root] = "validation"
        elif split:
            warnings.append("Недостаточно независимых групп: все примеры оставлены в train.")
        splits = {name: [] for name in ("train", "validation", "test")}
        rows = []
        messages = []
        for s in sorted(unique, key=lambda sample: sample["id"]):
            part = assignment[find("page:" + s["page_hash"])]
            splits[part].append(s["id"])
            image = "images/" + s["image_hash"] + ".png"
            rows.append(
                {
                    "id": s["id"],
                    "image": image,
                    "text": s["text"],
                    "instruction": INSTRUCTION,
                    "split": part,
                }
            )
            messages.append(
                {
                    "id": s["id"],
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": INSTRUCTION},
                                {"type": "image", "image": image},
                            ],
                        },
                        {"role": "assistant", "content": [{"type": "text", "text": s["text"]}]},
                    ],
                }
            )
        manifest = {
            "format_version": 1,
            "instruction": INSTRUCTION,
            "split_method": "sha256-connected-document-groups-v1",
            "splits": splits,
            "samples": [
                {"id": s["id"], "page_hash": s["page_hash"], "image_hash": s["image_hash"]}
                for s in sorted(unique, key=lambda x: x["id"])
            ],
        }
        report = {
            "exported": len(unique),
            "duplicates": duplicates,
            "skipped": len(samples) - len(approved),
            "groups": len(roots),
            "warnings": warnings,
            "splits": {k: len(v) for k, v in splits.items()},
        }
        stage = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".dataset-export-", dir=target.parent))
            (stage / ".gitignore").write_text("*\n", encoding="utf-8")
            (stage / "images").mkdir()
            for s in unique:
                shutil.copyfile(s["image_path"], stage / "images" / (s["image_hash"] + ".png"))

            def jsonl(name, values):
                (stage / name).write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values),
                    encoding="utf-8",
                )

            jsonl("data.jsonl", rows)
            jsonl("messages.jsonl", messages)
            for part, ids in splits.items():
                jsonl(part + ".jsonl", [r for r in rows if r["id"] in ids])
                jsonl(part + "_messages.jsonl", [r for r in messages if r["id"] in ids])
            for name, value in [("manifest.json", manifest), ("report.json", report)]:
                (stage / name).write_text(
                    json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            (stage / "load_dataset.py").write_text(LOADER, encoding="utf-8")
            (stage / "README.md").write_text(
                "# Local OCR vision dataset\n\nOnly explicitly approved pairs are included. "
                "data.jsonl uses relative PNG paths; messages.jsonl contains vision dialogs. "
                'Use load_dataset.py: load_dataset("train_messages.jsonl") '
                "to materialize PIL images locally. "
                "Install Pillow in your training environment. Pass the resulting messages "
                "to the chosen model processor and chat template; validate one batch before "
                "Unsloth training. The format is not guaranteed for every model. "
                "Do not train before reviewing report.json and the independent group splits "
                "in manifest.json. "
                "No network requests are performed by this loader.\n",
                encoding="utf-8",
            )
            if target.exists():
                raise AppError("Папка экспорта уже существует.")
            os.rename(stage, target)
            stage = None
            return report
        except OSError as exc:
            raise AppError(f"Не удалось экспортировать датасет: {exc}") from exc
        finally:
            if stage is not None:
                shutil.rmtree(stage)
