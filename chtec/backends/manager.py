"""Launch official CLIs; own only processes/model instances created here."""

import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from chtec.backends.lifetime import ProcessLifetime
from chtec.domain import AppError
from chtec.llm import LocalModel


class StartupOutput:
    def __init__(self):
        self.api_key = ""
        self.port = None
        self.ready = False
        self.safe_lines = deque(maxlen=15)

    def feed(self, line):
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if line.startswith("UNSLOTH_START_PORT:"):
            self.port = int(line.split(":", 1)[1].strip())
        elif line.startswith("UNSLOTH_START_API_KEY:"):
            self.api_key = line.split(":", 1)[1].strip()
        elif line.startswith("API Key:"):
            self.api_key = line.split(":", 1)[1].strip()
            self.ready = True
        elif not any(word in line.lower() for word in ("key", "token", "bearer", "secret")):
            self.safe_lines.append(line.replace(self.api_key, "[скрыто]") if self.api_key else line)

    def __repr__(self):
        return f"StartupOutput(port={self.port}, ready={self.ready})"


def discover_paths(name):
    home = Path.home()
    if name == "unsloth":
        return str(home / ".unsloth/studio/unsloth_studio/Scripts/unsloth.exe"), ""
    cli = shutil.which("lms") or str(home / ".cache/lm-studio/bin/lms.exe")
    app = home / "AppData/Local/Programs/lm-studio/LM Studio/LM Studio.exe"
    return cli, str(app)


def unsloth_arguments(profile):
    model = Path(profile.model_path)
    if not model.is_file() or model.suffix.lower() != ".gguf":
        raise AppError("Выберите существующий локальный файл модели GGUF для Unsloth.")
    settings = profile.settings.validated()
    parts = urlsplit(settings.base_url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.path != "/v1":
        raise AppError("Для автозапуска используйте http://127.0.0.1:ПОРТ/v1.")
    return [
        "run",
        "--model",
        str(model.resolve()),
        "--port",
        str(parts.port or 80),
        "--host",
        "127.0.0.1",
        "--api-only",
        "--disable-tools",
        "--no-cloudflare",
        "--max-seq-length",
        str(profile.context_length),
        "--api-key-name",
        "chtec",
        "--start-api-key-marker",
        "--silent",
    ]


def loaded_unsloth_model(client):
    # The catalog scans every disk model and can stall startup. This endpoint
    # reports resident instances without a disk scan (Unsloth Studio API).
    try:
        rows = client._request("/api/inference/loaded-models")["data"]
        loaded = {
            row["id"]
            for row in rows
            if row.get("loaded") is True and isinstance(row.get("id"), str) and not row.get("task")
        }
        # Quant-pinned aliases can point to the same resident model.
        unpinned = {name for name in loaded if ":" not in name}
        candidates = unpinned or loaded
        if len(candidates) == 1:
            return candidates.pop()
    except (TypeError, KeyError, AttributeError):
        pass
    raise AppError("Unsloth не подтвердил единственную загруженную текстовую/vision-модель.")


class BackendManager:
    def __init__(self):
        self.process = None
        self.lm_instance = None
        self.runtime = None
        self.backend = None
        self.lifetime = None
        self.pending_load = None

    @staticmethod
    def check_cancel(cancel):
        if cancel.is_set():
            raise AppError("Активация отменена.")

    @staticmethod
    def _spawn(args, **kwargs):
        return subprocess.Popen(
            args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs
        )

    def _command(self, args, cancel, timeout=120, defer_cancel=False):
        # Output stays in memory; never expose CLI output that may include credentials.
        proc = self._spawn(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        retained = False
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    output, _ = proc.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if defer_cancel and time.monotonic() > deadline:
                        # Killing a CLI client does not cancel its server-side model load.
                        # Retain ownership and unload after the real operation completes.
                        retained = True
                        self._retain_late_load(proc)
                        raise AppError(
                            "LM Studio ещё загружает модель. "
                            "Чтец продолжает контролировать загрузку "
                            "и выгрузит её после завершения. Переключение пока заблокировано."
                        )
                    if cancel.is_set() and not defer_cancel or time.monotonic() > deadline:
                        self._kill_tree(proc)
                        self.check_cancel(cancel)
                        raise AppError("Истекло время выполнения команды локального сервера.")
            if proc.returncode:
                raise AppError(
                    "Команда локального сервера завершилась с ошибкой. Проверьте модель и путь CLI."
                )
            return output.decode("utf-8", errors="replace")
        finally:
            if not retained and proc.stdout:
                proc.stdout.close()

    def _retain_late_load(self, process):
        state = {"done": threading.Event(), "error": None}
        cli, identifier = self.lm_instance
        self.pending_load = state

        def cleanup():
            try:
                process.communicate()
                self._unload_instance(cli, identifier)
            except Exception:
                state["error"] = AppError(
                    "Не удалось выгрузить поздно загруженную модель LM Studio."
                )
            finally:
                if process.stdout:
                    process.stdout.close()
                state["done"].set()

        # Preserve cleanup even if the UI closes while the server finishes loading.
        threading.Thread(target=cleanup, name="chtec-late-load-cleanup", daemon=False).start()

    def _unload_instance(self, cli, identifier):
        try:
            rows = json.loads(self._command([cli, "ps", "--json"], threading.Event(), 30))
            if not isinstance(rows, list):
                raise ValueError
            if any(row.get("identifier") == identifier for row in rows):
                self._command([cli, "unload", identifier], threading.Event(), 60)
        except (ValueError, TypeError, AttributeError) as exc:
            raise AppError("Не удалось проверить загруженные модели LM Studio.") from exc

    @staticmethod
    def _kill_tree(proc):
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=20,
                )
            else:
                proc.terminate()
            proc.wait(timeout=20)

    def deactivate(self):
        if self.pending_load:
            if not self.pending_load["done"].is_set():
                raise AppError("Дождитесь завершения и автоматической выгрузки модели LM Studio.")
            if self.pending_load["error"]:
                # Transient CLI failure must be recoverable after LM Studio returns.
                self._unload_instance(*self.lm_instance)
            self.pending_load = None
            self.lm_instance = None
        if self.lifetime is not None:
            self.lifetime.close()
            self.lifetime = None
        if self.process is not None:
            self._kill_tree(self.process)
            if self.process.stdout:
                self.process.stdout.close()
            self.process = None
        if self.lm_instance:
            cli, identifier = self.lm_instance
            # A failed/cancelled load may never have created the instance.
            self._unload_instance(cli, identifier)
            self.lm_instance = None
        self.runtime = None
        self.backend = None

    def activate(self, profile, cancel, progress):
        self.check_cancel(cancel)
        settings = profile.settings.validated()
        cli_default, app_default = discover_paths(profile.name)
        cli = profile.cli_path or cli_default
        if not Path(cli).is_file():
            raise AppError(
                "Не найден официальный CLI. Укажите путь в настройках выбранной системы."
            )
        # Validate before stopping a currently working model.
        args = unsloth_arguments(profile) if profile.name == "unsloth" else None
        if profile.name == "lmstudio" and not settings.ocr_model:
            raise AppError("Укажите ключ локальной модели LM Studio в настройках.")
        progress("Освобождение предыдущей модели…")
        self.deactivate()
        try:
            if profile.name == "unsloth":
                runtime = self._start_unsloth(cli, args, settings, cancel, progress)
            else:
                runtime = self._start_lm(
                    cli,
                    profile.app_path or app_default,
                    settings,
                    profile.context_length,
                    cancel,
                    progress,
                )
            self.check_cancel(cancel)
            self.runtime = runtime
            self.backend = profile.name
            return runtime
        except Exception:
            if not self.pending_load:
                self.deactivate()
            raise

    def _start_unsloth(self, cli, args, settings, cancel, progress):
        parts = urlsplit(settings.base_url)
        port = parts.port or 80
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise AppError(
                    f"Порт {port} уже занят. Для автозапуска выберите свободный порт; "
                    "чужой сервер не остановлен."
                )
        progress("Запуск Unsloth и загрузка локальной модели (до 15 минут)…")
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        self.process = self._spawn(
            [cli, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        self.lifetime = ProcessLifetime()
        self.lifetime.attach(self.process)
        lines = queue.Queue(maxsize=256)
        proc = self.process

        def read():
            try:
                for line in proc.stdout:
                    # Startup is consumed below; later logs must not grow unbounded.
                    put_line(line)
            except (OSError, ValueError):
                pass  # Owned stdout can close during cancellation/exit.
            finally:
                put_line(None)

        def put_line(line):
            try:
                lines.put_nowait(line)
            except queue.Full:
                try:
                    lines.get_nowait()
                except queue.Empty:
                    pass
                lines.put_nowait(line)

        threading.Thread(target=read, daemon=True).start()
        output = StartupOutput()
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            self.check_cancel(cancel)
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                raise AppError(
                    "Unsloth завершился до загрузки модели. "
                    "Проверьте GGUF, mmproj и свободную память."
                )
            output.feed(line)
            if output.port is not None and output.port != port:
                raise AppError(
                    "Unsloth изменил порт при запуске. "
                    "Активация остановлена во избежание смешения серверов."
                )
            if output.ready and output.api_key and output.port == port:
                runtime = replace(
                    settings,
                    api_key=output.api_key,
                    timeout=max(settings.timeout, 300),
                    enable_thinking=False,
                )
                model_id = loaded_unsloth_model(LocalModel(replace(runtime, timeout=10)))
                return replace(runtime, ocr_model=model_id, edit_model=model_id)
        raise AppError("Unsloth не успел загрузить модель за 15 минут.")

    def _start_lm(self, cli, app, settings, context, cancel, progress):
        parts = urlsplit(settings.base_url)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.path != "/v1":
            raise AppError("Для автозапуска используйте http://127.0.0.1:ПОРТ/v1.")
        progress("Запуск LM Studio…")
        if Path(app).is_file():
            self._spawn([app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # CLI can briefly fail while the desktop starts.
        deadline = time.monotonic() + 60
        while True:
            self.check_cancel(cancel)
            try:
                self._command(
                    [
                        cli,
                        "server",
                        "start",
                        "--port",
                        str(parts.port or 80),
                        "--bind",
                        "127.0.0.1",
                    ],
                    cancel,
                    30,
                )
                break
            except AppError:
                if time.monotonic() > deadline or cancel.is_set():
                    raise
                cancel.wait(1)
        progress("Загрузка выбранной модели LM Studio…")
        identifier = "chtec-" + uuid.uuid4().hex[:12]
        # Register ownership before load so cancellation can clean up a partial load.
        self.lm_instance = cli, identifier
        self._command(
            [
                cli,
                "load",
                settings.ocr_model,
                "--identifier",
                identifier,
                "--context-length",
                str(context),
                "--yes",
                "--no-engine-config-file",
            ],
            cancel,
            600,
            defer_cancel=True,
        )
        self.check_cancel(cancel)
        runtime = replace(
            settings,
            ocr_model=identifier,
            edit_model=identifier,
        )
        if identifier not in LocalModel(runtime).models():
            raise AppError(
                "Модель LM Studio не появилась на выбранном API. Проверьте порт сервера."
            )
        return runtime
