import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chtec.domain import AppError, Page
from chtec.llm import LocalModel, LocalSettings


@pytest.fixture
def server():
    state = {"requests": [], "status": 200, "finish": "stop", "text": "Рукопись"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["get_path"] = self.path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "local-vision"}]}).encode())

        def do_POST(self):
            state["requests"].append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", "https://example.com/stolen")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {"content": state["text"]},
                                "finish_reason": state["finish"],
                            }
                        ]
                    }
                ).encode()
            )

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}/v1"
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://192.168.1.10:8000/v1",
        "http://127.0.0.1.evil.com/v1",
        "http://user:pass@localhost/v1",
        "file:///tmp/model",
        "http://localhost/v1?next=cloud",
    ],
)
def test_rejects_non_local_or_ambiguous_urls(url):
    with pytest.raises(AppError):
        LocalSettings(base_url=url).validated()


def test_models_and_vision_payload(server):
    model = LocalModel(LocalSettings(base_url=server["url"], ocr_model="local-vision"))
    assert model.models() == ["local-vision"]
    result = model.recognize(Page(b"image-bytes", "scan.png", 1), "Сохрани переносы")
    assert result == "Рукопись"
    payload = server["requests"][0]
    assert payload["model"] == "local-vision"
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_root_api_endpoint_stays_on_same_local_server(server):
    model = LocalModel(LocalSettings(base_url=server["url"]))
    model._request("/api/inference/loaded-models")
    assert server["get_path"] == "/api/inference/loaded-models"


def test_layout_request_has_json_contract_and_image(server):
    model = LocalModel(LocalSettings(base_url=server["url"], ocr_model="vision"))
    model.layout(Page(b"image", "scan", 1))
    payload = server["requests"][-1]
    assert payload["model"] == "vision"
    assert "rect" in payload["messages"][0]["content"]
    assert "angle" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_unsloth_non_thinking_flag_is_forwarded_only_when_explicit(server):
    model = LocalModel(
        LocalSettings(base_url=server["url"], ocr_model="vision", enable_thinking=False)
    )
    assert model.recognize(Page(b"image", "scan", 1)) == "Рукопись"
    assert server["requests"][-1]["enable_thinking"] is False
    LocalModel(LocalSettings(base_url=server["url"], edit_model="editor")).edit("текст", "правка")
    assert "enable_thinking" not in server["requests"][-1]


@pytest.mark.parametrize("text", ["Вотми" + "к" * 500, "Дмитрий\n" * 80])
def test_repeating_truncated_output_does_not_recommend_more_tokens(server, text):
    server["finish"] = "length"
    server["text"] = text
    with pytest.raises(AppError, match="повторяющемся"):
        LocalModel(LocalSettings(base_url=server["url"], ocr_model="vision")).recognize(
            Page(b"image", "scan", 1)
        )


def test_editor_uses_separate_model_without_image(server):
    model = LocalModel(LocalSettings(base_url=server["url"], edit_model="local-editor"))
    assert model.edit("Исходный текст", "Исправь пунктуацию") == "Рукопись"
    assert server["requests"][0]["model"] == "local-editor"
    assert "Исходный текст" in server["requests"][0]["messages"][1]["content"]


@pytest.mark.parametrize("finish,text", [("length", "обрезано"), ("stop", "  ")])
def test_rejects_truncated_or_empty_output(server, finish, text):
    server.update(finish=finish, text=text)
    with pytest.raises(AppError):
        LocalModel(LocalSettings(base_url=server["url"], edit_model="editor")).edit("x", "fix")


@pytest.mark.parametrize("status", [302, 401, 500])
def test_http_errors_and_redirect_are_not_followed(server, status):
    server["status"] = status
    with pytest.raises(AppError):
        LocalModel(LocalSettings(base_url=server["url"], edit_model="editor")).edit("x", "fix")
    assert len(server["requests"]) == 1
