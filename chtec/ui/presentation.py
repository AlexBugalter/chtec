"""Live appearance changes without rebuilding documents or connections."""

import weakref
from dataclasses import replace

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QStatusBar,
    QTabWidget,
    QWidget,
)
from shiboken6 import isValid

from chtec.backends.catalog import ModelEntry
from chtec.ui import translations
from chtec.ui.style import stylesheet
from chtec.ui.translations import model_entry_label, tr, translate_message


class PresentationManager(QObject):
    def __init__(self, app, preferences, root=None):
        super().__init__(root)
        self.app = app
        self.root = root
        self.preferences = preferences.validated()
        self.bindings = {}
        self.pending = False
        self.closed = False
        app.installEventFilter(self)
        translations.LANGUAGE = preferences.language

    def bind(self, widget, property_name, source, *args):
        self.bindings[(id(widget), property_name)] = (weakref.ref(widget), source, args)
        widget.setProperty(property_name, tr(source, *args))

    def belongs(self, widget):
        return self.root is None or widget is self.root or self.root.isAncestorOf(widget)

    def eventFilter(self, watched, event):
        if (
            not self.closed
            and event.type() in (QEvent.Type.Show, QEvent.Type.Polish)
            and isinstance(watched, QWidget)
            and self.belongs(watched)
            and not self.pending
        ):
            self.pending = True
            QTimer.singleShot(0, self.refresh)
        return False

    def refresh(self):
        self.pending = False
        if self.closed:
            return
        objects = (
            [self.root, *self.root.findChildren(QObject)]
            if self.root
            else list(QApplication.allWidgets())
        )
        for obj in objects:
            if not isValid(obj):
                continue
            properties = (
                ["toolTip", "windowTitle", "accessibleName"] if isinstance(obj, QWidget) else []
            )
            if isinstance(obj, (QLabel, QAbstractButton, QAction)):
                properties.append("text")
            if isinstance(obj, QAction):
                properties += ["toolTip", "statusTip"]
                font = QFont("Segoe UI")
                font.setPointSizeF(10 * self.preferences.scale / 100)
                if obj.font() != font:
                    obj.setFont(font)
            if isinstance(obj, (QLineEdit, QPlainTextEdit)):
                properties.append("placeholderText")
            if isinstance(obj, (QGroupBox, QMenu)):
                properties.append("title")
            for name in properties:
                value = obj.property(name)
                if not isinstance(value, str) or not value:
                    self.bindings.pop((id(obj), name), None)
                    continue
                record = translations.RENDERED.get(value)
                previous = self.bindings.get((id(obj), name))
                if record is None and previous:
                    _, source, args = previous
                    if tr(source, *args) == value:
                        record = (source, args)
                if record is None and value in translations.CATALOG:
                    record = (value, ())
                if record is None and isinstance(obj, QLabel) and name == "text":
                    translated = translate_message(value)
                    record = translations.RENDERED.get(translated)
                if record:
                    self.bindings[(id(obj), name)] = (weakref.ref(obj), *record)
                    obj.setProperty(name, tr(record[0], *record[1]))
                else:
                    self.bindings.pop((id(obj), name), None)
            if isinstance(obj, QDialogButtonBox):
                for role, source in (
                    (QDialogButtonBox.StandardButton.Apply, "Применить"),
                    (QDialogButtonBox.StandardButton.Cancel, "Отмена"),
                    (QDialogButtonBox.StandardButton.Yes, "Да"),
                    (QDialogButtonBox.StandardButton.No, "Нет"),
                ):
                    button = obj.button(role)
                    if button and button.text() in (
                        "Apply",
                        "Cancel",
                        "Yes",
                        "No",
                        "Применить",
                        "Отмена",
                        "Да",
                        "Нет",
                    ):
                        button.setText(tr(source))
            if isinstance(obj, QTabWidget):
                for i in range(obj.count()):
                    value = obj.tabText(i)
                    source, args = translations.RENDERED.get(value, (value, ()))
                    obj.setTabText(i, tr(source, *args))
            if isinstance(obj, QComboBox):
                was_blocked = obj.blockSignals(True)
                for i in range(obj.count()):
                    entry = obj.itemData(i)
                    if isinstance(entry, ModelEntry):
                        obj.setItemText(i, model_entry_label(entry))
                    else:
                        record = translations.RENDERED.get(obj.itemText(i))
                        if record:
                            obj.setItemText(i, tr(record[0], *record[1]))
                obj.blockSignals(was_blocked)
            if isinstance(obj, QComboBox):
                blocked = obj.blockSignals(True)
                for i in range(obj.count()):
                    value = obj.itemText(i)
                    record = translations.RENDERED.get(value)
                    if record:
                        obj.setItemText(i, tr(record[0], *record[1]))
                obj.blockSignals(blocked)
            if isinstance(obj, (QAbstractButton, QAbstractSpinBox, QLineEdit, QComboBox)):
                if not obj.toolTip():
                    label = None
                    for form in obj.window().findChildren(QFormLayout):
                        label = form.labelForField(obj)
                        if label is not None:
                            break
                    if isinstance(label, QLabel):
                        tip = label.text()
                    elif isinstance(obj, QAbstractButton):
                        tip = {
                            "+": tr("Увеличить масштаб"),
                            "−": tr("Уменьшить масштаб"),
                            "↶": tr("Отменить правку"),
                        }.get(obj.text(), obj.text())
                    else:
                        tip = tr("Выберите значение")
                    obj.setToolTip(tip)
                    obj.setAccessibleName(tip)

    def apply(self, **changes):
        self.preferences = replace(self.preferences, **changes).validated()
        self.refresh()
        translations.LANGUAGE = self.preferences.language
        target = self.root or self.app
        target.setStyleSheet(stylesheet(self.preferences.theme, self.preferences.scale))
        dark = self.preferences.theme == "dark"
        palette = QPalette(self.app.style().standardPalette())
        colors = {
            QPalette.ColorRole.Window: "#181B20" if dark else "#F7F8FA",
            QPalette.ColorRole.Base: "#22262D" if dark else "#FFFFFF",
            QPalette.ColorRole.Text: "#ECEFF4" if dark else "#20242B",
            QPalette.ColorRole.WindowText: "#ECEFF4" if dark else "#20242B",
            QPalette.ColorRole.ButtonText: "#ECEFF4" if dark else "#20242B",
        }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        target.setPalette(palette)
        for key, (reference, source, args) in list(self.bindings.items()):
            widget = reference()
            if widget is None or not isValid(widget):
                del self.bindings[key]
            else:
                widget.setProperty(key[1], tr(source, *args))
        self.refresh()

        if self.root is not None:
            bar = self.root.findChild(QStatusBar)
            if bar and bar.currentMessage():
                bar.showMessage(bar.currentMessage())

    def close(self):
        self.closed = True
        self.app.removeEventFilter(self)
        translations.LANGUAGE = "ru"
        if self.root is None:
            self.app.setStyleSheet("")


class TranslatedStatusBar(QStatusBar):
    def showMessage(self, message, timeout=0):
        super().showMessage(translate_message(message), timeout)
