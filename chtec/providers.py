"""Provider registry; local endpoint restrictions remain in LocalSettings."""

import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from chtec.domain import AppError
from chtec.llm import LocalModel, LocalSettings


@dataclass(frozen=True)
class Provider:
    title: str
    protocol: str
    endpoints: tuple[str, ...] = ()


PROVIDERS = {
    "lmstudio": Provider("LM Studio", "local"),
    "unsloth": Provider("Unsloth", "local"),
    "openai": Provider("OpenAI", "responses", ("https://api.openai.com/v1",)),
    "anthropic": Provider("Anthropic Claude", "anthropic", ("https://api.anthropic.com/v1",)),
    "google": Provider(
        "Google Gemini", "gemini", ("https://generativelanguage.googleapis.com/v1beta",)
    ),
    "deepseek": Provider("DeepSeek", "chat", ("https://api.deepseek.com/v1",)),
    "qwen": Provider(
        "Alibaba Qwen",
        "chat",
        (
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
        ),
    ),
    "xai": Provider("xAI Grok", "chat", ("https://api.x.ai/v1",)),
}


@dataclass(frozen=True)
class CloudSettings(LocalSettings):
    base_url: str = "https://api.openai.com/v1"
    provider: str = "openai"
    vision: bool | None = None

    def validated(self):
        provider = PROVIDERS.get(self.provider)
        url = self.base_url.strip().rstrip("/")
        parts = urlsplit(url)
        workspace = self.provider == "qwen" and re.fullmatch(
            r"[a-zA-Z0-9-]+\.(?:ap-southeast-1|cn-beijing|cn-hongkong|eu-central-1|ap-northeast-1)"
            r"\.maas\.aliyuncs\.com",
            parts.hostname or "",
        )
        official_workspace = bool(
            workspace
            and parts.scheme == "https"
            and parts.netloc == parts.hostname
            and parts.path == "/compatible-mode/v1"
            and not parts.query
            and not parts.fragment
        )
        if (
            not provider
            or provider.protocol == "local"
            or (url not in provider.endpoints and not official_workspace)
        ):
            raise AppError("Выберите официальный HTTPS-адрес выбранного поставщика API.")
        # Reuse all scalar limits without weakening the local host guard.
        LocalSettings(
            timeout=self.timeout,
            max_tokens=self.max_tokens,
            api_key=self.api_key,
            enable_thinking=self.enable_thinking,
        ).validated()
        return replace(self, base_url=url)


def create_model(settings):
    if isinstance(settings, CloudSettings):
        from chtec.cloud import CloudModel

        return CloudModel(settings)
    return LocalModel(settings)


def destination(settings):
    if isinstance(settings, CloudSettings):
        host = urlsplit(settings.base_url).hostname
        return f"Облако · {PROVIDERS[settings.provider].title} · {host}"
    return "Локальный API"
