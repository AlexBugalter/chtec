"""Document workspace construction and persistent presentation controls."""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from chtec.domain_errors import AppError
from chtec.providers import PROVIDERS
from chtec.ui.layout import LayoutPane
from chtec.ui.menus import build_menus, sync_actions
from chtec.ui.preferences import UiPreferences, UiPreferencesStore
from chtec.ui.presentation import PresentationManager, TranslatedStatusBar
from chtec.ui.translations import tr
from chtec.ui.widgets import ImagePreview, RevisionEditor


def build_workspace(w):
    w.setStatusBar(TranslatedStatusBar(w))
    central = QWidget()
    central.setObjectName("workspace")
    w.setCentralWidget(central)
    root = QVBoxLayout(central)
    root.setContentsMargins(10, 6, 10, 6)
    root.setSpacing(6)
    w.connections_dialog = QDialog(w)
    w.connections_dialog.setWindowTitle(tr("Управление ИИ"))
    w.connections_dialog.resize(660, 300)
    connection = QVBoxLayout(w.connections_dialog)
    w.settings_button = w.button(tr("Настройки ИИ"), w.open_settings)
    w.new_button = w.button(tr("Новый документ"), w.new_document)
    w.new_button.setParent(w)
    w.new_button.hide()
    if w.profiles is not None:
        connection.addWidget(QLabel(tr("Система")))
        w.backend_selector = QComboBox()
        w.backend_selector.setToolTip(tr("Система"))
        for name, provider in PROVIDERS.items():
            w.backend_selector.addItem(provider.title, name)
        w.backend_selector.setCurrentIndex(w.backend_selector.findData(w.profiles.selected))
        w.backend_selector.currentIndexChanged.connect(w.switch_backend)
        connection.addWidget(w.backend_selector)
        connection.addWidget(QLabel(tr("Модель")))
        w.model_selector = QComboBox()
        w.model_selector.setToolTip(tr("Модель"))
        w.model_selector.setMinimumContentsLength(18)
        w.model_selector.currentIndexChanged.connect(w.switch_model)
        connection.addWidget(w.model_selector)
        row = QHBoxLayout()
        w.refresh_models_button = w.button(
            tr("Обновить каталог доступных моделей"), w.refresh_models
        )
        w.activate_button = w.button(tr("Подключить"), w.activate_backend)
        row.addWidget(w.refresh_models_button)
        row.addWidget(w.activate_button)
        connection.addLayout(row)
        w.backend_status = QLabel(tr("Выберите платформу и модель."))
        w.backend_status.setWordWrap(True)
        connection.addWidget(w.backend_status)
    connection.addWidget(w.settings_button)
    connection.addStretch()

    toolbar = QHBoxLayout()
    w.open_button = w.button(tr("Добавить"), w.open_files)
    w.detect_zones_button = w.button(tr("Найти зоны"), w.detect_zones)
    w.export_button = w.button(tr("Сохранить"), w.export_document, True)
    w.recognize_button = QToolButton()
    w.recognize_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    w.recognize_button.clicked.connect(w.run_selected_ocr)
    for button in (w.open_button, w.detect_zones_button, w.recognize_button, w.export_button):
        toolbar.addWidget(button)
    toolbar.addStretch()
    w.model_label = QLabel()
    w.model_label.setObjectName("subtitle")
    w.model_label.setMaximumWidth(310)
    w.statusBar().addPermanentWidget(w.model_label)
    root.addLayout(toolbar)
    # Hidden command adapters retain controller availability and established tests.
    for name, text, slot in (
        ("ocr_current", "Распознать страницу", lambda: w.recognize(False)),
        ("ocr_all", "Распознать всё", lambda: w.recognize(True)),
        ("layout_button", "Распознать с расположением", w.recognize_layout),
        ("bind_button", "Привязать выделенный текст к участку", w.bind_text),
        ("review_button", "Уточнить страницу по фрагментам", w.review_regions),
    ):
        button = w.button(tr(text), slot)
        button.setParent(w)
        button.hide()
        setattr(w, name, button)

    w.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
    w.pages_pane = QWidget()
    pages = QVBoxLayout(w.pages_pane)
    pages.setContentsMargins(0, 0, 4, 0)
    w.pages_label = QLabel(tr("СТРАНИЦЫ · {0}", 0))
    w.pages_label.setObjectName("section")
    pages.addWidget(w.pages_label)
    w.page_list = QListWidget()
    w.page_list.setIconSize(QSize(44, 58))
    w.page_list.currentRowChanged.connect(w.select_page)
    pages.addWidget(w.page_list)
    w.delete_button = w.button(tr("Удалить"), w.delete_page)
    w.delete_button.setToolTip(tr("Удалить страницу"))
    pages.addWidget(w.delete_button)
    w.delete_action = QAction(w.page_list)
    w.delete_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
    w.delete_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
    w.delete_action.triggered.connect(w.delete_page)
    w.page_list.addAction(w.delete_action)
    w.pages_pane.setMinimumWidth(115)
    w.workspace_splitter.addWidget(w.pages_pane)

    w.text_splitter = QSplitter(Qt.Orientation.Horizontal)
    source = QWidget()
    column = QVBoxLayout(source)
    column.setContentsMargins(0, 0, 0, 0)
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("ОРИГИНАЛ")))
    row.addStretch()
    w.zoom_label = QLabel("100%")
    row.addWidget(w.zoom_label)
    row.addWidget(w.button("−", lambda: w.image.change_zoom(0.8)))
    row.addWidget(w.button("+", lambda: w.image.change_zoom(1.25)))
    w.region_select_button = w.button("□", w.toggle_region_selection)
    w.region_select_button.setToolTip(tr("Выделить фрагмент"))
    w.region_select_button.setCheckable(True)
    w.region_read_button = w.button(tr("Прочитать фрагмент"), w.recognize_region)
    row.addWidget(w.region_select_button)
    column.addLayout(row)
    w.image = ImagePreview()
    w.image.region_selected.connect(w.region_selection_changed)
    w.image.zoom_changed.connect(lambda zoom: w.zoom_label.setText(f"{zoom * 100:.0f}%"))
    column.addWidget(w.image, 1)
    # Read area is available in the document menu, close to the area-selection action.
    w.region_read_button.setParent(w)
    w.region_read_button.hide()
    w.text_splitter.addWidget(source)

    w.assistant_splitter = QSplitter(Qt.Orientation.Vertical)
    editor_pane = QWidget()
    column = QVBoxLayout(editor_pane)
    column.setContentsMargins(0, 0, 0, 0)
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("ЦИФРОВАЯ КОПИЯ")))
    row.addStretch()
    w.undo_button = w.button("↶", w.undo_text)
    w.undo_button.setToolTip(tr("Отменить правку"))
    row.addWidget(w.undo_button)
    column.addLayout(row)
    w.editor = RevisionEditor()
    w.editor.setPlaceholderText(tr("Здесь появится распознанный текст. Сверьте его с оригиналом."))
    w.editor.undo_requested.connect(w.undo_text)
    w.editor.textChanged.connect(w.text_changed)
    w.editor.selectionChanged.connect(w.text_selection_changed)
    w.editor.block_edit_requested.connect(w.edit_selected_block)
    column.addWidget(w.editor, 1)
    w.count_label = QLabel(tr("{0} знаков", 0))
    column.addWidget(w.count_label)
    w.assistant_splitter.addWidget(editor_pane)
    w.assistant_pane = QWidget()
    column = QVBoxLayout(w.assistant_pane)
    column.setContentsMargins(0, 0, 0, 0)
    row = QHBoxLayout()
    row.addWidget(QLabel(tr("Запрос к ИИ")))
    row.addStretch()
    hide = w.button("−", lambda: w.show_assistant(False))
    hide.setToolTip(tr("Свернуть или показать панель."))
    row.addWidget(hide)
    column.addLayout(row)
    row = QHBoxLayout()
    w.edit_request = QLineEdit()
    w.edit_request.setPlaceholderText(tr("Например: исправь пунктуацию, сохрани стиль"))
    w.edit_request.setToolTip(tr("Предложить правку страницы"))
    w.edit_request.returnPressed.connect(w.edit_with_model)
    row.addWidget(w.edit_request, 1)
    w.edit_button = w.button(tr("Отправить"), w.edit_with_model)
    w.edit_button.setToolTip(tr("Предложить правку страницы"))
    row.addWidget(w.edit_button)
    column.addLayout(row)
    w.assistant_splitter.addWidget(w.assistant_pane)
    w.assistant_splitter.setStretchFactor(0, 1)
    w.assistant_splitter.setStretchFactor(1, 0)
    w.text_splitter.addWidget(w.assistant_splitter)
    w.view_tabs = QTabWidget()
    w.view_tabs.addTab(w.text_splitter, tr("Текст и фрагменты"))
    w.layout_pane = LayoutPane()
    w.layout_pane.page_selector.hide()
    w.layout_pane.changed.connect(w.layout_changed)
    w.layout_pane.refine_requested.connect(w.refine_block)
    w.layout_pane.page_chosen.connect(w.layout_selection_changed)
    w.layout_pane.source.area_chosen.connect(w.binding_area_changed)
    w.layout_pane.page_selector.currentIndexChanged.connect(w.page_list.setCurrentRow)
    w.view_tabs.addTab(w.layout_pane, tr("Как в оригинале"))
    w.workspace_splitter.addWidget(w.view_tabs)
    root.addWidget(w.workspace_splitter, 1)
    w.progress_bar = QProgressBar()
    w.progress_bar.setFixedWidth(100)
    w.progress_bar.setTextVisible(False)
    w.progress_bar.hide()
    w.cancel_button = w.button(tr("Остановить"), w.cancel_task)
    w.cancel_button.hide()
    w.statusBar().addPermanentWidget(w.progress_bar)
    w.statusBar().addPermanentWidget(w.cancel_button)
    w.view_tabs.currentChanged.connect(w.workspace_mode_changed)
    build_menus(w)
    w.restore_panels()
    w.presentation.apply()
    w.ensure_tooltips()


class WorkspaceControls:
    def init_presentation(self):
        path = self.settings_store.path.with_name("interface.json") if self.settings_store else None
        self.ui_store = UiPreferencesStore(path) if path else None
        preferences = self.ui_store.load() if self.ui_store else UiPreferences()
        self.presentation = PresentationManager(QApplication.instance(), preferences, self)

    def set_appearance(self, **changes):
        self.presentation.apply(**changes)
        for canvas in (self.layout_pane.source, self.layout_pane.digital):
            canvas.retranslate()
        self.sync_workspace()
        self.ensure_tooltips()
        self.save_ui()

    def ensure_tooltips(self):
        for widget in self.findChildren(QWidget):
            if isinstance(widget, (QPushButton, QToolButton, QComboBox, QLineEdit, QTabWidget)):
                if not widget.toolTip():
                    text = (
                        widget.text()
                        if isinstance(widget, (QPushButton, QToolButton))
                        else widget.placeholderText()
                        if isinstance(widget, QLineEdit)
                        else tr("Переключить режим просмотра документа.")
                    )
                    widget.setToolTip(text or tr("Настройки"))
                    widget.setAccessibleName(text or tr("Настройки"))
            if isinstance(widget, QSplitter):
                for i in range(1, widget.count()):
                    widget.handle(i).setToolTip(
                        tr("Перетащите разделитель, чтобы изменить размер панели.")
                    )
        self.presentation.refresh()

    def save_ui(self):
        if not hasattr(self, "workspace_splitter"):
            return
        sizes = {
            name: split.sizes()
            for name, split in self.panel_splitters().items()
            if sum(split.sizes()) > 0
        }
        self.presentation.preferences = replace(self.presentation.preferences, sizes=sizes)
        if self.ui_store:
            try:
                self.ui_store.save(self.presentation.preferences)
            except AppError as exc:
                self.statusBar().showMessage(str(exc))

    def panel_splitters(self):
        return {
            "workspace": self.workspace_splitter,
            "text": self.text_splitter,
            "layout": self.layout_pane.splitter,
            "assistant": self.assistant_splitter,
        }

    def restore_panels(self, reset=False):
        prefs = self.presentation.preferences
        defaults = {
            "workspace": [170, 1100],
            "text": [550, 550],
            "layout": [550, 550],
            "assistant": [600, 110],
        }
        for name, split in self.panel_splitters().items():
            values = (prefs.sizes.get(name) if not reset else None) or defaults[name]
            if len(values) != split.count() or any(v == 0 for v in values):
                values = defaults[name]
            split.setSizes(values)
            split.setChildrenCollapsible(False)
            split.setHandleWidth(7)
        self.show_pages(True if reset else prefs.pages_visible, save=False)
        self.show_assistant(True if reset else prefs.assistant_visible, save=False)
        self.set_focus_mode("both" if reset else prefs.focus, save=False)
        self.view_tabs.setCurrentIndex(0 if reset else prefs.mode)

    def reset_panels(self):
        self.restore_panels(True)
        self.save_ui()

    def show_pages(self, visible, save=True):
        self.pages_pane.setVisible(visible)
        self.actions["pages"].setChecked(visible)
        self.presentation.preferences = replace(
            self.presentation.preferences, pages_visible=visible
        )
        if save:
            self.save_ui()

    def show_assistant(self, visible, save=True):
        self.assistant_pane.setVisible(visible)
        self.actions["assistant"].setChecked(visible)
        self.presentation.preferences = replace(
            self.presentation.preferences, assistant_visible=visible
        )
        if save:
            self.save_ui()

    def set_focus_mode(self, mode, save=True):
        for split in (self.text_splitter, self.layout_pane.splitter):
            split.widget(0).setVisible(mode != "digital")
            split.widget(1).setVisible(mode != "source")
        self.presentation.preferences = replace(self.presentation.preferences, focus=mode)
        self.actions["focus_" + mode].setChecked(True)
        if save:
            self.save_ui()

    def workspace_mode_changed(self, index):
        if hasattr(self, "actions"):
            self.presentation.preferences = replace(self.presentation.preferences, mode=index)
            self.actions["view_text"].setChecked(index == 0)
            self.actions["view_layout"].setChecked(index == 1)
            self.save_ui()

    def choose_ocr(self, mode, run=False):
        self.presentation.preferences = replace(self.presentation.preferences, ocr_mode=mode)
        self.update_ocr_label()
        self.save_ui()
        if run:
            self.run_selected_ocr()

    def update_ocr_label(self):
        names = {
            "layout": "Распознать зоны",
            "page": "Распознать страницу",
            "all": "Распознать всё",
        }
        self.recognize_button.setText(tr(names[self.presentation.preferences.ocr_mode]))
        self.recognize_button.setAccessibleName(self.recognize_button.text())
        self.recognize_button.setToolTip(self.recognize_button.text())

    def run_selected_ocr(self):
        mode = self.presentation.preferences.ocr_mode
        button = {"layout": self.layout_button, "page": self.ocr_current, "all": self.ocr_all}[mode]
        if button.isEnabled():
            button.click()

    def sync_workspace(self):
        sync_actions(self)
        self.update_ocr_label()
        mode = self.presentation.preferences.ocr_mode
        self.recognize_button.setEnabled(self.actions["ocr_" + mode].isEnabled())
        provider = PROVIDERS[self.profiles.selected].title if self.profiles else tr("Локальный API")
        status = tr(
            "ИИ: {0}",
            provider + " · " + ((self.runtime_settings or self.settings).ocr_model or "—"),
        )
        self.model_label.setText(
            QFontMetrics(self.model_label.font()).elidedText(
                status, Qt.TextElideMode.ElideRight, self.model_label.maximumWidth()
            )
        )
        self.model_label.setToolTip(status + "\n" + self.settings.base_url)
        self.presentation.refresh()
