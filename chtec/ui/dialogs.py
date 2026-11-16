from PySide6.QtCore import Qt
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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chtec.domain import AppError
from chtec.llm import LocalModel, LocalSettings
from chtec.ui.workers import Worker


class SettingsDialog(QDialog):
    def __init__(self, settings: LocalSettings, parent=None, profile=None, runtime_settings=None):
        super().__init__(parent)
        self.setWindowTitle("Локальная модель")
        self.setMinimumWidth(560)
        self.worker = None
        self.profile = profile
        self.runtime_settings = runtime_settings
        layout = QVBoxLayout(self)
        note = QLabel(
            "Настройки относятся только к выбранной системе.\n"
            "Для автозапуска сохраните настройки и нажмите «Активировать».\n"
            "Unsloth: локальный GGUF и mmproj рядом. LM Studio: ключ установленной модели."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.url = QLineEdit(settings.base_url)
        self.key = QLineEdit(settings.api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Если нужен; хранится только до закрытия приложения")
        self.ocr = QComboBox()
        self.ocr.setEditable(True)
        self.ocr.setCurrentText(settings.ocr_model)
        self.edit = QComboBox()
        self.edit.setEditable(True)
        self.edit.setCurrentText(settings.edit_model)
        self.edit.setPlaceholderText("Как для распознавания")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 1800)
        self.timeout.setValue(int(settings.timeout))
        self.timeout.setSuffix(" с")
        self.tokens = QSpinBox()
        self.tokens.setRange(128, 32768)
        self.tokens.setValue(settings.max_tokens)
        form.addRow("Адрес API", self.url)
        form.addRow("API-ключ", self.key)
        form.addRow("Модель распознавания", self.ocr)
        form.addRow("Модель редактора", self.edit)
        if profile is not None:
            self.edit.setEnabled(False)
            self.edit.setToolTip(
                "При автозапуске правки выполняет та же модель, что читает изображения."
            )
            if profile.name == "unsloth":
                self.ocr.setEnabled(False)
                self.ocr.setToolTip("Идентификатор определяется после загрузки выбранного GGUF.")
        form.addRow("Тайм-аут", self.timeout)
        form.addRow("Лимит ответа, токены", self.tokens)
        if profile is not None:
            from chtec.backends.manager import discover_paths

            cli, app = discover_paths(profile.name)
            self.cli_path = QLineEdit(profile.cli_path or cli)
            self.app_path = QLineEdit(profile.app_path or app)
            self.model_path = QLineEdit(profile.model_path)
            self.context = QSpinBox()
            self.context.setRange(512, 131072)
            self.context.setValue(profile.context_length)
            form.addRow("Официальный CLI", self.cli_path)
            if profile.name == "lmstudio":
                form.addRow("Приложение LM Studio", self.app_path)
            else:
                row = QHBoxLayout()
                row.addWidget(self.model_path)
                browse = QPushButton("Выбрать GGUF…")
                browse.clicked.connect(self.browse_model)
                row.addWidget(browse)
                form.addRow("Локальная модель", row)
            form.addRow("Контекст автозагрузки", self.context)
        layout.addLayout(form)
        self.check = QPushButton("Проверить и получить модели")
        self.check.clicked.connect(self.fetch_models)
        layout.addWidget(self.check)
        self.status = QLabel("Данные отправляются только на loopback-адрес этого компьютера.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Применить")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def value(self) -> LocalSettings:
        return LocalSettings(
            self.url.text(),
            self.ocr.currentText().strip(),
            self.edit.currentText().strip(),
            self.key.text(),
            self.timeout.value(),
            self.tokens.value(),
        ).validated()

    def save(self):
        try:
            self.value()
        except AppError as exc:
            QMessageBox.warning(self, "Проверьте настройки", str(exc))
            return
        self.accept()

    def browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Локальная модель Unsloth", self.model_path.text(), "GGUF (*.gguf)"
        )
        if path:
            self.model_path.setText(path)

    def profile_value(self):
        from dataclasses import replace
        from pathlib import Path

        settings = self.value()
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

    def fetch_models(self):
        try:
            from dataclasses import replace

            settings = self.value()
            if self.runtime_settings and settings.base_url == self.runtime_settings.base_url:
                settings = replace(settings, api_key=self.runtime_settings.api_key)
            model = LocalModel(settings)
        except AppError as exc:
            self.status.setText(str(exc))
            return
        self.check.setEnabled(False)
        self.buttons.setEnabled(False)
        self.status.setText("Подключение к локальному серверу…")

        def list_models(_):
            if self.profile and self.profile.name == "unsloth":
                from chtec.backends.manager import loaded_unsloth_model

                return [loaded_unsloth_model(model)]
            return model.models()

        self.worker = Worker(list_models, self)
        self.worker.result.connect(self.show_models)
        self.worker.error.connect(self.status.setText)
        self.worker.finished.connect(self.fetch_finished)
        self.worker.start()

    def show_models(self, models):
        for combo in (self.ocr, self.edit):
            previous = combo.currentText()
            combo.clear()
            combo.addItems(models)
            if previous:
                combo.setCurrentText(previous)
        self.status.setText(
            f"Сервер доступен. Моделей: {len(models)}. "
            "Поддержку изображений проверьте в описании выбранной модели."
        )

    def fetch_finished(self):
        self.check.setEnabled(True)
        self.buttons.setEnabled(True)
        self.worker.deleteLater()
        self.worker = None

    def reject(self):
        if self.worker is None:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)


class ProposalDialog(QDialog):
    def __init__(self, original: str, proposed: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка правки модели")
        self.resize(960, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Сверьте изменения. Предложение можно отредактировать перед применением.")
        )
        splitter = QSplitter(Qt.Orientation.Horizontal)
        for title, text, readonly in (
            ("Исходный текст", original, True),
            ("Предложение модели", proposed, False),
        ):
            from PySide6.QtWidgets import QWidget

            pane = QWidget()
            column = QVBoxLayout(pane)
            column.addWidget(QLabel(title))
            editor = QPlainTextEdit()
            editor.setPlainText(text)
            editor.setReadOnly(readonly)
            column.addWidget(editor)
            splitter.addWidget(pane)
            if not readonly:
                self.proposed = editor
        layout.addWidget(splitter)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("Применить правку")
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Оставить исходный")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def proposed_text(self) -> str:
        return self.proposed.toPlainText()


class ExportPreviewDialog(QDialog):
    def __init__(self, document, registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Предпросмотр перед сохранением")
        self.resize(800, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Проверьте весь текст. Разделители ниже показывают исходные страницы.\n"
                "PDF/HTML сохраняют расположение размеченных блоков; TXT/DOCX — линейный текст.\n"
                "Текст без привязки сохраняется отдельно. JSON сохраняет весь рабочий проект."
            )
        )
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "\n\n".join(
                f"── Страница {i + 1} · {page.source} ──\n\n{page.text}"
                for i, page in enumerate(document.pages)
            )
        )
        if any(page.blocks for page in document.pages):
            from chtec.ui.layout import LayoutCanvas

            tabs = QTabWidget()
            spatial = QWidget()
            column = QVBoxLayout(spatial)
            pages = QComboBox()
            pages.addItems([f"Страница {i + 1}" for i in range(len(document.pages))])
            canvas = LayoutCanvas(True)
            pages.currentIndexChanged.connect(lambda i: canvas.set_page(document.pages[i]))
            canvas.set_page(document.pages[0])
            column.addWidget(pages)
            column.addWidget(canvas)
            tabs.addTab(spatial, "Расположение")
            tabs.addTab(text, "Весь текст")
            layout.addWidget(tabs)
        else:
            layout.addWidget(text)
        row = QHBoxLayout()
        row.addWidget(QLabel("Сохранить как"))
        self.format = QComboBox()
        for extension, handler in registry.exporters.items():
            self.format.addItem(f"{extension.upper()[1:]} — {handler.label}", extension)
        row.addWidget(self.format, 1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Выбрать файл и сохранить…")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Вернуться к редактированию")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
