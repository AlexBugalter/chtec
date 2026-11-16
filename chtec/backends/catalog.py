"""Installed model discovery, independent of the currently resident model."""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from chtec.domain import AppError


@dataclass(frozen=True)
class ModelEntry:
    id: str
    title: str
    path: str = ""
    vision: bool | None = None

    @property
    def label(self):
        suffix = {True: "изображения", False: "только текст", None: "возможности не указаны"}
        return f"{self.title} · {suffix[self.vision]}"


def model_roots(profile):
    roots = []
    if profile.model_path:
        roots.append(Path(profile.model_path).parent)
    home = Path.home()
    for location in (home / ".cache/lm-studio/settings.json", home / ".lmstudio/settings.json"):
        try:
            folder = json.loads(location.read_text(encoding="utf-8")).get("downloadsFolder")
            if folder:
                roots.append(Path(folder))
        except (OSError, ValueError, AttributeError):
            pass
    roots.extend([home / ".lmstudio/models", home / ".cache/lm-studio/models"])
    return list(dict.fromkeys(roots))


def is_chat_candidate(identifier, architecture=""):
    # CLI also indexes diffusion weights and ASR models as "llm"/"vision".
    # Exclude identifiable non-chat families; do not infer vision from their name.
    name = (identifier + " " + architecture).lower()
    return not re.search(
        r"(?:^|[/_ .-])(?:asr|tts|vocoder|vae|mmproj|"
        r"qwen-image|wan\d*|ltxv?|latent[-_]upsampler|connectors)"
        r"(?:$|[/_ .-]|\d)",
        name,
    )


def local_catalog(profile, roots=None, cancel=None):
    if profile.name == "lmstudio":
        from chtec.backends.manager import discover_paths

        cli = profile.cli_path or discover_paths("lmstudio")[0]
        try:
            result = subprocess.run(
                [cli, "ls", "--llm", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode:
                raise AppError("LM Studio не вернул каталог. Проверьте путь CLI в настройках.")
            rows = json.loads(result.stdout)
            if not isinstance(rows, list):
                raise ValueError
            entries = [
                ModelEntry(
                    row["modelKey"],
                    row.get("displayName") or row["modelKey"],
                    vision=row.get("vision"),
                )
                for row in rows
                if row.get("modelKey")
                and is_chat_candidate(row["modelKey"], row.get("architecture", ""))
            ]
            return sorted(entries, key=lambda e: e.id.lower())
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
            raise AppError(
                "Не удалось получить установленные модели LM Studio. Проверьте CLI."
            ) from None
    entries = {}
    scanned = 0
    for root in roots if roots is not None else model_roots(profile):
        root = Path(root)
        if not root.is_dir():
            continue
        for directory, folders, files in os.walk(root, followlinks=False):
            if cancel is not None and cancel.is_set():
                raise AppError("Получение каталога отменено.")
            folders[:] = [
                f
                for f in folders
                if f not in (".git", "xet", ".cache") and not Path(directory, f).is_symlink()
            ]
            if len(Path(directory).relative_to(root).parts) >= 8:
                folders[:] = []
            for name in files:
                scanned += 1
                if scanned > 50000:
                    raise AppError(
                        "Слишком большая папка моделей. Укажите папку GGUF в настройках."
                    )
                if not name.lower().endswith(".gguf") or not is_chat_candidate(name):
                    continue
                path = Path(directory, name).resolve()
                projectors = list(path.parent.glob("*mmproj*.gguf"))
                entries[str(path)] = ModelEntry(
                    str(path), name, str(path), True if projectors else None
                )
    return sorted(entries.values(), key=lambda e: e.id.lower())


def catalog(profile, cancel=None):
    if profile.name in ("lmstudio", "unsloth"):
        return local_catalog(profile, cancel=cancel)
    from chtec.providers import create_model

    return create_model(profile.settings).catalog(cancel=cancel)
