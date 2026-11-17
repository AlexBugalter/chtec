"""Local training collection jobs, isolated from document editing and AI credentials."""

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMenu, QMessageBox

from chtec.domain import Page
from chtec.domain_errors import AppError
from chtec.ui.translations import tr
from chtec.ui.workers import Worker


class DatasetPreferences:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.folder, self.enabled, self.group = "", False, ""
        self.warning = ""
        if self.path and self.path.exists():
            try:
                if self.path.stat().st_size > 65536:
                    raise ValueError
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if (
                    not isinstance(data, dict)
                    or type(data.get("enabled")) is not bool
                    or not isinstance(data.get("folder"), str)
                    or not isinstance(data.get("group"), str)
                ):
                    raise ValueError
                self.folder, self.enabled, self.group = (
                    data["folder"],
                    data["enabled"],
                    data["group"],
                )
                if not self.folder:
                    self.enabled = False
            except (OSError, ValueError, TypeError):
                self.warning = tr("Настройки датасета повреждены. Сбор выключен.")

    def save(self, folder, enabled, group):
        if self.path:
            temporary = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent, delete=False
                ) as f:
                    temporary = Path(f.name)
                    json.dump(
                        dict(folder=folder, enabled=bool(enabled), group=group),
                        f,
                        ensure_ascii=False,
                    )
                os.replace(temporary, self.path)
            except OSError as exc:
                raise AppError(tr("Не удалось сохранить настройки датасета.")) from exc
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        self.folder, self.enabled, self.group = folder, bool(enabled), group


class DatasetController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        path = (
            window.settings_store.path.with_name("dataset-settings.json")
            if window.settings_store
            else None
        )
        self.preferences = DatasetPreferences(path)
        self.worker = None
        self.queue = []
        self.dialog = None
        self.closing = False
        self.last_signature = None
        self.observed = {}
        self.pending = {}
        self.failed_sync = []
        self.active_job = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(650)
        self.timer.timeout.connect(self.flush)
        self.menu = QMenu(tr("Датасет"), window)
        window.menuBar().addMenu(self.menu)
        self.menu.addAction(tr("Открыть коллекцию…"), self.open_collection)
        self.collect_action = self.menu.addAction(tr("Собирать черновики после правок"))
        self.collect_action.setCheckable(True)
        self.collect_action.setChecked(self.enabled)
        self.collect_action.toggled.connect(self.toggle)
        self.menu.addAction(tr("Группа документа…"), self.set_group)
        self.menu.addSeparator()
        self.menu.addAction(tr("Просмотреть примеры…"), self.show_manager)
        self.menu.addAction(tr("Подтвердить выбранную зону для обучения…"), self.collect_selected)
        window.layout_pane.training_requested.connect(self.collect_selected)
        if self.preferences.warning:
            window.statusBar().showMessage(self.preferences.warning)

    @property
    def enabled(self):
        return self.preferences.enabled

    def service(self, folder=None):
        from chtec.dataset import DatasetService

        return DatasetService(folder or self.preferences.folder)

    def error(self, message):
        if self.dialog:
            self.dialog.show_error(message)
        self.window.statusBar().showMessage(tr("Датасет: {0}", message))

    def persist(self, folder, enabled, group):
        try:
            self.preferences.save(folder, enabled, group)
            self.last_signature = None
            self.observed.clear()
            return True
        except AppError as exc:
            self.error(str(exc))
            return False

    def open_collection(self):
        self.flush()
        if self.worker or self.queue:
            self.error(tr("Дождитесь завершения операции датасета."))
            return False
        folder = QFileDialog.getExistingDirectory(
            self.window, tr("Папка локальной коллекции"), self.preferences.folder
        )
        if not folder:
            return False
        if self.persist(folder, self.enabled, self.preferences.group):
            self.notify()
            self.show_manager()
            return True
        return False

    def ensure_collection(self):
        return bool(self.preferences.folder) or self.open_collection()

    def toggle(self, enabled):
        if enabled and not self.ensure_collection():
            self.collect_action.blockSignals(True)
            self.collect_action.setChecked(False)
            self.collect_action.blockSignals(False)
            return
        if not self.persist(self.preferences.folder, enabled, self.preferences.group):
            self.collect_action.blockSignals(True)
            self.collect_action.setChecked(self.enabled)
            self.collect_action.blockSignals(False)
        self.notify()

    def set_group(self):
        value, ok = QInputDialog.getText(
            self.window,
            tr("Группа документа"),
            tr("Название книги или дела для общего разбиения датасета"),
            text=self.preferences.group,
        )
        if ok:
            self.persist(self.preferences.folder, self.enabled, value.strip())

    @staticmethod
    def snapshot(page):
        return Page(
            page.image_png,
            page.source,
            page.number,
            page.text,
            source_path=page.source_path,
            blocks=list(page.blocks),
            unplaced_text=page.unplaced_text,
        )

    @staticmethod
    def signature(page):
        return (
            id(page.image_png),
            page.source_path,
            page.number,
            tuple((b.id, b.text, b.rect, b.angle, b.quad) for b in page.blocks),
        )

    def record_current(self):
        page = self.window.current_page
        if self.closing or not self.preferences.folder or page is None:
            return False
        live = {id(p) for p in self.window.document.pages}
        self.observed = {k: v for k, v in self.observed.items() if k in live}
        key, signature = id(page), self.signature(page)
        previous = self.observed.get(key)
        if previous and previous[0] == signature:
            return False
        if previous and previous[1].image_png != page.image_png:
            old = self.snapshot(previous[1])
            old.blocks = []
            self.pending[(key, previous[0][0])] = (
                old,
                self.preferences.group,
                False,
                self.preferences.folder,
            )
        snapshot = self.snapshot(page)
        self.observed[key] = (signature, snapshot)
        self.pending[key] = (
            snapshot,
            self.preferences.group,
            self.enabled,
            self.preferences.folder,
        )
        return True

    def notify(self):
        if self.record_current():
            self.timer.start()

    def flush(self):
        if self.closing or not self.preferences.folder:
            return
        self.record_current()
        pending, self.pending = self.pending, {}
        for key, (snapshot, group, enabled, folder) in pending.items():

            def operation(snapshot=snapshot, group=group, enabled=enabled, folder=folder):
                service = self.service(folder)
                service.sync_page(snapshot, group=group, capture_new=enabled)
                return service.list_samples()

            kind = "sync:" + str(key)
            self.queue = [job for job in self.queue if job[0] != kind]
            self.enqueue(kind, operation, self.updated)

    def enqueue(self, kind, operation, callback):
        if self.closing:
            return
        self.queue.append((kind, operation, callback))
        self.start_next()

    def start_next(self):
        if self.worker or not self.queue:
            return
        self.active_job = self.queue.pop(0)
        _, operation, callback = self.active_job
        worker = Worker(lambda _: operation(), self)
        self.worker = worker
        if self.dialog:
            self.dialog.set_busy(True)
        worker.result.connect(lambda result: callback(result) if not self.closing else None)
        worker.error.connect(self.failed)
        worker.finished.connect(self.finished)
        worker.start()

    def failed(self, message):
        self.last_signature = None
        self.observed.clear()
        if self.active_job and self.active_job[0].startswith("sync:"):
            self.failed_sync.append(self.active_job)
        self.queue = [job for job in self.queue if job[0].startswith("sync:")]
        if self.closing:
            self.closing = False
            self.window._dataset_close_approved = False
            self.queue.clear()
        self.error(message)

    def finished(self):
        previous, self.worker = self.worker, None
        if previous:
            previous.deleteLater()
        if self.dialog:
            self.dialog.set_busy(False)
        if self.queue:
            self.start_next()
        elif self.closing:
            QTimer.singleShot(0, self.window.close)

    def updated(self, samples):
        if self.dialog:
            self.dialog.set_samples(samples)

    def show_manager(self):
        if not self.ensure_collection():
            return
        from chtec.ui.dataset_dialog import DatasetManagerDialog

        if self.dialog is None:
            self.dialog = DatasetManagerDialog([], self.window)
            self.dialog.action_requested.connect(self.action)
        self.dialog.show()
        self.dialog.raise_()
        folder = self.preferences.folder
        self.enqueue("refresh", lambda: self.service(folder).list_samples(), self.updated)

    def collect_selected(self, block_id=None):
        if self.window.worker or self.closing:
            return
        page = self.window.current_page
        ids = [block_id] if isinstance(block_id, str) else self.window.layout_pane.selected_ids
        if page is None or len(ids) != 1:
            self.error(tr("Выберите одну зону с текстом."))
            return
        block = next((b for b in page.blocks if b.id == ids[0]), None)
        if not block or not block.text.strip():
            self.error(tr("Пустая зона не подходит для обучения."))
            return
        if not self.ensure_collection():
            return
        snapshot, folder, group = (
            self.snapshot(page),
            self.preferences.folder,
            self.preferences.group,
        )
        self.show_manager()

        def captured(result):
            sample, samples = result
            self.updated(samples)
            self.dialog.select_sample(sample["id"])

        def capture():
            service = self.service(folder)
            sample = service.capture(snapshot, block, group=group)
            return sample, service.list_samples()

        self.enqueue("capture", capture, captured)

    def action(self, kind, payload):
        if self.closing:
            return
        self.timer.stop()
        self.queue[0:0] = self.failed_sync
        self.failed_sync = []
        self.flush()
        pages = [self.snapshot(p) for p in self.window.document.pages]
        folder, group, enabled = self.preferences.folder, self.preferences.group, self.enabled

        def operation():
            service = self.service(folder)
            # Reconcile a fresh document snapshot before any approval or export.
            for page in pages:
                service.sync_page(page, group=group, capture_new=enabled)
            if kind == "save":
                service.revise(
                    payload["id"],
                    payload["fingerprint"],
                    payload["text"],
                    group=payload.get("group", ""),
                    uncertain=payload.get("uncertain", False),
                )
            elif kind == "approve":
                service.approve(payload["id"], payload["fingerprint"])
            elif kind == "exclude":
                service.exclude(payload["id"])
            elif kind == "export":
                report = service.export(Path(payload["target"]), split=payload["split"])
                return ("export", report)
            return service.list_samples()

        def completed(result):
            if isinstance(result, tuple):
                report = result[1]
                warnings = report.get("warnings", [])
                message = tr("Датасет сохранён: {0}", payload["target"])
                message += "\n" + tr("Примеров: {0}", report.get("exported", 0))
                message += "".join("\n" + tr(warning) for warning in warnings)
                (QMessageBox.warning if warnings else QMessageBox.information)(
                    self.window,
                    tr("Экспорт датасета"),
                    message,
                )
            else:
                self.updated(result)

        self.enqueue(kind, operation, completed)

    def request_close(self):
        if not self.closing:
            self.timer.stop()
            self.queue[0:0] = self.failed_sync
            self.failed_sync = []
            self.flush()
            self.closing = True
        if self.worker or self.queue:
            self.start_next()
            return False
        return True
