"""Small native API adapters. Images are inline; no external file upload lifecycle."""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from chtec.domain import AppError
from chtec.llm import LocalModel, NoRedirect
from chtec.providers import PROVIDERS


class CloudModel(LocalModel):
    def __init__(self, settings):
        self.settings = settings.validated()
        self.protocol = PROVIDERS[settings.provider].protocol

    def _send(self, endpoint, payload=None):
        if not self.settings.api_key.strip():
            raise AppError("Введите API-ключ выбранного поставщика в настройках приложения.")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.protocol == "anthropic":
            headers.update({"x-api-key": self.settings.api_key, "anthropic-version": "2023-06-01"})
        elif self.protocol == "gemini":
            headers["x-goog-api-key"] = self.settings.api_key
        else:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        base = self.settings.base_url
        if endpoint.startswith("/"):
            parts = urllib.parse.urlsplit(base)
            base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        request = urllib.request.Request(
            base + ("/" if not endpoint.startswith("/") else "") + endpoint,
            headers=headers,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            timeout = self.settings.timeout
            if getattr(self, "_catalog_deadline", None):
                self._check_catalog()
                timeout = min(timeout, max(0.1, self._catalog_deadline - time.monotonic()))
            with opener.open(request, timeout=timeout) as response:
                body = response.read(16 * 1024 * 1024 + 1)
            if len(body) > 16 * 1024 * 1024:
                raise AppError("Ответ API превышает 16 MiB.")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            hint = {
                400: "Проверьте поддержку изображений и параметры модели.",
                401: "Проверьте API-ключ.",
                403: "Проверьте права и доступность API.",
                404: "Обновите список моделей и выберите доступную модель.",
                429: "Исчерпан лимит запросов или баланс API.",
            }.get(exc.code, "Повторите позже.")
            raise AppError(
                f"{PROVIDERS[self.settings.provider].title}: HTTP {exc.code}. {hint}"
            ) from None
        except (TimeoutError, socket.timeout):
            raise AppError("Истёк тайм-аут облачного API. Предыдущий текст сохранён.") from None
        except (urllib.error.URLError, OSError):
            raise AppError("Нет связи с выбранным облачным API. Проверьте подключение.") from None
        except (ValueError, UnicodeError):
            raise AppError("Облачный API вернул некорректный JSON.") from None

    def _check_catalog(self):
        if self._catalog_cancel is not None and self._catalog_cancel.is_set():
            raise AppError("Получение каталога отменено.")
        if time.monotonic() >= self._catalog_deadline:
            raise AppError("Истекло общее время получения каталога моделей (45 секунд).")

    def catalog(self, cancel=None):
        self._catalog_deadline = time.monotonic() + 45
        self._catalog_cancel = cancel
        try:
            return self._catalog()
        finally:
            self._catalog_deadline = None
            self._catalog_cancel = None

    def _catalog(self):
        from chtec.backends.catalog import ModelEntry

        if self.settings.provider == "qwen":
            return self._qwen_catalog()

        endpoint = "models?pageSize=1000" if self.protocol == "gemini" else "models"
        if self.settings.provider == "xai":
            endpoint = "language-models"
        result, seen = {}, set()
        for _ in range(100):
            self._check_catalog()
            if endpoint in seen:
                raise AppError("API повторяет страницу каталога моделей.")
            seen.add(endpoint)
            data = self._send(endpoint)
            try:
                rows = data[
                    "models"
                    if self.protocol == "gemini" or self.settings.provider == "xai"
                    else "data"
                ]
                if not isinstance(rows, list):
                    raise ValueError
                for row in rows:
                    if self.protocol == "gemini" and "generateContent" not in row.get(
                        "supportedGenerationMethods", []
                    ):
                        continue
                    identifier = row["name" if self.protocol == "gemini" else "id"]
                    if self.protocol == "gemini":
                        identifier = identifier.removeprefix("models/")
                    if not isinstance(identifier, str) or not identifier:
                        raise ValueError
                    modalities = row.get("input_modalities") or row.get("inputModalities")
                    vision = (
                        "image" in [str(m).lower() for m in modalities]
                        if isinstance(modalities, list)
                        else None
                    )
                    result[identifier] = ModelEntry(
                        identifier,
                        row.get("display_name") or row.get("displayName") or identifier,
                        vision=vision,
                    )
                token = data.get("nextPageToken")
                if token and self.protocol == "gemini":
                    endpoint = "models?pageSize=1000&pageToken=" + urllib.parse.quote(
                        token, safe=""
                    )
                elif data.get("has_more") and self.protocol == "anthropic":
                    endpoint = "models?after_id=" + urllib.parse.quote(data["last_id"], safe="")
                else:
                    return sorted(result.values(), key=lambda e: e.id.lower())
            except (KeyError, TypeError, ValueError, AttributeError):
                raise AppError("Некорректный каталог моделей от поставщика.") from None
        raise AppError("Слишком много страниц каталога моделей.")

    def _qwen_catalog(self):
        from chtec.backends.catalog import ModelEntry

        result = {}
        count = 0
        for page in range(1, 101):
            self._check_catalog()
            data = self._send(f"/api/v1/models?page_no={page}&page_size=100&language=en-US")
            try:
                if data.get("success") is not True:
                    raise ValueError
                output = data["output"]
                rows = output["models"]
                if not isinstance(rows, list):
                    raise ValueError
                for row in rows:
                    metadata = row.get("inference_metadata") or {}
                    outputs = [m.lower() for m in metadata.get("response_modality", [])]
                    if outputs and "text" not in outputs:
                        continue
                    inputs = metadata.get("request_modality")
                    vision = (
                        "image" in [m.lower() for m in inputs] if isinstance(inputs, list) else None
                    )
                    identifier = row["model"]
                    if not isinstance(identifier, str) or not identifier:
                        raise ValueError
                    result[identifier] = ModelEntry(
                        identifier, row.get("name") or identifier, vision=vision
                    )
                count += len(rows)
                if count >= output["total"]:
                    return sorted(result.values(), key=lambda e: e.id.lower())
                if not rows:
                    raise ValueError
            except (KeyError, ValueError, TypeError, AttributeError):
                raise AppError("Некорректный каталог моделей Alibaba Qwen.") from None
        raise AppError("Слишком много страниц каталога Alibaba Qwen.")

    def models(self):
        return [row.id for row in self.catalog()]

    def _request(self, endpoint, payload=None):
        if endpoint != "chat/completions":
            return self._send(endpoint, payload)
        if self.protocol == "chat":
            # Only send extensions to providers that document them.
            payload.pop("enable_thinking", None)
            return self._send(endpoint, payload)
        messages = payload["messages"]
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        users = [m for m in messages if m["role"] != "system"]
        try:
            if self.protocol == "responses":
                inputs = []
                for message in users:
                    content = message["content"]
                    if isinstance(content, str):
                        content = [{"type": "text", "text": content}]
                    inputs.append(
                        {
                            "role": message["role"],
                            "content": [
                                {"type": "input_text", "text": p["text"]}
                                if p["type"] == "text"
                                else {"type": "input_image", "image_url": p["image_url"]["url"]}
                                for p in content
                            ],
                        }
                    )
                data = self._send(
                    "responses",
                    {
                        "model": payload["model"],
                        "instructions": system,
                        "input": inputs,
                        "max_output_tokens": payload["max_tokens"],
                        "store": False,
                    },
                )
                finish = "stop" if data.get("status") == "completed" else "length"
                text = "\n".join(
                    p["text"]
                    for item in data.get("output", [])
                    if item.get("type") == "message"
                    for p in item.get("content", [])
                    if p.get("type") == "output_text"
                )
            elif self.protocol == "anthropic":
                converted = []
                for message in users:
                    content = message["content"]
                    if isinstance(content, list):
                        parts = []
                        for part in content:
                            if part["type"] == "text":
                                parts.append(part)
                            else:
                                url = part["image_url"]["url"]
                                parts.append(
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": url.split(",", 1)[1],
                                        },
                                    }
                                )
                        content = parts
                    converted.append({"role": message["role"], "content": content})
                data = self._send(
                    "messages",
                    {
                        "model": payload["model"],
                        "system": system,
                        "messages": converted,
                        "max_tokens": payload["max_tokens"],
                    },
                )
                finish = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}.get(
                    data.get("stop_reason"), "blocked"
                )
                text = "\n".join(
                    p["text"] for p in data.get("content", []) if p.get("type") == "text"
                )
            else:
                contents = []
                for message in users:
                    content = message["content"]
                    if isinstance(content, str):
                        content = [{"type": "text", "text": content}]
                    parts = [
                        {"text": p["text"]}
                        if p["type"] == "text"
                        else {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": p["image_url"]["url"].split(",", 1)[1],
                            }
                        }
                        for p in content
                    ]
                    contents.append({"role": "user", "parts": parts})
                identifier = urllib.parse.quote(payload["model"].removeprefix("models/"), safe="")
                data = self._send(
                    f"models/{identifier}:generateContent",
                    {
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": contents,
                        "generationConfig": {"maxOutputTokens": payload["max_tokens"]},
                    },
                )
                candidate = data.get("candidates", [{}])[0]
                finish = {"STOP": "stop", "MAX_TOKENS": "length"}.get(
                    candidate.get("finishReason"), "blocked"
                )
                text = "\n".join(
                    p["text"]
                    for p in candidate.get("content", {}).get("parts", [])
                    if isinstance(p.get("text"), str) and not p.get("thought")
                )
            return {"choices": [{"finish_reason": finish, "message": {"content": text}}]}
        except (KeyError, TypeError, IndexError, AttributeError):
            raise AppError("Некорректная структура ответа облачной модели.") from None

    def _complete(self, model, messages):
        if self.settings.vision is False and any(
            isinstance(m["content"], list)
            and any(p.get("type") == "image_url" for p in m["content"])
            for m in messages
        ):
            raise AppError("Выбранная модель не поддерживает изображения. Выберите vision-модель.")
        return super()._complete(model, messages)
