from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QPlainTextEdit, QRubberBand, QScrollArea, QStyle

from chtec.ui.translations import tr


class RevisionEditor(QPlainTextEdit):
    """Use one domain revision history for typing, OCR, model edits and undo."""

    undo_requested = Signal()
    block_edit_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setUndoRedoEnabled(False)
        self.spatial_edit = False

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Undo):
            if not self.isReadOnly():
                self.undo_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def edit_menu(self):
        menu = QMenu(self)
        edit = menu.addAction(
            tr("Редактировать"),
            self.block_edit_requested.emit if self.spatial_edit else self.setFocus,
        )
        edit.setEnabled(not self.isReadOnly() or self.spatial_edit)
        undo = menu.addAction(tr("Отменить правку\tCtrl+Z"), self.undo_requested.emit)
        undo.setEnabled(not self.isReadOnly())
        menu.addSeparator()
        cut = menu.addAction(tr("Вырезать"), self.cut)
        cut.setEnabled(not self.isReadOnly() and self.textCursor().hasSelection())
        menu.addAction(tr("Копировать"), self.copy)
        paste = menu.addAction(tr("Вставить"), self.paste)
        paste.setEnabled(not self.isReadOnly())
        delete = menu.addAction(tr("Удалить"), lambda: self.textCursor().removeSelectedText())
        delete.setEnabled(not self.isReadOnly() and self.textCursor().hasSelection())
        menu.addSeparator()
        menu.addAction(tr("Выделить всё"), self.selectAll)
        return menu

    def contextMenuEvent(self, event):
        self.edit_menu().exec(event.globalPos())


class ImagePreview(QScrollArea):
    region_selected = Signal(object)
    zoom_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.label = QLabel(tr("Добавьте фото или PDF\nдля просмотра оригинала"))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("color: #7b837c; background: transparent; padding: 20px;")
        self.original = QPixmap()
        self.zoom = 1.0
        self._render_key = None
        self.selected_region = None
        self.selection_mode = False
        self._selection_anchor = None
        self._drag_anchor = None
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self.label)
        self.rubber.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWidget(self.label)
        self.viewport().installEventFilter(self)
        self.label.installEventFilter(self)
        self.viewport().setMouseTracking(True)
        self.label.setMouseTracking(True)
        self._pending_anchor = None
        self._anchor_timer = QTimer(self)
        self._anchor_timer.setSingleShot(True)
        self._anchor_timer.timeout.connect(self._restore_zoom_anchor)
        self.horizontalScrollBar().rangeChanged.connect(self._update_cursor)
        self.verticalScrollBar().rangeChanged.connect(self._update_cursor)

    def set_image(self, data: bytes):
        self.clear_selection()
        self.original.loadFromData(data, "PNG")
        self.zoom = 1.0
        self.render()
        self._update_cursor()
        self.zoom_changed.emit(self.zoom)

    def clear_image(self):
        self.clear_selection()
        self.original = QPixmap()
        self.zoom = 1.0
        self._render_key = None
        self.label.clear()
        self.label.setMinimumSize(0, 0)
        self.label.setText(tr("Добавьте фото или PDF\nдля просмотра оригинала"))
        self.zoom_changed.emit(self.zoom)
        self._update_cursor()

    def change_zoom(self, factor: float, anchor: QPoint | None = None):
        if self.original.isNull():
            return
        anchor = anchor or self.viewport().rect().center()
        fraction = self._fraction(self.label.mapFrom(self.viewport(), anchor))
        self.zoom = max(0.25, min(5.0, self.zoom * factor))
        self.render()
        self._update_cursor()
        self.zoom_changed.emit(self.zoom)
        self._pending_anchor = (self.original.cacheKey(), fraction, anchor)
        self._anchor_timer.start(0)

    def _restore_zoom_anchor(self):
        if self._pending_anchor is None:
            return
        source_key, fraction, anchor = self._pending_anchor
        self._pending_anchor = None
        if self.original.cacheKey() != source_key:
            return
        rect = self.image_rect()
        point = QPoint(
            round(rect.x() + fraction[0] * rect.width()),
            round(rect.y() + fraction[1] * rect.height()),
        )
        current = self.label.mapTo(self.viewport(), point)
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + current.x() - anchor.x()
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + current.y() - anchor.y()
        )

    def image_rect(self) -> QRectF:
        if self.original.isNull() or self.label.pixmap().isNull():
            return QRectF()
        pixmap = self.label.pixmap()
        bounds = self.label.contentsRect()
        width = pixmap.width() / pixmap.devicePixelRatio()
        height = pixmap.height() / pixmap.devicePixelRatio()
        return QRectF(
            bounds.x() + (bounds.width() - width) / 2,
            bounds.y() + (bounds.height() - height) / 2,
            width,
            height,
        )

    def _fraction(self, point):
        rect = self.image_rect()
        if rect.isEmpty():
            return 0.0, 0.0
        return (
            max(0.0, min(1.0, (point.x() - rect.x()) / rect.width())),
            max(0.0, min(1.0, (point.y() - rect.y()) / rect.height())),
        )

    def _position_selection(self):
        if not self.selected_region:
            self.rubber.hide()
            return
        x0, y0, x1, y1 = self.selected_region
        rect = self.image_rect()
        self.rubber.setGeometry(
            QRectF(
                rect.x() + x0 * rect.width(),
                rect.y() + y0 * rect.height(),
                (x1 - x0) * rect.width(),
                (y1 - y0) * rect.height(),
            ).toAlignedRect()
        )
        self.rubber.show()

    def clear_selection(self):
        self.selected_region = None
        self._selection_anchor = None
        self._drag_anchor = None
        self.selection_mode = False
        self.rubber.hide()
        self.region_selected.emit(None)
        self._update_cursor()

    def set_selection_mode(self, enabled: bool):
        self.selection_mode = enabled and not self.original.isNull()
        if self.selection_mode:
            self.selected_region = None
            self.rubber.hide()
        self._selection_anchor = None
        self._drag_anchor = None
        self._update_cursor()

    def _update_cursor(self):
        cursor = Qt.CursorShape.ArrowCursor
        if self.selection_mode:
            cursor = Qt.CursorShape.CrossCursor
        elif self._drag_anchor:
            cursor = Qt.CursorShape.ClosedHandCursor
        elif self._can_pan():
            cursor = Qt.CursorShape.OpenHandCursor
        self.viewport().setCursor(cursor)
        self.label.setCursor(cursor)

    def _can_pan(self):
        return self.horizontalScrollBar().maximum() > 0 or self.verticalScrollBar().maximum() > 0

    def eventFilter(self, watched, event):
        if watched is self.label and event.type() == QEvent.Type.Resize:
            self._position_selection()
        if self.original.isNull():
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                point = watched.mapTo(self.viewport(), event.position().toPoint())
                self.change_zoom(1.2 ** (delta / 120), point)
                event.accept()
                return True
        if event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ):
            point = watched.mapTo(self.label, event.position().toPoint())
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                if self.selection_mode and self.image_rect().contains(QPointF(point)):
                    self._selection_anchor = self._fraction(point)
                    self.selected_region = None
                    self.rubber.hide()
                    return True
                if self._can_pan() and not self.selection_mode:
                    self._drag_anchor = (
                        event.globalPosition().toPoint(),
                        self.horizontalScrollBar().value(),
                        self.verticalScrollBar().value(),
                    )
                    self._update_cursor()
                    return True
            if self._selection_anchor is not None:
                x0, y0 = self._selection_anchor
                x1, y1 = self._fraction(point)
                self.selected_region = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                self._position_selection()
                if event.type() == QEvent.Type.MouseButtonRelease:
                    rect = self.selected_region
                    if (rect[2] - rect[0]) * self.original.width() < 3 or (
                        rect[3] - rect[1]
                    ) * self.original.height() < 3:
                        self.selected_region = None
                        self.rubber.hide()
                    self.set_selection_mode(False)
                    self.region_selected.emit(self.selected_region)
                return True
            if self._drag_anchor is not None:
                start, horizontal, vertical = self._drag_anchor
                delta = event.globalPosition().toPoint() - start
                self.horizontalScrollBar().setValue(horizontal - delta.x())
                self.verticalScrollBar().setValue(vertical - delta.y())
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self._drag_anchor = None
                    self._update_cursor()
                return True
        return super().eventFilter(watched, event)

    def render(self):
        if self.original.isNull():
            return
        # The viewport width changes when a scrollbar appears. Using it here
        # lets scaling remove that scrollbar, which resizes the viewport again.
        # Reserve scrollbar space against the stable outer frame instead.
        scrollbar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        available = self.width() - 2 * self.frameWidth() - scrollbar - 36
        width = max(100, int(available * self.zoom))
        key = (self.original.cacheKey(), width)
        if key == self._render_key:
            self._position_selection()
            return
        image = self.original.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.label.setPixmap(image)
        self.label.setMinimumSize(image.width() + 32, image.height() + 32)
        self._render_key = key
        self._position_selection()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render()
