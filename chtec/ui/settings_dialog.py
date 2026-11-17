import copy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from chtec.backends.catalog import catalog
from chtec.domain import AppError
from chtec.providers import PROVIDERS, CloudSettings, create_model
from chtec.ui.translations import model_entry_label, tr
from chtec.ui.workers import Worker


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None, profile=None, runtime_settings=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Настройки подключения"))
        self.setMinimumSize(460, 400)
        self.resize(720, 680)
        self.worker = None
        self.profile = profile
        self.initial_settings = settings
        self.cloud = isinstance(settings, CloudSettings)
        self.entries = []
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        name = PROVIDERS[profile.name].title if profile else tr("Локальный API")
        note = QLabel(
            tr(
                "{0}: настройте один раз. После выбора модель подключается автоматически.",
                f"{name}",
            )
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.url = QLineEdit(settings.base_url)
        if self.cloud and settings.provider == "qwen":
            self.url.setPlaceholderText(
                tr("Можно указать HTTPS-адрес своего workspace из Alibaba Console")
            )
        if self.cloud:
            endpoints = PROVIDERS[settings.provider].endpoints
            if len(endpoints) > 1:
                self.region = QComboBox()
                self.region.addItems(endpoints)
                self.region.setCurrentText(settings.base_url)
                self.region.currentTextChanged.connect(self.change_endpoint)
                form.addRow(tr("Регион API"), self.region)
            self.url.setReadOnly(settings.provider != "qwen")
        self.key = QLineEdit(settings.api_key)
        if self.cloud:
            self.url.textEdited.connect(lambda _: self.key.clear())
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(
            tr("Сохраняется зашифрованным в Windows")
            if self.cloud
            else tr("Если нужен; только до закрытия программы")
        )
        self.ocr = QComboBox()
        self.ocr.setEditable(True)
        self.ocr.setCurrentText(settings.ocr_model)
        self.ocr.setMinimumContentsLength(24)
        self.edit = QComboBox()
        self.edit.setEditable(True)
        self.edit.setCurrentText(settings.edit_model)
        self.edit.setPlaceholderText(tr("Та же модель"))
        if profile and not self.cloud:
            self.edit.setEnabled(False)
            self.edit.setToolTip(
                tr("Локально используется одна модель для распознавания и правок.")
            )
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 1800)
        self.timeout.setValue(int(settings.timeout))
        self.timeout.setSuffix(tr(" с"))
        self.tokens = QSpinBox()
        self.tokens.setRange(128, 32768)
        self.tokens.setValue(settings.max_tokens)
        form.addRow(tr("Адрес API"), self.url)
        form.addRow(tr("API-ключ"), self.key)
        form.addRow(tr("Модель распознавания"), self.ocr)
        form.addRow(tr("Модель редактора"), self.edit)
        self.cli_path = QLineEdit()
        self.app_path = QLineEdit()
        self.model_path = QLineEdit(profile.model_path if profile else "")
        self.context = QSpinBox()
        self.context.setRange(512, 131072)
        self.context.setValue(profile.context_length if profile else 8192)
        if profile and not self.cloud:
            from chtec.backends.manager import discover_paths

            cli, app = discover_paths(profile.name)
            self.cli_path.setText(profile.cli_path or cli)
            self.app_path.setText(profile.app_path or app)
            form.addRow(tr("Официальный CLI"), self.cli_path)
            if profile.name == "lmstudio":
                form.addRow(tr("Приложение LM Studio"), self.app_path)
            else:
                row = QHBoxLayout()
                row.addWidget(self.model_path)
                browse = QPushButton(tr("Выбрать GGUF…"))
                browse.clicked.connect(self.browse_model)
                row.addWidget(browse)
                form.addRow(tr("Локальная модель"), row)
                self.ocr.currentIndexChanged.connect(self.select_file)
            form.addRow(tr("Контекст автозагрузки"), self.context)
        form.addRow(tr("Тайм-аут"), self.timeout)
        form.addRow(tr("Лимит ответа, токены"), self.tokens)
        layout.addLayout(form)
        self.check = QPushButton(tr("Обновить список моделей"))
        self.check.clicked.connect(self.fetch_models)
        layout.addWidget(self.check)
        self.status = QLabel(
            tr("Изображения и текст отправляются выбранному облачному поставщику.")
            if self.cloud
            else tr("Модели ищутся на этом компьютере.")
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            tr("Сохранить и подключить")
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Отмена"))
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_catalog_started", False):
            self._catalog_started = True
            if self.profile and (not self.cloud or self.key.text().strip()):
                QTimer.singleShot(0, self.fetch_models)

    def change_endpoint(self, endpoint):
        self.url.setText(endpoint)
        self.key.clear()
        self.entries = []
        self.ocr.clear()
        self.edit.clear()

    def select_file(self):
        entry = self.ocr.currentData()
        if entry and entry.path and self.ocr.currentText() == model_entry_label(entry):
            self.model_path.setText(entry.path)

    def value(self):
        entry = self.ocr.currentData()
        # Editable text must not accidentally reuse the previous row's identifier.
        identifier = (
            entry.id
            if entry and self.ocr.currentText() == model_entry_label(entry)
            else self.ocr.currentText().strip()
        )
        settings = replace(
            self.initial_settings,
            base_url=self.url.text(),
            ocr_model=identifier,
            edit_model=self.edit.currentText().strip(),
            api_key=self.key.text(),
            timeout=self.timeout.value(),
            max_tokens=self.tokens.value(),
        )
        if self.cloud:
            settings = replace(
                settings, vision=entry.vision if entry and identifier == entry.id else None
            )
        return settings.validated()

    def profile_value(self):
        settings = self.value()
        self.select_file()
        model_path = self.model_path.text().strip()
        if self.profile.name == "unsloth" and model_path:
            settings = replace(
                settings, ocr_model=Path(model_path).stem, edit_model=Path(model_path).stem
            )
        return replace(
            self.profile,
            settings=settings,
            cli_path=self.cli_path.text().strip(),
            app_path=self.app_path.text().strip(),
            model_path=model_path,
            context_length=self.context.value(),
        )

    def save(self):
        try:
            settings = self.value()
            if self.cloud and (not settings.api_key.strip() or not settings.ocr_model.strip()):
                raise AppError(
                    tr("Введите API-ключ и выберите модель из каталога или укажите её имя.")
                )
        except AppError as exc:
            QMessageBox.warning(self, tr("Проверьте настройки"), str(exc))
            return
        self.accept()

    def browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Локальная модель Unsloth"), self.model_path.text(), "GGUF (*.gguf)"
        )
        if path:
            self.model_path.setText(path)
            self.ocr.setEditText(Path(path).stem)

    def fetch_models(self):
        if self.worker:
            return
        try:
            settings = replace(self.value(), timeout=20)
            profile = copy.deepcopy(self.profile)
            if profile:
                profile.settings = settings
                profile.cli_path = self.cli_path.text().strip()
                profile.model_path = self.model_path.text().strip()
        except AppError as exc:
            self.status.setText(str(exc))
            return
        self.check.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.status.setText(tr("Получение каталога моделей…"))
        self.worker = Worker(
            lambda worker: (
                catalog(profile, cancel=worker.cancel)
                if profile
                else create_model(settings).models()
            ),
            self,
        )
        self.worker.result.connect(self.show_catalog)
        self.worker.error.connect(self.status.setText)
        self.worker.finished.connect(self.fetch_finished)
        self.worker.start()

    def show_catalog(self, entries):
        from chtec.backends.catalog import ModelEntry

        self.entries = [ModelEntry(e, e) if isinstance(e, str) else e for e in entries]
        entry = self.ocr.currentData()
        previous = (
            entry.id
            if entry and self.ocr.currentText() == model_entry_label(entry)
            else self.ocr.currentText().strip()
        )
        previous_edit = self.edit.currentText()
        if self.profile and self.profile.name == "unsloth":
            previous = self.model_path.text()
        self.ocr.blockSignals(True)
        self.ocr.clear()
        for entry in self.entries:
            self.ocr.addItem(model_entry_label(entry), entry)
        selected = next((i for i, e in enumerate(self.entries) if e.id == previous), -1)
        if selected >= 0:
            self.ocr.setCurrentIndex(selected)
        elif previous:
            self.ocr.setEditText(previous)
        else:
            self.ocr.setCurrentIndex(-1)
        self.ocr.blockSignals(False)
        self.edit.clear()
        self.edit.addItem("")
        self.edit.addItems([e.id for e in self.entries])
        self.edit.setCurrentText(previous_edit)
        self.status.setText(
            tr("Найдено моделей: {0}. Можно указать идентификатор вручную.", f"{len(self.entries)}")
        )

    def show_models(self, models):
        self.show_catalog(models)

    def fetch_finished(self):
        self.check.setEnabled(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)
        self.worker.deleteLater()
        self.worker = None
        if getattr(self, "_reject_pending", False):
            super().reject()

    def reject(self):
        if self.worker:
            self._reject_pending = True
            self.worker.cancel.set()
            self.status.setText(tr("Отмена после текущего запроса каталога…"))
        else:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
