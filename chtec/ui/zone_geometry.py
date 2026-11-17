"""A geometry gesture previews both views but commits a single domain revision."""

from dataclasses import replace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen, QPolygonF

from chtec.domain_errors import AppError
from chtec.ui.translations import tr


class CanvasGeometryMixin:
    def set_edit_mode(self, enabled):
        self.cancel_geometry()
        self.edit_mode = bool(enabled)
        self.draw_area = False
        self.pan_anchor = None
        self.setDragMode(self.DragMode.NoDrag if enabled else self.DragMode.ScrollHandDrag)
        self.viewport().unsetCursor()
        self.viewport().setToolTip("")
        self.viewport().update()

    def handle_points(self):
        if len(self.selected_ids) != 1:
            return {}
        item = self.block_items.get(self.selected_ids[0])
        if item is None:
            return {}
        return dict(
            zip(
                ("nw", "ne", "se", "sw"),
                (QPointF(x, y) for x, y in item.block.corners(*self.page_size)),
            )
        )

    def draw_geometry_handles(self, painter):
        if not self.edit_mode or self.busy:
            return
        points = self.handle_points()
        if not points:
            return
        size = 12 / max(abs(self.transform().m11()), 0.001)
        pen = QPen(QColor("#257356"))
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(QPolygonF(list(points.values())))
        painter.setBrush(QColor("white"))
        for point in points.values():
            painter.drawEllipse(point, size / 2, size / 2)

    def geometry_press(self, event):
        if not self.edit_mode or self.busy or event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = event.position().toPoint()
        handle = next(
            (
                key
                for key, point in self.handle_points().items()
                if (self.mapFromScene(point) - pos).manhattanLength() <= 12
            ),
            None,
        )
        if handle:
            identifier = self.selected_ids[0]
        else:
            item = self.block_at(pos)
            if item is None:
                return False
            identifier = item.block_id
            self.chosen.emit([identifier])
        self._geometry_gesture = {
            "id": identifier,
            "handle": handle,
            "block": self.block_items[identifier].block,
            "quad": tuple(
                (round(x / self.page_size[0], 12), round(y / self.page_size[1], 12))
                for x, y in self.block_items[identifier].block.corners(*self.page_size)
            ),
            "start": self.mapToScene(pos),
            "current": None,
        }
        event.accept()
        return True

    def geometry_move(self, event):
        gesture = self._geometry_gesture
        if gesture is None:
            if self.edit_mode and not self.busy:
                pos = event.position().toPoint()
                corner = any(
                    (self.mapFromScene(point) - pos).manhattanLength() <= 12
                    for point in self.handle_points().values()
                )
                inside = self.block_at(pos) is not None
                self.viewport().setCursor(
                    Qt.CursorShape.CrossCursor
                    if corner
                    else Qt.CursorShape.SizeAllCursor
                    if inside
                    else Qt.CursorShape.ArrowCursor
                )
                self.viewport().setToolTip(
                    tr("Перетащите угол, чтобы изменить форму зоны.")
                    if corner
                    else tr("Перетащите зону, чтобы переместить её.")
                )
            return False
        w, h = self.page_size
        point = self.mapToScene(event.position().toPoint())
        if point == gesture["start"]:
            gesture["current"] = None
            self.geometry_preview.emit(gesture["id"], gesture["block"])
            event.accept()
            return True
        points = gesture["quad"]
        handle = gesture["handle"]
        if handle is None:
            x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
            x1, y1 = max(p[0] for p in points), max(p[1] for p in points)
            dx = max(-x0, min(1 - x1, (point.x() - gesture["start"].x()) / w))
            dy = max(-y0, min(1 - y1, (point.y() - gesture["start"].y()) / h))
            quad = tuple((max(0, min(1, x + dx)), max(0, min(1, y + dy))) for x, y in points)
        else:
            index = ("nw", "ne", "se", "sw").index(handle)
            quad = list(points)
            quad[index] = (max(0, min(1, point.x() / w)), max(0, min(1, point.y() / h)))
            quad = tuple(quad)
        try:
            gesture["block"].with_quad(quad)
        except AppError:
            event.accept()
            return True
        gesture["current"] = quad
        self.geometry_preview.emit(gesture["id"], quad)
        event.accept()
        return True

    def geometry_release(self, event):
        if self._geometry_gesture is None:
            return False
        self.geometry_move(event)
        gesture = self._geometry_gesture
        self._geometry_gesture = None
        if gesture["current"] is not None and gesture["current"] != gesture["quad"]:
            self.geometry_committed.emit(gesture["id"], gesture["current"])
        event.accept()
        return True

    def cancel_geometry(self):
        gesture = getattr(self, "_geometry_gesture", None)
        self._geometry_gesture = None
        if gesture:
            self.geometry_preview.emit(gesture["id"], gesture["block"])

    def preview_geometry(self, identifier, rect):
        item = self.block_items.get(identifier)
        if item is not None:
            if hasattr(rect, "corners"):
                block = rect
            elif isinstance(rect[0], (tuple, list)):
                block = item.block.with_quad(rect)
            else:
                block = replace(item.block, rect=tuple(rect))
            item.set_geometry(block, *self.page_size)
            self.viewport().update()
