from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chtec.ui.settings_dialog import SettingsDialog  # noqa: F401


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
