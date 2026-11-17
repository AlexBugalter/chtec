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
from chtec.ui.translations import tr


class ProposalDialog(QDialog):
    def __init__(self, original: str, proposed: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Проверка правки модели"))
        self.resize(960, 640)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(tr("Сверьте изменения. Предложение можно отредактировать перед применением."))
        )
        splitter = QSplitter(Qt.Orientation.Horizontal)
        for title, text, readonly in (
            (tr("Исходный текст"), original, True),
            (tr("Предложение модели"), proposed, False),
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
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText(tr("Применить правку"))
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Оставить исходный"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def proposed_text(self) -> str:
        return self.proposed.toPlainText()


class ExportPreviewDialog(QDialog):
    def __init__(self, document, registry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Предпросмотр перед сохранением"))
        self.resize(800, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                tr(
                    (
                        "Проверьте весь текст. Разделители ниже показывают исходные "
                        "страницы.\nPDF/HTML сохраняют расположение размеченных блоков; "
                        "TXT/DOCX — линейный текст.\nТекст без привязки сохраняется "
                        "отдельно. JSON сохраняет весь рабочий проект."
                    )
                )
            )
        )
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(
            "\n\n".join(
                tr("── Страница {0} · {1} ──\n\n{2}", f"{i + 1}", f"{page.source}", f"{page.text}")
                for i, page in enumerate(document.pages)
            )
        )
        if any(page.blocks for page in document.pages):
            from chtec.ui.layout import LayoutCanvas

            tabs = QTabWidget()
            spatial = QWidget()
            column = QVBoxLayout(spatial)
            pages = QComboBox()
            pages.addItems([tr("Страница {0}", f"{i + 1}") for i in range(len(document.pages))])
            canvas = LayoutCanvas(True)
            pages.currentIndexChanged.connect(lambda i: canvas.set_page(document.pages[i]))
            canvas.set_page(document.pages[0])
            column.addWidget(pages)
            column.addWidget(canvas)
            tabs.addTab(spatial, tr("Расположение"))
            tabs.addTab(text, tr("Весь текст"))
            layout.addWidget(tabs)
        else:
            layout.addWidget(text)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Сохранить как")))
        self.format = QComboBox()
        for extension, handler in registry.exporters.items():
            self.format.addItem(f"{extension.upper()[1:]} — {handler.label}", extension)
        row.addWidget(self.format, 1)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(
            tr("Выбрать файл и сохранить…")
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            tr("Вернуться к редактированию")
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
