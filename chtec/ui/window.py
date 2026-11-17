import copy
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
)

from chtec import __version__
from chtec.backends.catalog import ModelEntry, catalog
from chtec.domain import MAX_PAGES, AppError, Document, validate_image_budget
from chtec.layout_recognition import LayoutRecognitionService
from chtec.llm import LocalModel, LocalSettings
from chtec.providers import PROVIDERS, CloudSettings, create_model, destination
from chtec.regions import ImageRegion, RegionRecognitionService
from chtec.services import DocumentService
from chtec.ui.dialogs import ExportPreviewDialog, ProposalDialog, SettingsDialog
from chtec.ui.layout import LayoutProposalDialog
from chtec.ui.translations import model_entry_label, tr, translate_message
from chtec.ui.workers import Worker
from chtec.ui.workspace import WorkspaceControls, build_workspace
from chtec.zone_detection import TextZoneDetector


class MainWindow(WorkspaceControls, QMainWindow):
    def __init__(
        self,
        registry,
        settings: LocalSettings,
        settings_store=None,
        profiles=None,
        backend_manager=None,
    ):
        super().__init__()
        self.registry = registry
        self.settings = settings
        self.settings_store = settings_store
        self.profiles = profiles
        self.backend_manager = backend_manager
        self.runtime_settings = None
        self.model_catalogs = {}
        self.binding_area = None
        self.document = Document()
        self.dirty = False
        self.worker = None
        self.close_after_task = False
        self.setWindowTitle(tr("Чтец {0}[*] — оцифровка рукописей", f"{__version__}"))
        available = self.screen().availableGeometry()
        self.resize(min(1380, available.width() - 32), min(880, available.height() - 64))
        self.setMinimumSize(800, 560)
        self.init_presentation()
        self.setAcceptDrops(True)
        self.build_ui()
        self.task_elapsed = QElapsedTimer()
        self.task_timer = QTimer(self)
        self.task_timer.timeout.connect(self.update_task_time)
        self.task_timer.setInterval(1000)
        self.task_time = QLabel()
        self.statusBar().addPermanentWidget(self.task_time)
        self.statusBar().showMessage(tr("Добавьте снимки, сканы PDF или сохраненный JSON-проект."))
        self.refresh_controls()

    def button(self, text, slot, primary=False):
        button = QPushButton(text)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(slot)
        return button

    def build_ui(self):
        build_workspace(self)

    @property
    def current_page(self):
        index = self.page_list.currentRow()
        return self.document.pages[index] if 0 <= index < len(self.document.pages) else None

    def refresh_controls(self):
        busy = self.worker is not None
        self.layout_button.setEnabled(not busy and self.current_page is not None)
        self.detect_zones_button.setEnabled(not busy and self.current_page is not None)
        self.bind_button.setEnabled(not busy and self.current_page is not None)
        self.layout_pane.set_busy(busy)
        if self.profiles is not None:
            self.backend_selector.setEnabled(not busy)
            self.activate_button.setEnabled(not busy)
            self.model_selector.setEnabled(not busy)
            self.refresh_models_button.setEnabled(not busy)
        has_page = self.current_page is not None
        for button in (self.open_button, self.settings_button, self.new_button):
            button.setEnabled(not busy)
        for button in (self.ocr_current, self.ocr_all, self.export_button, self.undo_button):
            button.setEnabled(has_page and not busy)
        self.editor.setReadOnly(busy or not has_page or bool(self.current_page.blocks))
        self.editor.spatial_edit = has_page and bool(self.current_page.blocks) and not busy
        if any(p.blocks for p in self.document.pages):
            self.ocr_all.setEnabled(False)
        self.edit_button.setEnabled(
            has_page and bool(self.editor.toPlainText().strip()) and not busy
        )
        self.edit_request.setEnabled(has_page and not busy)
        self.delete_button.setEnabled(has_page and not busy)
        self.delete_action.setEnabled(has_page and not busy)
        self.region_select_button.setEnabled(has_page and not busy)
        self.region_read_button.setEnabled(
            has_page
            and self.image.selected_region is not None
            and not self.image.selection_mode
            and not busy
        )
        self.review_button.setEnabled(
            has_page and bool(self.editor.toPlainText().strip()) and not busy
        )
        if busy and self.region_select_button.isChecked():
            self.region_select_button.setChecked(False)
            self.image.set_selection_mode(False)
        self.model_label.setText(
            "● "
            + tr(destination(self.settings))
            + " · "
            + ((self.runtime_settings or self.settings).ocr_model or tr("модель не выбрана"))
        )
        self.model_label.setToolTip(self.settings.base_url)
        self.pages_label.setText(tr("СТРАНИЦЫ · {0}", f"{len(self.document.pages)}"))
        self.count_label.setText(
            tr("{0} знаков", f"{len(self.editor.toPlainText()):,}").replace(",", " ")
        )
        self.setWindowModified(self.dirty)
        self.sync_workspace()

    def add_pages(self, pages):
        first = len(self.document.pages)
        self.document.append(pages)
        for page in pages:
            pixmap = QPixmap()
            pixmap.loadFromData(page.image_png, "PNG")
            item = QListWidgetItem(QIcon(pixmap), f"{self.page_list.count() + 1:02}\n{page.source}")
            item.setToolTip(tr("{0} · исходная страница {1}", f"{page.source}", f"{page.number}"))
            self.page_list.addItem(item)
        self.page_list.setCurrentRow(first)
        self.dirty = True
        self.refresh_controls()

    def select_page(self, index):
        self.binding_area = None
        self.editor.blockSignals(True)
        if self.current_page:
            self.editor.setPlainText(self.current_page.text)
            self.image.set_image(self.current_page.image_png)
        else:
            self.editor.clear()
            self.image.clear_image()
        self.editor.blockSignals(False)
        self.layout_pane.set_page(self.current_page)
        selector = self.layout_pane.page_selector
        selector.blockSignals(True)
        selector.clear()
        selector.addItems([f"{i + 1} · {p.source}" for i, p in enumerate(self.document.pages)])
        selector.setCurrentIndex(index)
        selector.blockSignals(False)
        self.editor.setExtraSelections([])
        self.refresh_controls()

    def delete_page(self):
        if self.worker is not None or self.current_page is None:
            return
        index = self.page_list.currentRow()
        if (
            QMessageBox.question(
                self,
                tr("Удалить страницу?"),
                tr(
                    (
                        "Удалить страницу {0} из документа вместе с её текстом? Исходный "
                        "файл останется на диске."
                    ),
                    f"{index + 1}",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.page_list.blockSignals(True)
        del self.document.pages[index]
        self.page_list.takeItem(index)
        for row, page in enumerate(self.document.pages):
            self.page_list.item(row).setText(f"{row + 1:02}\n{page.source}")
        selected = min(index, len(self.document.pages) - 1)
        self.page_list.setCurrentRow(selected)
        self.page_list.blockSignals(False)
        self.dirty = True
        self.select_page(selected)
        self.statusBar().showMessage(tr("Страница {0} удалена из документа.", f"{index + 1}"))

    def text_changed(self):
        if self.current_page:
            self.current_page.replace_text(self.editor.toPlainText(), tr("Ручная правка"))
            self.dirty = True
        self.refresh_controls()

    def undo_text(self):
        if self.worker is not None:
            return
        if self.current_page and self.current_page.undo():
            self.dirty = True
            self.select_page(self.page_list.currentRow())

    def layout_changed(self):
        ids = list(self.layout_pane.selected_ids)
        state = self.layout_pane.view_state()
        self.dirty = True
        self.select_page(self.page_list.currentRow())
        self.layout_pane.restore_view_state(state)
        self.layout_pane.select_ids(ids)

    def edit_selected_block(self):
        if self.layout_pane.selected_ids:
            self.layout_pane.edit_block(self.layout_pane.selected_ids[0])
        else:
            self.show_error(tr("Выберите текстовый блок для редактирования."))

    def text_selection_changed(self):
        page = self.current_page
        if not page or not page.blocks:
            return
        position = self.editor.textCursor().selectionStart()
        text = self.editor.toPlainText()
        start = len(text.encode("utf-16-le")[: position * 2].decode("utf-16-le"))
        offset = 0
        ids = []
        for block in page.blocks:
            if offset <= start <= offset + len(block.text):
                ids.append(block.id)
            offset += len(block.text) + 2
        self.layout_pane.select_ids(ids[:1])

    def layout_selection_changed(self, ids):
        from chtec.ui.menus import sync_actions

        sync_actions(self)
        from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
        from PySide6.QtWidgets import QTextEdit

        page = self.current_page
        if not page:
            return
        offset = 0
        selections = []
        for block in page.blocks:
            if block.id in ids:
                extra = QTextEdit.ExtraSelection()
                cursor = self.editor.textCursor()
                prefix = page.text[:offset]
                start = len(prefix.encode("utf-16-le")) // 2
                cursor.setPosition(start)
                cursor.setPosition(
                    start + len(block.text.encode("utf-16-le")) // 2,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                extra.cursor = cursor
                if not self.editor.hasFocus():
                    self.editor.blockSignals(True)
                    self.editor.setTextCursor(cursor)
                    self.editor.ensureCursorVisible()
                    self.editor.blockSignals(False)
                fmt = QTextCharFormat()
                fmt.setBackground(QColor("#bfe4ce"))
                extra.format = fmt
                selections.append(extra)
            offset += len(block.text) + 2
        self.editor.setExtraSelections(selections)
        if ids:
            block = next((b for b in page.blocks if b.id == ids[0]), None)
            if block:
                self.image.selected_region = block.rect
                self.image._position_selection()

    def bind_text(self):
        from uuid import uuid4

        from chtec.layout import TextBlock

        if self.worker or not self.current_page:
            return
        page = self.current_page
        region = self.binding_area
        cursor = self.editor.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        if not region or not selected:
            self.show_error(
                tr(("Выделите текст в редакторе и прямоугольник на фото, затем нажмите привязку."))
            )
            return
        raw = self.editor.toPlainText().encode("utf-16-le")
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        prefix = raw[: start * 2].decode("utf-16-le")
        suffix = raw[end * 2 :].decode("utf-16-le")
        if page.blocks:
            # Only unplaced text can acquire a new location; existing IDs stay intact.
            unplaced_start = len(page.text) - len(page.unplaced_text)
            if len(prefix) < unplaced_start:
                self.show_error(tr("Этот текст уже привязан. Правьте его существующий блок."))
                return
            remainder = prefix[unplaced_start:] + suffix
        else:
            remainder = prefix + suffix
        block = TextBlock(uuid4().hex, selected, tuple(region), verified=True)
        from chtec.ui.layout import BlockDialog

        dialog = BlockDialog(block, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from dataclasses import replace

        block = replace(block, text=dialog.editor.toPlainText(), angle=dialog.angle.value())
        page.replace_blocks([*page.blocks, block], tr("Ручная привязка"), unplaced_text=remainder)
        self.layout_changed()
        self.layout_pane.select_ids([block.id])
        self.view_tabs.setCurrentIndex(1)
        self.layout_pane.note.setText(
            tr("Блок привязан. Остальной текст сохранён без координат.")
            if remainder
            else tr("Блок привязан к оригиналу.")
        )

    def detect_zones(self):
        if self.worker or not self.current_page:
            return
        page = self.current_page
        snapshot = copy.deepcopy(page)

        def proposed(blocks):
            if self.close_after_task or self.worker.cancel.is_set():
                return
            if page.text != snapshot.text or page.blocks != snapshot.blocks:
                self.show_error(tr("Страница изменилась. Повторите поиск зон."))
                return
            dialog = LayoutProposalDialog(snapshot, blocks, self)
            dialog.draft.replace_blocks(blocks, unplaced_text=snapshot.text)
            dialog.pane.set_page(dialog.draft)
            dialog.pane.note.setText(
                tr(("Локальный поиск границ без чтения. Прежний текст сохранится без привязки."))
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                page.replace_blocks(
                    dialog.draft.blocks,
                    tr("Локальный поиск зон"),
                    unplaced_text=dialog.draft.unplaced_text,
                )
                self.layout_changed()
                self.view_tabs.setCurrentIndex(1)

        self.run_task(
            lambda job: TextZoneDetector().detect(
                snapshot, job.cancel, lambda n, total, text: job.progress.emit(n, text)
            ),
            proposed,
            lambda n, text: self.statusBar().showMessage(text),
            cancellable=True,
        )

    def recognize_layout(self):
        if self.worker or not self.current_page:
            return
        page = self.current_page
        snapshot = copy.deepcopy(page)
        try:
            service = LayoutRecognitionService(self.language_model(), detector=TextZoneDetector())
        except AppError as exc:
            self.show_error(str(exc))
            return

        def proposed(blocks):
            if self.close_after_task or self.worker.cancel.is_set():
                return
            if page.text != snapshot.text or page.blocks != snapshot.blocks:
                self.show_error(tr("Страница изменилась. Повторите распознавание."))
                return
            dialog = LayoutProposalDialog(snapshot, blocks, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                page.replace_blocks(
                    dialog.draft.blocks,
                    tr("Распознавание с расположением"),
                    unplaced_text=dialog.draft.unplaced_text,
                )
                self.layout_changed()
                self.view_tabs.setCurrentIndex(1)

        self.statusBar().showMessage(
            tr("Локальные границы и чтение фрагментов; потребуется проверка…")
        )
        self.run_task(
            lambda job: service.recognize(
                snapshot, job.cancel, lambda n, total, text: job.progress.emit(n, text)
            ),
            proposed,
            lambda n, text: self.statusBar().showMessage(text),
            cancellable=True,
        )

    def refine_block(self, block_id):
        if self.worker or not self.current_page:
            return
        page = self.current_page
        block = next((b for b in page.blocks if b.id == block_id), None)
        if block is None:
            return
        snapshot = copy.deepcopy(page)
        try:
            service = LayoutRecognitionService(self.language_model())
        except AppError as exc:
            self.show_error(str(exc))
            return

        def proposed(text):
            if self.close_after_task or self.worker.cancel.is_set():
                return
            if page.blocks != snapshot.blocks:
                self.show_error(tr("Разметка изменилась. Повторите уточнение."))
                return
            dialog = ProposalDialog(block.text, text, self)
            dialog.setWindowTitle(tr("Уточнение связанного блока"))
            if dialog.exec() == QDialog.DialogCode.Accepted:
                page.edit_block(block_id, dialog.proposed_text(), reason=tr("Уточнение блока"))
                self.layout_changed()
                self.layout_pane.select_ids([block_id])

        self.layout_pane.select_ids([block_id])
        self.run_task(lambda _: service.refine(snapshot, block), proposed, cancellable=True)

    def toggle_region_selection(self):
        self.image.set_selection_mode(self.region_select_button.isChecked())
        self.refresh_controls()
        if self.region_select_button.isChecked():
            self.statusBar().showMessage(
                tr("Обведите фрагмент левой кнопкой. Выделите в редакторе слова для замены.")
            )

    def region_selection_changed(self, region):
        self.binding_area = region
        self.region_select_button.setChecked(False)
        self.refresh_controls()
        if region is not None:
            self.statusBar().showMessage(
                tr(
                    (
                        "Фрагмент выделен. Выделите заменяемые слова в тексте и нажмите "
                        "«Прочитать фрагмент»."
                    )
                )
            )

    def binding_area_changed(self, region):
        self.binding_area = region

    def _apply_region_proposal(
        self, page, original, proposed, title, evidence, image=None, source_note=""
    ):
        if self.close_after_task or (self.worker is not None and self.worker.cancel.is_set()):
            return
        if page.text != original:
            self.show_error(
                tr("Текст страницы изменился. Повторите уточнение, чтобы не потерять правки.")
            )
            return
        dialog = ProposalDialog(original, proposed, self)
        dialog.setWindowTitle(title)
        note = QLabel(
            tr(
                (
                    "{0} · исходная страница {1}\nПроверьте чтение по оригиналу. Поле "
                    "справа можно исправить вручную.\n"
                ),
                f"{page.source}",
                f"{page.number}",
            )
            + (
                source_note
                or (
                    image.source_label
                    if image
                    else tr("Уточнение по четырём перекрывающимся фрагментам.")
                )
            )
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        dialog.layout().insertWidget(1, note)
        from PySide6.QtWidgets import QPlainTextEdit

        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(evidence)
        details.setMaximumHeight(170)
        details.hide()
        show_details = QPushButton(tr("Показать повторные чтения"))
        show_details.setCheckable(True)
        show_details.toggled.connect(details.setVisible)
        dialog.layout().insertWidget(2, show_details)
        dialog.layout().insertWidget(3, details)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        page.replace_text(dialog.proposed_text(), title)
        self.dirty = True
        if self.current_page is page:
            self.editor.blockSignals(True)
            self.editor.setPlainText(page.text)
            self.editor.blockSignals(False)
        self.refresh_controls()
        self.statusBar().showMessage(
            tr("Уточнение применено. Предыдущий текст доступен через отмену.")
        )

    def recognize_region(self):
        if self.current_page and self.current_page.blocks:
            if self.layout_pane.selected_ids:
                self.refine_block(self.layout_pane.selected_ids[0])
            else:
                self.show_error(tr("Выберите связанный текстовый блок в режиме «Как в оригинале»."))
            return
        if self.worker is not None or self.current_page is None or not self.image.selected_region:
            return
        if not (self.runtime_settings or self.settings).ocr_model:
            self.open_settings()
            if not (self.runtime_settings or self.settings).ocr_model:
                return
        page = self.current_page
        snapshot = copy.deepcopy(page)
        cursor = self.editor.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        # QTextCursor positions count UTF-16 code units, Python strings count Unicode scalars.
        visible = self.editor.toPlainText()
        prefix = visible.encode("utf-16-le")[: start * 2].decode("utf-16-le")
        suffix = visible.encode("utf-16-le")[end * 2 :].decode("utf-16-le")
        region = ImageRegion(*self.image.selected_region).validated()
        try:
            service = RegionRecognitionService(self.language_model())
        except AppError as exc:
            self.show_error(str(exc))
            return

        def result(reading):
            self._apply_region_proposal(
                page,
                snapshot.text,
                prefix + reading.text + suffix,
                tr("Распознавание выделенного фрагмента"),
                reading.text,
                reading.image,
            )

        self.statusBar().showMessage(tr("Читаю выделенный фрагмент по доступному оригиналу…"))
        self.run_task(lambda _: service.recognize(snapshot, region), result, cancellable=True)

    def review_regions(self):
        if self.current_page and self.current_page.blocks:
            self.recognize_layout()
            return
        if (
            self.worker is not None
            or self.current_page is None
            or not self.current_page.text.strip()
        ):
            return
        if not (self.runtime_settings or self.settings).ocr_model:
            self.open_settings()
            if not (self.runtime_settings or self.settings).ocr_model:
                return
        page = self.current_page
        snapshot = copy.deepcopy(page)
        try:
            service = RegionRecognitionService(self.language_model())
        except AppError as exc:
            self.show_error(str(exc))
            return

        def progress(count, text):
            self.progress_bar.setRange(0, 5)
            self.progress_bar.setValue(count)
            self.statusBar().showMessage(
                tr("Повторно прочитано фрагментов: {0}/4. ", f"{count}")
                + (tr("Модель готовит предложение…") if count == 4 else tr("Читаю следующий…"))
            )

        def result(review):
            if review is None:
                self.statusBar().showMessage(tr("Уточнение остановлено. Текст страницы сохранён."))
                return
            evidence = "\n\n".join(
                tr(
                    "Фрагмент {0}: {1}\n{2}",
                    f"{i}",
                    f"{reading.image.source_label}",
                    f"{reading.text}",
                )
                for i, reading in enumerate(review.readings, 1)
            )
            self._apply_region_proposal(
                page,
                snapshot.text,
                review.text,
                tr("Уточнение страницы по фрагментам"),
                evidence,
                source_note=(
                    tr("Четыре фрагмента исходного файла.")
                    if all(reading.image.used_original for reading in review.readings)
                    else tr("Есть фрагменты из сохранённой копии: оригинал недоступен.")
                ),
            )

        self.statusBar().showMessage(tr("Повторное чтение: 4 фрагмента и предложение правки…"))
        self.run_task(
            lambda job: service.review(
                snapshot, job.cancel, lambda count, text: job.progress.emit(count, text)
            ),
            result,
            progress,
            cancellable=True,
        )

    def run_task(self, operation, on_result, on_progress=None, cancellable=False):
        if self.worker is not None:
            return
        self.worker = Worker(operation, self)
        self.worker.result.connect(on_result)
        self.worker.error.connect(self.show_error)
        if on_progress:
            self.worker.progress.connect(on_progress)
        self.worker.finished.connect(self.task_finished)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.task_elapsed.start()
        self.update_task_time()
        self.task_timer.start()
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.setEnabled(True)
        self.refresh_controls()
        self.worker.start()

    def task_finished(self):
        # finished can precede native thread teardown; join before disposing Qt objects.
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.task_timer.stop()
        self.task_time.setText(
            tr("Завершено за {0} с", f"{self.task_elapsed.elapsed() / 1000:.1f}")
        )
        self.refresh_controls()
        if self.close_after_task:
            self.close_after_task = False
            QTimer.singleShot(0, self.close)

    def update_task_time(self):
        self.task_time.setText(tr("Ожидание: {0} с", f"{self.task_elapsed.elapsed() // 1000}"))

    def cancel_task(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.statusBar().showMessage(
                tr("Остановка после текущего запроса. Запрос ограничен тайм-аутом модели.")
            )

    def show_error(self, message):
        message = translate_message(message)
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, tr("Не удалось завершить операцию"), message)

    def open_files(self):
        if self.worker:
            return
        extensions = " ".join(f"*{ext}" for ext in self.registry.importers)
        files, _ = QFileDialog.getOpenFileNames(
            self,
            tr("Добавить фотографии и сканы"),
            "",
            tr("Поддерживаемые файлы ({0})", f"{extensions}"),
        )
        if files:
            self.load_paths(files)

    def load_paths(self, paths):
        if self.worker:
            return
        registry = self.registry
        existing_pages = list(self.document.pages)

        def load(_):
            pages = []
            for path in paths:
                remaining = MAX_PAGES - len(existing_pages) - len(pages)
                pages.extend(registry.import_file(Path(path), max_pages=remaining))
                validate_image_budget([*existing_pages, *pages])
            return pages

        self.statusBar().showMessage(tr("Подготовка страниц…"))
        self.run_task(load, self.files_loaded)

    def files_loaded(self, pages):
        try:
            self.add_pages(pages)
            self.statusBar().showMessage(tr("Добавлено страниц: {0}.", f"{len(pages)}"))
        except AppError as exc:
            self.show_error(str(exc))

    def language_model(self):
        settings = self.model_settings
        return (
            create_model(settings) if isinstance(settings, CloudSettings) else LocalModel(settings)
        )

    def save_connection(self):
        if self.settings_store:
            self.settings_store.save_profiles(self.profiles)

    def open_settings(self):
        if self.worker:
            return
        dialog = SettingsDialog(
            self.settings,
            self,
            profile=self.profiles.active if self.profiles else None,
            runtime_settings=self.runtime_settings,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if self.profiles is not None:
                candidate = copy.deepcopy(self.profiles)
                candidate.profiles[candidate.selected] = dialog.profile_value()
                candidate.validated()
                if self.settings_store:
                    self.settings_store.save_profiles(candidate)
                self.profiles = candidate
                self.settings = candidate.active.settings
                self.model_catalogs.pop(candidate.selected, None)
                self.runtime_settings = None
                self.activate_backend()
            else:
                self.settings = dialog.value()
                if self.settings_store:
                    self.settings_store.save(self.settings)
        except AppError as exc:
            self.show_error(str(exc))
        self.refresh_controls()

    @property
    def model_settings(self):
        if self.profiles is not None and self.runtime_settings is None:
            raise AppError(tr("Модель ещё не подключена. Выберите её или нажмите «Подключить»."))
        return self.runtime_settings or self.settings

    def fill_models(self, entries):
        if self.profiles is None:
            return
        selected = (
            self.profiles.active.model_path
            if self.profiles.selected == "unsloth"
            else self.settings.ocr_model
        )
        self.model_selector.blockSignals(True)
        self.model_selector.clear()
        for entry in entries:
            self.model_selector.addItem(model_entry_label(entry), entry)
        index = next((i for i, e in enumerate(entries) if e.id == selected), -1)
        if index < 0 and selected:
            entry = ModelEntry(selected, Path(selected).name)
            self.model_selector.addItem(model_entry_label(entry), entry)
            index = self.model_selector.count() - 1
        self.model_selector.setCurrentIndex(index)
        self.model_selector.blockSignals(False)

    def switch_model(self):
        if self.worker or self.profiles is None:
            return
        entry = self.model_selector.currentData()
        if entry is None:
            return
        profile = self.profiles.active
        profile.settings = replace(profile.settings, ocr_model=entry.id)
        if isinstance(profile.settings, CloudSettings):
            profile.settings = replace(profile.settings, vision=entry.vision)
        else:
            profile.settings = replace(profile.settings, edit_model=entry.id)
        if profile.name == "unsloth":
            profile.model_path = entry.path or entry.id
        self.settings = profile.settings
        self.activate_backend()

    def switch_backend(self):
        if self.worker or self.profiles is None:
            return
        selected = self.backend_selector.currentData()
        if selected == self.profiles.selected:
            return
        self.profiles.switch(selected)
        self.settings = self.profiles.active.settings
        self.runtime_settings = None
        self.fill_models(self.model_catalogs.get(selected, []))
        self.backend_status.setText(
            tr("{0} · подключение…", PROVIDERS[self.profiles.selected].title)
        )
        if isinstance(self.settings, CloudSettings) and not self.settings.api_key:
            self.backend_status.setText(
                tr("{0} · введите API-ключ в настройках.", PROVIDERS[self.profiles.selected].title)
            )
            self.open_settings()
        else:
            self.activate_backend()

    def refresh_models(self):
        if self.worker or self.profiles is None:
            return
        self.model_catalogs.pop(self.profiles.selected, None)
        self.activate_backend(catalog_only=True)

    def activate_backend(self, checked=False, *, catalog_only=False):
        if self.worker or self.profiles is None:
            return
        previous_runtime = self.runtime_settings if catalog_only else None
        if not catalog_only:
            self.runtime_settings = None
        profile = copy.deepcopy(self.profiles.active)
        cached = self.model_catalogs.get(profile.name)
        self.backend_status.setText(tr("{0} · получение каталога…", PROVIDERS[profile.name].title))

        def connect(worker):
            query = copy.deepcopy(profile)
            query.settings = replace(query.settings, timeout=min(query.settings.timeout, 20))
            entries = cached if cached is not None else catalog(query, cancel=worker.cancel)
            if worker.cancel.is_set():
                raise AppError(tr("Подключение отменено."))
            if catalog_only:
                return profile, entries, previous_runtime
            selected = (
                profile.model_path if profile.name == "unsloth" else profile.settings.ocr_model
            )
            entry = next((e for e in entries if e.id == selected), None)
            if entry is None and not selected:
                entry = next((e for e in entries if e.vision is True), None)
                entry = entry or next((e for e in entries if e.vision is not False), None)
            if entry:
                profile.settings = replace(profile.settings, ocr_model=entry.id)
                if isinstance(profile.settings, CloudSettings):
                    profile.settings = replace(profile.settings, vision=entry.vision)
                else:
                    profile.settings = replace(profile.settings, edit_model=entry.id)
                if profile.name == "unsloth":
                    profile.model_path = entry.path or entry.id
            if not profile.settings.ocr_model:
                raise AppError(
                    tr("Модели не найдены. Укажите модель в настройках и повторите подключение.")
                )
            profile.settings.validated()
            if isinstance(profile.settings, CloudSettings):
                if not profile.settings.api_key:
                    raise AppError(tr("Введите API-ключ в настройках выбранного поставщика."))
                if not any(e.id == profile.settings.ocr_model for e in entries):
                    raise AppError(
                        tr("Модель отсутствует в каталоге этого API-ключа. Выберите доступную.")
                    )
                self.backend_manager.deactivate()
                runtime = profile.settings
            else:
                runtime = self.backend_manager.activate(
                    profile, worker.cancel, lambda text: worker.progress.emit(0, text)
                )
            if worker.cancel.is_set():
                self.backend_manager.deactivate()
                raise AppError(tr("Подключение отменено."))
            return profile, entries, runtime

        def ready(result):
            updated, entries, runtime = result
            self.profiles.profiles[updated.name] = updated
            self.settings = updated.settings
            self.model_catalogs[updated.name] = entries
            self.fill_models(entries)
            self.runtime_settings = runtime
            self.backend_status.setText(
                tr("{0} · Готово · {1}", PROVIDERS[updated.name].title, self.settings.ocr_model)
                if runtime
                else tr("{0} · каталог обновлён; выберите модель.", PROVIDERS[updated.name].title)
            )
            self.backend_status.setToolTip(self.settings.base_url)
            try:
                self.save_connection()
            except AppError as exc:
                self.show_error(str(exc))

        self.run_task(
            connect,
            ready,
            lambda _, text: self.backend_status.setText(translate_message(text)),
            cancellable=True,
        )

        def failed(message):
            self.runtime_settings = previous_runtime
            state = (
                "{0} · подключение сохранено; каталог недоступен: {1}"
                if previous_runtime
                else "{0} · не подключено: {1}"
            )
            self.backend_status.setText(
                tr(state, PROVIDERS[self.profiles.selected].title, translate_message(message))
            )
            if not self.close_after_task and not self.worker.cancel.is_set():
                self.show_error(message)

        self.worker.error.disconnect(self.show_error)
        self.worker.error.connect(failed)

    def restore_connection(self):
        if self.close_after_task:
            return
        if self.worker:
            QTimer.singleShot(500, self.restore_connection)
        elif self.profiles and (self.settings.ocr_model or self.profiles.active.model_path):
            self.activate_backend()
        elif self.profiles and not isinstance(self.settings, CloudSettings):
            self.refresh_models()

    def recognize(self, all_pages):
        if self.current_page and self.current_page.blocks:
            if not all_pages:
                self.recognize_layout()
            return
        if self.worker or not self.current_page:
            return
        if not (self.runtime_settings or self.settings).ocr_model:
            self.open_settings()
            if self.worker:
                return
            if not (self.runtime_settings or self.settings).ocr_model:
                return
        indices = (
            list(range(len(self.document.pages))) if all_pages else [self.page_list.currentRow()]
        )
        if any(self.document.pages[i].text for i in indices):
            answer = QMessageBox.question(
                self,
                tr("Повторное распознавание"),
                tr("Заменить текст выбранных страниц? Предыдущая версия останется в истории."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            model = self.language_model()
        except AppError as exc:
            self.show_error(str(exc))
            return
        pages = [copy.deepcopy(self.document.pages[i]) for i in indices]

        def on_page(index, text):
            target = indices[index]
            self.document.pages[target].replace_text(text, tr("Распознавание"))
            self.dirty = True
            if target == self.page_list.currentRow():
                self.select_page(target)
            self.progress_bar.setRange(0, len(indices))
            self.progress_bar.setValue(index + 1)
            self.statusBar().showMessage(
                tr("Распознано {0} из {1} страниц.", f"{index + 1}", f"{len(indices)}")
            )

        self.statusBar().showMessage(tr("Модель читает рукопись…"))
        self.run_task(
            lambda job: DocumentService(model).recognize(
                pages, "", job.cancel, lambda i, text: job.progress.emit(i, text)
            ),
            lambda count: self.statusBar().showMessage(
                tr(
                    "Обработка завершена: {0} из {1} страниц. Сверьте текст с оригиналом.",
                    f"{count}",
                    f"{len(indices)}",
                )
            ),
            on_page,
            cancellable=True,
        )

    def edit_with_model(self):
        if self.worker or not self.current_page:
            return
        instruction = self.edit_request.text().strip()
        if not instruction:
            self.edit_request.setFocus()
            return
        page = self.current_page
        block_id = (
            self.layout_pane.selected_ids[0]
            if page.blocks and self.layout_pane.selected_ids
            else None
        )
        if page.blocks and not block_id:
            self.show_error(tr("Выберите блок для правки с помощью модели."))
            return
        original = next(b.text for b in page.blocks if b.id == block_id) if block_id else page.text
        try:
            model = self.language_model()
        except AppError as exc:
            self.show_error(str(exc))
            return

        def proposal(result):
            dialog = ProposalDialog(original, result, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if block_id:
                    page.edit_block(
                        block_id,
                        dialog.proposed_text(),
                        reason=tr("Правка LLM: {0}", f"{instruction}"),
                    )
                else:
                    page.replace_text(
                        dialog.proposed_text(), tr("Правка LLM: {0}", f"{instruction}")
                    )
                self.dirty = True
                self.select_page(self.page_list.currentRow())
                self.statusBar().showMessage(
                    tr("Правка применена. Предыдущий текст доступен через отмену.")
                )

        self.statusBar().showMessage(tr("Модель готовит предложение…"))
        self.run_task(lambda _: model.edit(original, instruction), proposal)

    def export_document(self):
        if self.worker or not self.document.pages:
            return
        dialog = ExportPreviewDialog(self.document, self.registry, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        extension = dialog.format.currentData()
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("Сохранить цифровую копию"),
            tr("Рукопись{0}", f"{extension}"),
            f"{extension[1:].upper()} (*{extension})",
        )
        if not path:
            return
        path = Path(path)
        if path.suffix.lower() != extension:
            path = path.with_suffix(extension)
            if (
                path.exists()
                and QMessageBox.question(
                    self,
                    tr("Заменить файл?"),
                    tr("Файл {0} существует. Заменить?", f"{path.name}"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        snapshot = copy.deepcopy(self.document)

        def saved(_):
            # Only a project contains source images and revision history.
            if extension == ".json":
                self.dirty = False
            self.statusBar().showMessage(tr("Сохранено: {0}", f"{path}"))

        self.run_task(lambda _: self.registry.export_file(snapshot, path), saved)

    def confirm_discard(self):
        if not self.dirty:
            return True
        return (
            QMessageBox.question(
                self,
                tr("Есть несохраненный проект"),
                tr(
                    (
                        "Продолжить без сохранения проекта? Для сохранения оригиналов, "
                        "текста и истории используйте формат JSON в предпросмотре."
                    )
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def new_document(self):
        if self.worker or not self.confirm_discard():
            return
        self.document = Document()
        self.page_list.clear()
        self.image.clear_image()
        self.dirty = False
        self.refresh_controls()

    def dragEnterEvent(self, event):
        if not self.worker and event.mimeData().hasUrls():
            if all(url.isLocalFile() for url in event.mimeData().urls()):
                event.acceptProposedAction()

    def dropEvent(self, event):
        self.load_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])

    def closeEvent(self, event):
        if self.worker:
            self.close_after_task = True
            self.cancel_task()
            self.statusBar().showMessage(
                tr(
                    (
                        "Останавливаю обработку перед закрытием. Ожидание ограничено "
                        "тайм-аутом модели."
                    )
                )
            )
            event.ignore()
        elif self.confirm_discard():
            self.save_ui()
            self.presentation.close()
            event.accept()
        else:
            event.ignore()
