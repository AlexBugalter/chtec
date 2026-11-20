"""Single menu action per command, sharing controller availability."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from chtec.ui.translations import tr


def build_menus(w):
    w.actions = {}
    w.command_buttons = {}

    def menu(key, title, parent=None):
        value = (parent or w.menuBar()).addMenu(tr(title))
        value.setToolTipsVisible(True)
        w.actions[key] = value.menuAction()
        return value

    def action(parent, key, title, slot, shortcut=None, checkable=False, button=None):
        value = QAction(tr(title), w)
        value.setToolTip(tr(title))
        value.setStatusTip(tr(title))
        value.setCheckable(checkable)
        if shortcut:
            value.setShortcut(shortcut)
        value.triggered.connect(slot)
        parent.addAction(value)
        w.actions[key] = value
        if button:
            w.command_buttons[key] = button
        return value

    file = menu("file", "Файл")
    action(
        file,
        "new",
        "Новый документ",
        w.new_document,
        QKeySequence.StandardKey.New,
        button=w.new_button,
    )
    action(
        file, "open_project", "Открыть проект…", w.open_project, QKeySequence.StandardKey.Open
    )
    action(file, "open", "Добавить", w.open_files, button=w.open_button)
    file.addSeparator()
    action(file, "save_project", "Сохранить проект", w.save_project, QKeySequence.StandardKey.Save)
    action(file, "save_project_as", "Сохранить проект как…", w.save_project_as, "Ctrl+Shift+S")
    action(
        file,
        "save",
        "Предпросмотр и сохранение",
        w.export_document,
        button=w.export_button,
    )
    file.addSeparator()
    action(file, "close_project", "Закрыть проект", w.close_project, QKeySequence.StandardKey.Close)
    action(file, "exit", "Выход", w.close, "Alt+F4")
    edit = menu("edit", "Правка")
    action(edit, "undo", "Отменить правку", w.undo_text, "Ctrl+Z", button=w.undo_button)
    action(edit, "redo", "Повторить правку", w.redo_text, "Ctrl+Shift+Z")
    action(edit, "copy", "Копировать", w.editor.copy)
    action(edit, "edit_zone", "Редактировать", w.edit_selected_block)
    action(edit, "delete_zone", "Удалить зоны", w.layout_pane.delete_selected)
    doc = menu("document", "Документ")
    action(doc, "detect", "Найти зоны локально", w.detect_zones, button=w.detect_zones_button)
    ocr_menu = QMenu(w.recognize_button)
    w.recognize_button.setMenu(ocr_menu)
    for key, title, button in (
        ("layout", "Распознать с расположением", w.layout_button),
        ("page", "Распознать страницу", w.ocr_current),
        ("all", "Распознать всё", w.ocr_all),
    ):
        value = action(
            doc,
            "ocr_" + key,
            title,
            lambda checked=False, m=key: w.choose_ocr(m, True),
            button=button,
        )
        ocr_menu.addAction(value)
    doc.addSeparator()
    action(doc, "read_area", "Прочитать фрагмент", w.recognize_region, button=w.region_read_button)
    action(
        doc, "review", "Уточнить страницу по фрагментам", w.review_regions, button=w.review_button
    )
    action(doc, "bind", "Привязать выделенный текст к участку", w.bind_text, button=w.bind_button)
    action(doc, "delete_page", "Удалить страницу", w.delete_page, button=w.delete_button)
    templates = menu("templates", "Шаблоны", doc)
    action(templates, "save_template", "Сохранить зоны как шаблон…", w.layout_pane.save_template)
    action(templates, "open_template", "Применить шаблон…", w.layout_pane.open_template)
    view = menu("view", "Вид")
    modes = QActionGroup(w)
    for key, title, index in (
        ("view_text", "Текст и фрагменты", 0),
        ("view_layout", "Как в оригинале", 1),
    ):
        modes.addAction(
            action(
                view,
                key,
                title,
                lambda checked=False, i=index: w.view_tabs.setCurrentIndex(i),
                checkable=True,
            )
        )
    w.actions["view_text"].setChecked(True)
    view.addSeparator()
    action(view, "pages", "Список страниц", w.show_pages, checkable=True)
    action(view, "assistant", "Запрос к ИИ", w.show_assistant, checkable=True)
    group = QActionGroup(w)
    for mode, title in (
        ("both", "Оригинал и копия"),
        ("source", "Только оригинал"),
        ("digital", "Только копия"),
    ):
        group.addAction(
            action(
                view,
                "focus_" + mode,
                title,
                lambda checked=False, m=mode: w.set_focus_mode(m),
                checkable=True,
            )
        )
    action(view, "fit", "Подогнать страницу", lambda: fit_pages(w), "Ctrl+0")
    action(view, "reset", "Восстановить панели", w.reset_panels)
    settings = menu("settings", "Настройки")

    def connections():
        if w.profiles is None:
            w.open_settings()
        else:
            w.connections_dialog.show()
            w.connections_dialog.raise_()

    action(settings, "ai", "Управление ИИ", connections, button=w.settings_button)
    theme = menu("theme", "Тема", settings)
    group = QActionGroup(w)
    for key, title in (("light", "Светлая"), ("dark", "Тёмная")):
        group.addAction(
            action(
                theme,
                "theme_" + key,
                title,
                lambda checked=False, t=key: w.set_appearance(theme=t),
                checkable=True,
            )
        )
    scale = menu("scale", "Масштаб интерфейса", settings)
    for percent in (80, 90, 100, 110, 125, 150, 175, 200):
        action(
            scale,
            "scale_" + str(percent),
            f"{percent}%",
            lambda checked=False, p=percent: w.set_appearance(scale=p),
        )
    action(scale, "custom_scale", "Другой размер…", lambda: custom_scale(w))
    action(scale, "scale_reset", "Сбросить масштаб", lambda: w.set_appearance(scale=100))
    language = menu("language", "Язык", settings)
    group = QActionGroup(w)
    for code, title in (("ru", "Русский"), ("en", "English")):
        group.addAction(
            action(
                language,
                "language_" + code,
                title,
                lambda checked=False, c=code: w.set_appearance(language=c),
                checkable=True,
            )
        )
    help_menu = menu("help", "Справка")
    action(
        help_menu,
        "gestures",
        "Жесты и сочетания клавиш",
        lambda: QMessageBox.information(
            w,
            tr("Жесты и сочетания клавиш"),
            tr(
                (
                    "Колесо — масштаб; перетаскивание — перемещение. Правка зон: углы "
                    "меняют размер, Esc отменяет жест. Ctrl+O — открыть проект, Ctrl+S — "
                    "сохранить проект, Ctrl+0 — подогнать."
                )
            ),
        ),
    )
    action(
        help_menu,
        "about",
        "О программе",
        lambda: QMessageBox.information(
            w,
            tr("О программе"),
            tr("Чтец — распознавание рукописей с локальными и облачными моделями."),
        ),
    )


def custom_scale(w):
    scale, ok = QInputDialog.getInt(
        w, tr("Масштаб интерфейса"), tr("Размер, %"), w.presentation.preferences.scale, 80, 200, 5
    )
    if ok:
        w.set_appearance(scale=scale)


def fit_pages(w):
    w.image.change_zoom(1 / w.image.zoom)
    for canvas in (w.layout_pane.source, w.layout_pane.digital):
        canvas.fitInView(canvas.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        canvas.zoom = 1


def sync_actions(w):
    if not hasattr(w, "actions"):
        return
    for key, button in w.command_buttons.items():
        w.actions[key].setEnabled(button.isEnabled())
    busy = w.worker is not None
    w.actions["open_project"].setEnabled(not busy)
    w.actions["close_project"].setEnabled(not busy and bool(w.document.pages or w.project_path))
    for key in ("save_project", "save_project_as"):
        w.actions[key].setEnabled(not busy and bool(w.document.pages))
    page = w.current_page
    w.actions["redo"].setEnabled(not busy and bool(page and getattr(page, "_future", [])))
    for key in ("save_template", "open_template", "edit_zone", "delete_zone"):
        w.actions[key].setEnabled(not busy and page is not None)
    w.actions["save_template"].setEnabled(not busy and bool(page and page.blocks))
    for key in ("edit_zone", "delete_zone"):
        w.actions[key].setEnabled(not busy and bool(w.layout_pane.selected_ids))
    prefs = w.presentation.preferences
    w.actions["theme_" + prefs.theme].setChecked(True)
    w.actions["language_" + prefs.language].setChecked(True)
    w.actions["pages"].setChecked(prefs.pages_visible)
    w.actions["assistant"].setChecked(prefs.assistant_visible)
    w.actions["focus_" + prefs.focus].setChecked(True)
    w.recognize_button.setText(
        tr(
            {
                "layout": "Распознать зоны",
                "page": "Распознать страницу",
                "all": "Распознать всё",
            }[prefs.ocr_mode]
        )
    )
    w.recognize_button.setToolTip(w.recognize_button.text())
    w.recognize_button.setEnabled(w.actions["ocr_" + prefs.ocr_mode].isEnabled())
