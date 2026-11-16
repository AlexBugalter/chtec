"""Linked source/digital spatial views and block editing."""

from dataclasses import replace
from uuid import uuid4

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from chtec.domain_errors import AppError
from chtec.layout import TextBlock
from chtec.spatial import fitted_text, image_size
from chtec.ui.widgets import RevisionEditor


class BlockItem(QGraphicsPolygonItem):
    def __init__(self, block, width, height, digital):
        cx, cy, w, h = block.geometry(width, height)
        super().__init__(
            QPolygonF(
                [
                    QPointF(-w / 2, -h / 2),
                    QPointF(w / 2, -h / 2),
                    QPointF(w / 2, h / 2),
                    QPointF(-w / 2, h / 2),
                ]
            )
        )
        self.block_id = block.id
        self.block = block
        self.setPos(cx, cy)
        self.setRotation(block.angle)
        self.setFlag(self.GraphicsItemFlag.ItemIsSelectable)
        self.setToolTip(
            ("Проверено" if block.verified else "Привязка требует проверки") + "\n" + block.text
        )
        if digital:
            lines, size = fitted_text(block.text, w, h)
            font = QFont("Arial")
            font.setPointSizeF(size * 72 / 96)
            for i, line in enumerate(lines):
                item = QGraphicsSimpleTextItem(line, self)
                item.setFont(font)
                item.setPos(-w / 2, -h / 2 + i * size * 1.2)
                item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.highlight(False, digital)

    def highlight(self, selected, digital):
        pen = QPen(QColor("#257356" if selected else "#9aaba1"))
        pen.setWidthF(2 if selected else 0.6)
        pen.setCosmetic(True)
        if not self.block.verified and not selected:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(QColor(65, 155, 111, 48 if selected else 0)))


class LayoutCanvas(QGraphicsView):
    chosen = Signal(object)
    area_chosen = Signal(object)
    edit_requested = Signal(str)
    menu_requested = Signal(str, object)

    def __init__(self, digital=False):
        super().__init__()
        self.digital = digital
        self.setScene(QGraphicsScene(self))
        self.block_items = {}
        self.selected_ids = []
        self.page_size = (1, 1)
        self.draw_area = False
        self.anchor = None
        self.area_item = None
        self.zoom = 1.0
        self.pan_anchor = None
        self._source_pixmap = QPixmap()
        self.setDragMode(self.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(self.ViewportAnchor.AnchorUnderMouse)
        # Stable viewport dimensions avoid fit/scrollbar resize oscillation.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scene().selectionChanged.connect(self.selection_changed)
        self.setMinimumSize(240, 200)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.zoom == 1.0 and self.scene() and not self.sceneRect().isEmpty():
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_page(self, page):
        # Decode before replacing the visible page: failure must not erase its photo.
        pixmap = QPixmap()
        page_size = (1, 1)
        if page is not None:
            if self.digital:
                page_size = image_size(page)
            else:
                if not pixmap.loadFromData(page.image_png, "PNG") or pixmap.isNull():
                    raise AppError("Не удалось загрузить изображение страницы для просмотра.")
                page_size = (pixmap.width(), pixmap.height())
        self._source_pixmap = pixmap
        self.page_size = page_size
        self.pan_anchor = None
        self.draw_area = False
        self.setDragMode(self.DragMode.ScrollHandDrag)
        self.scene().blockSignals(True)
        self.scene().clear()
        self.block_items = {}
        self.area_item = None
        self.anchor = None
        self.selected_ids = []
        if page is None:
            self.scene().setSceneRect(0, 0, 1, 1)
            self.scene().blockSignals(False)
            self.viewport().update()
            return
        w, h = self.page_size
        self.scene().setSceneRect(0, 0, w, h)
        if self.digital:
            self.scene().addRect(
                0, 0, w, h, QPen(Qt.GlobalColor.white), QBrush(Qt.GlobalColor.white)
            )
            if not page.blocks:
                font = QFont("Arial")
                font.setPixelSize(max(12, round(w / 45)))
                message = self.scene().addSimpleText(
                    "Нет координат текста.\nИспользуйте текстовый вид\nили создайте разметку.", font
                )
                bounds = message.boundingRect()
                message.setPos((w - bounds.width()) / 2, (h - bounds.height()) / 2)
        for block in page.blocks:
            item = BlockItem(block, w, h, self.digital)
            self.scene().addItem(item)
            self.block_items[block.id] = item
        self.scene().blockSignals(False)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom = 1.0
        self.viewport().update()

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        # The photo is a persistent background, independent of selectable scene items
        # and their indexing/lifetime during zone selection and overlay rebuilding.
        if not self.digital and not self._source_pixmap.isNull():
            painter.drawPixmap(QPointF(0, 0), self._source_pixmap)

    def selection_changed(self):
        ids = [i.block_id for i in self.scene().selectedItems() if isinstance(i, BlockItem)]
        self.chosen.emit(ids)

    def select_ids(self, ids):
        self.selected_ids = [i for i in ids if i in self.block_items]
        self.scene().blockSignals(True)
        for id, item in self.block_items.items():
            item.setSelected(id in self.selected_ids)
            item.highlight(id in self.selected_ids, self.digital)
        self.scene().blockSignals(False)
        if self.selected_ids:
            bounds = QRectF()
            for id in self.selected_ids:
                bounds = bounds.united(self.block_items[id].sceneBoundingRect())
            self.ensureVisible(bounds, 20, 20)

    def block_at(self, pos):
        item = self.itemAt(pos)
        while item is not None and not isinstance(item, BlockItem):
            item = item.parentItem()
        return item

    def mousePressEvent(self, event):
        if self.draw_area and event.button() == Qt.MouseButton.LeftButton:
            self.anchor = self.mapToScene(event.position().toPoint())
            if self.area_item:
                self.scene().removeItem(self.area_item)
            self.area_item = self.scene().addRect(
                QRectF(self.anchor, self.anchor), QPen(QColor("#257356"))
            )
            return
        item = self.block_at(event.position().toPoint())
        if item and event.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit([item.block_id])
            self.pan_anchor = (
                event.globalPosition().toPoint(),
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value(),
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pan_anchor is not None:
            start, x, y = self.pan_anchor
            delta = event.globalPosition().toPoint() - start
            self.horizontalScrollBar().setValue(x - delta.x())
            self.verticalScrollBar().setValue(y - delta.y())
            return
        if self.anchor is not None:
            rect = QRectF(self.anchor, self.mapToScene(event.position().toPoint())).normalized()
            self.area_item.setRect(rect.intersected(self.sceneRect()))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.pan_anchor is not None:
            self.pan_anchor = None
            return
        if self.anchor is not None:
            rect = self.area_item.rect()
            self.anchor = None
            self.draw_area = False
            self.setDragMode(self.DragMode.ScrollHandDrag)
            if rect.width() > 3 and rect.height() > 3:
                w, h = self.page_size
                self.area_chosen.emit(
                    (rect.left() / w, rect.top() / h, rect.right() / w, rect.bottom() / h)
                )
                self.chosen.emit(
                    [
                        id
                        for id, item in self.block_items.items()
                        if item.sceneBoundingRect().intersects(rect)
                    ]
                )
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.pan_anchor = None
        item = self.block_at(event.position().toPoint())
        if item:
            self.edit_requested.emit(item.block_id)
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        item = self.block_at(event.pos())
        if item:
            self.chosen.emit([item.block_id])
            self.menu_requested.emit(item.block_id, event.globalPos())

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if 0.25 <= self.zoom * factor <= 8:
            self.scale(factor, factor)
            self.zoom *= factor
        event.accept()


class BlockDialog(QDialog):
    def __init__(self, block, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактировать текстовый блок")
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        self.editor = RevisionEditor()
        self.editor.setPlainText(block.text)
        # This local draft has its own undo until explicitly applied.
        self.editor.setUndoRedoEnabled(True)
        self.editor.undo_requested.connect(self.editor.undo)
        layout.addWidget(self.editor)
        form = QFormLayout()
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-360, 360)
        self.angle.setSuffix("°")
        self.angle.setValue(block.angle)
        form.addRow("Поворот по часовой стрелке", self.angle)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class LayoutPane(QWidget):
    changed = Signal()
    refine_requested = Signal(str)
    page_chosen = Signal(object)

    def __init__(self):
        super().__init__()
        self.page = None
        self.area = None
        self.selected_ids = []
        self.busy = False
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.page_selector = QComboBox()
        toolbar.addWidget(self.page_selector)
        self.draw = QPushButton("Выделить участок")
        self.draw.clicked.connect(self.begin_area)
        self.add = QPushButton("Создать / привязать блок")
        self.add.clicked.connect(self.add_block)
        self.read = QPushButton("Уточнить выбранный блок")
        self.read.clicked.connect(
            lambda: self.refine_requested.emit(self.selected_ids[0]) if self.selected_ids else None
        )
        self.relocate = QPushButton("Перепривязать")
        self.relocate.clicked.connect(self.relocate_block)
        for button in (self.draw, self.add, self.read, self.relocate):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.note = QLabel(
            "Фото слева · цифровая копия справа · колесо — масштаб · двойной щелчок — правка"
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        row = QHBoxLayout()
        self.source = LayoutCanvas()
        self.digital = LayoutCanvas(True)
        row.addWidget(self.source)
        row.addWidget(self.digital)
        layout.addLayout(row)
        for canvas in (self.source, self.digital):
            canvas.chosen.connect(self.select_ids)
            canvas.edit_requested.connect(self.edit_block)
            canvas.menu_requested.connect(self.context_menu)
        self.source.area_chosen.connect(self.set_area)

    def set_area(self, area):
        self.area = area
        self.note.setText(
            "Область выделена. Создайте блок или выберите пересекающийся текст для уточнения."
        )

    def begin_area(self):
        self.source.draw_area = True
        self.source.setDragMode(self.source.DragMode.NoDrag)
        self.note.setText("Обведите участок на фотографии слева.")

    def set_page(self, page):
        # Bind the new page only after its photo can actually be displayed.
        self.source.set_page(page)
        self.digital.set_page(page)
        self.page = page
        self.area = None
        self.selected_ids = []
        if page and page.unplaced_text:
            self.note.setText(
                "Остался текст без координат. Выделите его во вкладке «Текст и фрагменты» "
                "и привяжите к участку."
            )
        elif page and not page.blocks:
            self.note.setText(
                "Разметки пока нет. Создайте области вручную "
                "или нажмите «Распознать с расположением»."
            )
        else:
            self.note.setText(
                "Фото слева · цифровая копия справа · колесо — масштаб · двойной щелчок — правка"
            )
        self.set_busy(self.busy)

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.draw, self.add, self.read, self.relocate):
            widget.setEnabled(not busy and self.page is not None)
        self.read.setEnabled(not busy and len(self.selected_ids) == 1)
        self.relocate.setEnabled(not busy and len(self.selected_ids) == 1 and self.area is not None)

    def select_ids(self, ids):
        self.selected_ids = list(ids)
        self.source.select_ids(ids)
        self.digital.select_ids(ids)
        self.set_busy(self.busy)
        self.page_chosen.emit(ids)

    def relocate_block(self):
        if self.busy or not self.area or len(self.selected_ids) != 1:
            return
        id = self.selected_ids[0]
        self.page.replace_blocks(
            [
                replace(b, rect=tuple(self.area), verified=True) if b.id == id else b
                for b in self.page.blocks
            ],
            "Новая область блока",
        )
        self.set_page(self.page)
        self.select_ids([id])
        self.changed.emit()

    def change_block(self, id, text, angle):
        if self.busy:
            return
        self.page.edit_block(id, text, angle)
        self.set_page(self.page)
        self.select_ids([id])
        self.changed.emit()

    def edit_block(self, id):
        if self.busy or not self.page:
            return
        block = next(b for b in self.page.blocks if b.id == id)
        dialog = BlockDialog(block, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.change_block(id, dialog.editor.toPlainText(), dialog.angle.value())

    def add_block(self):
        if self.busy or not self.page:
            return
        if not self.area:
            self.note.setText("Сначала обведите область на фотографии кнопкой «Выделить участок».")
            return
        block = TextBlock(uuid4().hex, "", self.area, verified=True)
        dialog = BlockDialog(block, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        blocks = list(self.page.blocks)
        unplaced = self.page.unplaced_text if blocks else self.page.text
        blocks.append(replace(block, text=dialog.editor.toPlainText(), angle=dialog.angle.value()))
        self.page.replace_blocks(blocks, "Ручная разметка", unplaced_text=unplaced)
        self.set_page(self.page)
        self.select_ids([block.id])
        self.changed.emit()

    def context_menu(self, id, pos):
        block = next(b for b in self.page.blocks if b.id == id)
        menu = QMenu(self)
        edit = menu.addAction("Редактировать", lambda: self.edit_block(id))
        menu.addAction("Копировать", lambda: QApplication.clipboard().setText(block.text))
        paste = menu.addAction(
            "Вставить", lambda: self.change_block(id, QApplication.clipboard().text(), block.angle)
        )
        delete = menu.addAction("Удалить текст", lambda: self.change_block(id, "", block.angle))
        refine = menu.addAction("Уточнить с помощью модели", lambda: self.refine_requested.emit(id))
        verify = menu.addAction("Подтвердить положение и угол", lambda: self.verify_geometry(id))
        unlink = menu.addAction("Убрать привязку, сохранить текст", lambda: self.unlink(id))
        earlier = menu.addAction("Раньше в порядке чтения", lambda: self.reorder(id, -1))
        later = menu.addAction("Позже в порядке чтения", lambda: self.reorder(id, 1))
        undo = menu.addAction("Отменить правку", self.undo)
        for action in (edit, paste, delete, refine, undo, unlink, verify, earlier, later):
            action.setEnabled(not self.busy)
        menu.exec(pos)

    def verify_geometry(self, id):
        if self.busy:
            return
        self.page.replace_blocks(
            [replace(b, verified=True) if b.id == id else b for b in self.page.blocks],
            "Проверка геометрии",
        )
        self.set_page(self.page)
        self.select_ids([id])
        self.changed.emit()

    def reorder(self, id, step):
        if self.busy:
            return
        blocks = list(self.page.blocks)
        index = next(i for i, b in enumerate(blocks) if b.id == id)
        target = max(0, min(len(blocks) - 1, index + step))
        blocks.insert(target, blocks.pop(index))
        self.page.replace_blocks(blocks, "Порядок чтения")
        self.set_page(self.page)
        self.select_ids([id])
        self.changed.emit()

    def unlink(self, id):
        if self.busy:
            return
        block = next(b for b in self.page.blocks if b.id == id)
        unplaced = "\n\n".join(t for t in (self.page.unplaced_text, block.text) if t)
        self.page.replace_blocks(
            [b for b in self.page.blocks if b.id != id], "Отмена привязки", unplaced_text=unplaced
        )
        self.set_page(self.page)
        self.changed.emit()

    def undo(self):
        if not self.busy and self.page and self.page.undo():
            self.set_page(self.page)
            self.changed.emit()


class LayoutProposalDialog(QDialog):
    def __init__(self, page, blocks, parent=None):
        import copy

        super().__init__(parent)
        self.setWindowTitle("Проверка расположения и текста")
        self.resize(1200, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Разметка предложена моделью. Сверьте области, углы и текст. "
                "Двойной щелчок — исправить блок."
            )
        )
        self.draft = copy.deepcopy(page)
        self.draft.replace_blocks(blocks, unplaced_text="")
        self.pane = LayoutPane()
        self.pane.set_page(self.draft)
        layout.addWidget(self.pane)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
