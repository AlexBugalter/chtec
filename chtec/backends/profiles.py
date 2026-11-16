from dataclasses import dataclass, field
from urllib.parse import urlsplit

from chtec.domain import AppError
from chtec.llm import LocalSettings
from chtec.providers import PROVIDERS, CloudSettings


@dataclass
class BackendProfile:
    name: str
    settings: LocalSettings = field(default_factory=LocalSettings)
    cli_path: str = ""
    app_path: str = ""
    model_path: str = ""
    context_length: int = 8192


@dataclass
class BackendProfiles:
    selected: str
    profiles: dict[str, BackendProfile]

    @property
    def active(self):
        return self.profiles[self.selected]

    def switch(self, name):
        if name not in self.profiles:
            raise AppError("Неизвестная система распознавания.")
        self.selected = name

    def validated(self):
        if set(self.profiles) != set(PROVIDERS) or self.selected not in self.profiles:
            raise AppError("Повреждены профили локальных систем.")
        ports = []
        for name, profile in self.profiles.items():
            if profile.name != name or not 512 <= profile.context_length <= 131072:
                raise AppError("Проверьте профиль и размер контекста.")
            profile.settings = profile.settings.validated()
            if name not in ("lmstudio", "unsloth"):
                if (
                    not isinstance(profile.settings, CloudSettings)
                    or profile.settings.provider != name
                ):
                    raise AppError("Настройки API не соответствуют выбранному поставщику.")
                continue
            if isinstance(profile.settings, CloudSettings):
                raise AppError("Локальная система требует локального адреса API.")
            parts = urlsplit(profile.settings.base_url)
            ports.append(parts.port or (443 if parts.scheme == "https" else 80))
        if len(set(ports)) != len(ports):
            raise AppError("LM Studio и Unsloth должны использовать разные порты.")
        return self


def default_profiles(settings=None):
    result = BackendProfiles(
        "lmstudio",
        {
            "lmstudio": BackendProfile("lmstudio", settings or LocalSettings()),
            "unsloth": BackendProfile(
                "unsloth", LocalSettings(base_url="http://127.0.0.1:8890/v1")
            ),
        },
    )
    for name, provider in PROVIDERS.items():
        if provider.protocol != "local":
            result.profiles[name] = BackendProfile(
                name, CloudSettings(provider=name, base_url=provider.endpoints[0])
            )
    return result
