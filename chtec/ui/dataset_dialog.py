"""Review local training pairs; persistence and background work belong to the controller."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from chtec.ui.translations import tr

STATUS_LABELS = {
    "draft": "Черновик",
    "approved": "Подтверждён",
    "excluded": "Исключён",
    "stale": "Требует повторной проверки",
}


class DatasetManagerDialog(QDialog):
    action_requested = Signal(str, object)

    def __init__(self, samples, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Датасет для обучения"))
        self.setMinimumSize(650, 450)
        self.resize(1100, 760)
        self._samples = []
        self.current_sample = None
        self._busy = False
        self._loading = False
        self._valid_image = False
        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.status_filter = QComboBox()
        self.status_filter.addItem(tr("Все статусы"), "")
        for value, label in STATUS_LABELS.items():
            self.status_filter.addItem(tr(label), value)
        controls.addWidget(self.status_filter)
        self.refresh_button = QPushButton(tr("Обновить"))
        controls.addWidget(self.refresh_button)
        controls.addStretch()
        self.counters = QLabel()
        self.counters.setWordWrap(True)
        outer.addLayout(controls)
        outer.addWidget(self.counters)
        split = QSplitter()
        self.samples_list = QListWidget()
        split.addWidget(self.samples_list)
        review = QWidget()
        review_layout = QVBoxLayout(review)
        self.sample_info = QLabel()
        self.sample_info.setWordWrap(True)
        review_layout.addWidget(self.sample_info)
        previews = QSplitter()
        image_scroll = QScrollArea()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_scroll.setWidget(self.image)
        image_scroll.setWidgetResizable(True)
        previews.addWidget(image_scroll)
        self.text = QPlainTextEdit()
        self.text.setAccessibleName(tr("Точная транскрипция"))
        previews.addWidget(self.text)
        previews.setSizes([400, 400])
        review_layout.addWidget(previews, 1)
        fields = QFormLayout()
        self.group = QLineEdit()
        fields.addRow(tr("Группа документа"), self.group)
        self.uncertain = QCheckBox(tr("Есть неразборчивый или сомнительный текст"))
        fields.addRow(self.uncertain)
        review_layout.addLayout(fields)
        self.review_note = QLabel(
            tr(
                "Проверьте изображение и точный текст. После правки сначала сохраните пример, "
                "затем подтвердите его отдельным нажатием."
            )
        )
        self.review_note.setWordWrap(True)
        review_layout.addWidget(self.review_note)
        actions = QHBoxLayout()
        self.save_button = QPushButton(tr("Сохранить правки"))
        self.approve_button = QPushButton(tr("Подтвердить текст для обучения"))
        self.exclude_button = QPushButton(tr("Исключить пример"))
        for button in (self.save_button, self.approve_button, self.exclude_button):
            actions.addWidget(button)
        review_layout.addLayout(actions)
        split.addWidget(review)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 870])
        outer.addWidget(split, 1)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.error_label)
        footer = QHBoxLayout()
        self.split_checkbox = QCheckBox(tr("Разбить по группам на train / validation / test"))
        footer.addWidget(self.split_checkbox)
        self.export_button = QPushButton(tr("Экспортировать"))
        footer.addWidget(self.export_button)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.close)
        footer.addWidget(close)
        outer.addLayout(footer)
        self.status_filter.currentIndexChanged.connect(self._populate_list)
        self.samples_list.currentItemChanged.connect(self._selection_changed)
        self.text.textChanged.connect(self._update_actions)
        self.group.textChanged.connect(self._update_actions)
        self.uncertain.toggled.connect(self._update_actions)
        self.save_button.clicked.connect(self._save)
        self.approve_button.clicked.connect(self._approve)
        self.exclude_button.clicked.connect(self._exclude)
        self.refresh_button.clicked.connect(lambda: self.action_requested.emit("refresh", None))
        self.export_button.clicked.connect(self._export)
        self.set_samples(samples)

    def set_samples(self, samples):
        self._samples = [dict(sample) for sample in samples]
        self._populate_list()

    def _populate_list(self):
        selected = self.current_sample.get("id") if self.current_sample else None
        previous = self.current_sample or {}
        logical_key = (previous.get("page_hash"), previous.get("block_id"))
        status = self.status_filter.currentData()
        self.samples_list.blockSignals(True)
        self.samples_list.clear()
        selected_item = None
        logical_item = None
        for sample in self._samples:
            if status and sample.get("status") != status:
                continue
            label = STATUS_LABELS.get(sample.get("status"), str(sample.get("status", "")))
            item = QListWidgetItem(tr("{0} · {1}", sample["id"], tr(label)))
            item.setData(Qt.ItemDataRole.UserRole, sample)
            self.samples_list.addItem(item)
            if sample["id"] == selected:
                selected_item = item
            if (
                all(logical_key)
                and (sample.get("page_hash"), sample.get("block_id")) == logical_key
            ):
                logical_item = item
        self.samples_list.blockSignals(False)
        if selected_item is None:
            selected_item = logical_item
        if selected_item is not None:
            self.samples_list.setCurrentItem(selected_item)
        elif self.samples_list.count():
            self.samples_list.setCurrentRow(0)
        else:
            self._selection_changed(None)
        counts = [
            tr("{0}: {1}", tr(label), sum(s.get("status") == key for s in self._samples))
            for key, label in STATUS_LABELS.items()
        ]
        self.counters.setText(" · ".join(counts))

    def select_sample(self, sample_id):
        for row in range(self.samples_list.count()):
            item = self.samples_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole)["id"] == sample_id:
                self.samples_list.setCurrentItem(item)
                return
        if self.status_filter.currentData():
            self.status_filter.setCurrentIndex(0)
            self.select_sample(sample_id)

    def _selection_changed(self, item, previous=None):
        self._loading = True
        self.current_sample = item.data(Qt.ItemDataRole.UserRole) if item else None
        sample = self.current_sample or {}
        self.text.setPlainText(sample.get("text", ""))
        self.group.setText(sample.get("group") or "")
        self.uncertain.setChecked(bool(sample.get("uncertain", False)))
        self.sample_info.setText(
            tr("Пример: {0}", sample.get("id", "—"))
            + "\n"
            + tr("Источник изображения: {0}", tr(sample.get("source_label", "—")))
        )
        self.image.clear()
        pixmap = QPixmap(str(sample.get("image_path") or ""))
        self._valid_image = not pixmap.isNull()
        if self._valid_image:
            self.image.setPixmap(pixmap)
            self.image.setMinimumSize(pixmap.size())
        else:
            self.image.setMinimumSize(0, 0)
            self.image.setText(tr("Изображение недоступно"))
        self._loading = False
        self._update_actions()

    def _dirty(self):
        sample = self.current_sample or {}
        return (
            self.text.toPlainText() != sample.get("text", "")
            or self.group.text() != (sample.get("group") or "")
            or self.uncertain.isChecked() != bool(sample.get("uncertain", False))
        )

    def _update_actions(self):
        if self._loading:
            return
        editable = self.current_sample is not None and not self._busy
        self.text.setReadOnly(not editable)
        self.group.setEnabled(editable)
        self.uncertain.setEnabled(editable)
        self.save_button.setEnabled(editable and self._dirty())
        self.approve_button.setEnabled(
            editable
            and not self._dirty()
            and self._valid_image
            and bool(self.text.toPlainText().strip())
            and not self.uncertain.isChecked()
            and not self.current_sample.get("deleted", False)
            and not self.current_sample.get("source_invalid", False)
        )
        self.exclude_button.setEnabled(editable)
        self.refresh_button.setEnabled(not self._busy)
        self.export_button.setEnabled(not self._busy)
        self.samples_list.setEnabled(not self._busy)
        self.status_filter.setEnabled(not self._busy)

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_actions()

    def show_error(self, message):
        self.error_label.setText(str(message))

    def _save(self):
        if not self.save_button.isEnabled():
            return
        sample = self.current_sample
        self.action_requested.emit(
            "save",
            {
                "id": sample["id"],
                "fingerprint": sample["fingerprint"],
                "text": self.text.toPlainText(),
                "group": self.group.text(),
                "uncertain": self.uncertain.isChecked(),
            },
        )

    def _approve(self):
        if self.approve_button.isEnabled():
            sample = self.current_sample
            self.action_requested.emit(
                "approve",
                {
                    "id": sample["id"],
                    "fingerprint": sample["fingerprint"],
                },
            )

    def _exclude(self):
        if self.exclude_button.isEnabled():
            self.action_requested.emit("exclude", {"id": self.current_sample["id"]})

    def _export(self):
        target, _ = QFileDialog.getSaveFileName(self, tr("Новая папка экспорта"))
        if not target:
            return
        if Path(target).exists():
            self.show_error(tr("Выберите новую папку: путь уже существует."))
            return
        self.action_requested.emit(
            "export",
            {
                "target": target,
                "split": self.split_checkbox.isChecked(),
            },
        )
