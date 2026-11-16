import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from chtec.backends.profiles import BackendProfile, BackendProfiles, default_profiles
from chtec.domain import AppError
from chtec.llm import LocalSettings


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> LocalSettings:
        return self.load_profiles().active.settings

    def load_profiles(self) -> BackendProfiles:
        if not self.path.exists():
            return default_profiles()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "profiles" not in data:
                data.pop("api_key", None)
                return default_profiles(LocalSettings(**data).validated())
            profiles = {}
            for name, value in data["profiles"].items():
                value = dict(value)
                settings = dict(value.pop("settings"))
                settings.pop("api_key", None)
                profiles[name] = BackendProfile(settings=LocalSettings(**settings), **value)
            return BackendProfiles(data["selected"], profiles).validated()
        except (OSError, ValueError, TypeError, AttributeError, KeyError, AppError) as exc:
            raise AppError(
                f"Не удалось прочитать настройки {self.path}. "
                "Переименуйте файл или исправьте настройки подключения."
            ) from exc

    def save(self, settings: LocalSettings) -> None:
        profiles = self.load_profiles()
        profiles.active.settings = settings
        self.save_profiles(profiles)

    def save_profiles(self, profiles: BackendProfiles) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            profiles.validated()
            data = {
                "version": 2,
                "selected": profiles.selected,
                "profiles": {name: asdict(p) for name, p in profiles.profiles.items()},
            }
            for profile in data["profiles"].values():
                profile["settings"].pop("api_key", None)
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, mode="w", encoding="utf-8", delete=False
            ) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        except OSError as exc:
            raise AppError("Не удалось сохранить настройки подключения.") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
