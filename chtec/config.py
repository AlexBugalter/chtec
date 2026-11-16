import json
import os
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from chtec.backends.profiles import BackendProfile, BackendProfiles, default_profiles
from chtec.credentials import CredentialStore
from chtec.domain import AppError
from chtec.llm import LocalSettings
from chtec.providers import PROVIDERS, CloudSettings


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.credentials = CredentialStore(path.parent / (path.stem + "-keys"))
        self.warnings = []

    def load(self) -> LocalSettings:
        return self.load_profiles().active.settings

    def load_profiles(self) -> BackendProfiles:
        self.warnings = []
        if not self.path.exists():
            return default_profiles()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "profiles" not in data:
                data.pop("api_key", None)
                return default_profiles(LocalSettings(**data).validated())
            profiles = default_profiles().profiles
            for name, value in data["profiles"].items():
                value = dict(value)
                settings = dict(value.pop("settings"))
                settings.pop("api_key", None)
                settings_type = (
                    CloudSettings if name not in ("lmstudio", "unsloth") else LocalSettings
                )
                parsed = settings_type(**settings).validated()
                if isinstance(parsed, CloudSettings):
                    try:
                        key = self.credentials.get(name, parsed.base_url)
                    except AppError:
                        key = ""
                        self.warnings.append(
                            PROVIDERS[name].title
                            + ': ключ недоступен этому пользователю Windows; '
                            'введите его заново.'
                        )
                    parsed = replace(parsed, api_key=key)
                profiles[name] = BackendProfile(settings=parsed, **value)
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
                "version": 3,
                "selected": profiles.selected,
                "profiles": {name: asdict(p) for name, p in profiles.profiles.items()},
            }
            for profile in data["profiles"].values():
                profile["settings"].pop("api_key", None)
            for name, profile in profiles.profiles.items():
                if isinstance(profile.settings, CloudSettings):
                    self.credentials.set(name, profile.settings.base_url, profile.settings.api_key)
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
