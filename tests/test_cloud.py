from dataclasses import replace

import pytest

from chtec.domain import AppError, Page
from chtec.providers import PROVIDERS, CloudSettings, create_model


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "deepseek", "qwen", "xai"])
def test_image_and_edit_use_provider_native_protocol(provider, monkeypatch):
    from chtec.cloud import CloudModel

    settings = CloudSettings(
        provider=provider,
        base_url=PROVIDERS[provider].endpoints[0],
        api_key="secret",
        ocr_model="vision-test",
    )
    model = create_model(settings)
    calls = []

    def transport(endpoint, payload=None):
        calls.append((endpoint, payload))
        if provider == "openai":
            return {
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "Текст"}]}
                ],
            }
        if provider == "anthropic":
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Текст"}]}
        if provider == "google":
            return {
                "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Текст"}]}}]
            }
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Текст"}}]}

    monkeypatch.setattr(model, "_send", transport)
    assert model.recognize(Page(b"PNG", "synthetic", 1)) == "Текст"
    assert "UE5H" in str(calls[-1][1])
    assert model.edit("Начало", "Исправь") == "Текст"
    assert "UE5H" not in str(calls[-1][1])
    assert "secret" not in repr(model)
    assert isinstance(model, CloudModel)


def test_gemini_catalog_pagination_and_generation_filter(monkeypatch):
    model = create_model(
        CloudSettings(provider="google", base_url=PROVIDERS["google"].endpoints[0], api_key="k")
    )
    calls = []

    def send(endpoint, payload=None):
        calls.append(endpoint)
        if len(calls) == 1:
            return {
                "models": [
                    {
                        "name": "models/gemini-test",
                        "supportedGenerationMethods": ["generateContent"],
                    }
                ],
                "nextPageToken": "next",
            }
        return {
            "models": [{"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]
        }

    monkeypatch.setattr(model, "_send", send)
    assert [row.id for row in model.catalog()] == ["gemini-test"]
    assert "pageToken=next" in calls[1]


def test_cloud_rejects_incomplete_or_empty_response(monkeypatch):
    model = create_model(CloudSettings(api_key="k", ocr_model="test"))
    monkeypatch.setattr(model, "_send", lambda *a: {"status": "incomplete", "output": []})
    with pytest.raises(AppError):
        model.edit("hello", "correct")


def test_cloud_missing_key_and_text_only_model_block_image(monkeypatch):
    with pytest.raises(AppError, match="ключ"):
        create_model(CloudSettings()).models()
    model = create_model(replace(CloudSettings(api_key="k", ocr_model="text"), vision=False))
    monkeypatch.setattr(model, "_send", lambda *a: pytest.fail("image must not be sent"))
    with pytest.raises(AppError, match="изображен"):
        model.recognize(Page(b"PNG", "synthetic", 1))


def test_qwen_uses_native_catalog_endpoint_and_pagination(monkeypatch):
    model = create_model(
        CloudSettings(provider="qwen", base_url=PROVIDERS["qwen"].endpoints[0], api_key="k")
    )
    calls = []

    def send(endpoint, payload=None):
        calls.append(endpoint)
        number = len(calls)
        return {
            "success": True,
            "output": {
                "total": 2,
                "models": [
                    {
                        "model": f"qwen-{number}",
                        "name": "Vision",
                        "inference_metadata": {
                            "request_modality": ["Text", "Image"],
                            "response_modality": ["Text"],
                        },
                    }
                ],
            },
        }

    monkeypatch.setattr(model, "_send", send)
    assert [e.id for e in model.catalog()] == ["qwen-1", "qwen-2"]
    assert calls[0].startswith("/api/v1/models?")
    assert "page_no=2" in calls[1]


def test_qwen_workspace_endpoint_and_scope_are_supported():
    url = "https://llm-test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    assert CloudSettings(provider="qwen", base_url=url).validated().base_url == url
    with pytest.raises(AppError):
        CloudSettings(provider="qwen", base_url=url.replace(".com/", ".com.evil/")).validated()


def test_catalog_cancellation_stops_before_next_page(monkeypatch):
    from threading import Event

    cancel = Event()
    model = create_model(
        CloudSettings(provider="google", base_url=PROVIDERS["google"].endpoints[0], api_key="k")
    )
    calls = []

    def send(*args):
        calls.append(args)
        cancel.set()
        return {"models": [], "nextPageToken": "next"}

    monkeypatch.setattr(model, "_send", send)
    with pytest.raises(AppError, match="отмен"):
        model.catalog(cancel=cancel)
    assert len(calls) == 1


def test_xai_catalog_reads_language_model_modalities(monkeypatch):
    model = create_model(
        CloudSettings(provider="xai", base_url=PROVIDERS["xai"].endpoints[0], api_key="k")
    )
    calls = []

    def send(endpoint, payload=None):
        calls.append(endpoint)
        return {"models": [{"id": "grok-test", "input_modalities": ["text", "image"]}]}

    monkeypatch.setattr(model, "_send", send)
    assert model.catalog()[0].vision is True
    assert calls == ["language-models"]


@pytest.mark.parametrize(
    "provider,header",
    [("openai", "Authorization"), ("anthropic", "X-api-key"), ("google", "X-goog-api-key")],
)
def test_transport_authentication_and_no_redirects(provider, header, monkeypatch):
    import urllib.request

    from chtec.llm import NoRedirect

    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b'{"data": []}'

    class Opener:
        def open(self, request, **kwargs):
            captured.append(request)
            return Response()

    def build(*handlers):
        assert any(isinstance(h, NoRedirect) for h in handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    model = create_model(
        CloudSettings(
            provider=provider, base_url=PROVIDERS[provider].endpoints[0], api_key="test-private-key"
        )
    )
    model._send("models")
    assert captured[0].get_header(header).endswith("test-private-key")
    assert "test-private-key" not in captured[0].full_url


def test_http_errors_do_not_echo_provider_response_or_key(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError(
                "https://api.openai.com/v1/models",
                401,
                "private-key server body",
                {},
                io.BytesIO(b"private-key"),
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: Opener())
    model = create_model(CloudSettings(api_key="private-key"))
    with pytest.raises(AppError) as error:
        model.models()
    assert "private-key" not in str(error.value)
    assert "401" in str(error.value)
