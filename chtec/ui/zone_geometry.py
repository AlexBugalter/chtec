"""A geometry gesture previews both views but commits a single domain revision."""

from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen


class CanvasGeometryMixin:
    def set_edit_mode(self, enabled):
        self.cancel_geometry()
        self.edit_mode = bool(enabled)
        self.draw_area = False
        self.pan_anchor = None
        self.setDragMode(self.DragMode.NoDrag if enabled else self.DragMode.ScrollHandDrag)
        self.viewport().update()

    def handle_points(self):
        if len(self.selected_ids) != 1:
            return {}
        item = self.block_items.get(self.selected_ids[0])
        if item is None:
            return {}
        x0, y0, x1, y1 = item.block.rect
        w, h = self.page_size
        return {
            "nw": QPointF(x0 * w, y0 * h),
            "ne": QPointF(x1 * w, y0 * h),
            "se": QPointF(x1 * w, y1 * h),
            "sw": QPointF(x0 * w, y1 * h),
        }

    def draw_geometry_handles(self, painter):
        if not self.edit_mode or self.busy:
            return
        points = self.handle_points()
        if not points:
            return
        size = 8 / max(abs(self.transform().m11()), 0.001)
        pen = QPen(QColor("#257356"))
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(points["nw"], points["se"]))
        painter.setBrush(QColor("white"))
        for point in points.values():
            painter.drawRect(QRectF(point.x() - size / 2, point.y() - size / 2, size, size))

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
            "rect": self.block_items[identifier].block.rect,
            "start": self.mapToScene(pos),
            "current": None,
        }
        event.accept()
        return True

    def geometry_move(self, event):
        gesture = self._geometry_gesture
        if gesture is None:
            return False
        w, h = self.page_size
        point = self.mapToScene(event.position().toPoint())
        x0, y0, x1, y1 = gesture["rect"]
        handle = gesture["handle"]
        if handle is None:
            dx = max(-x0, min(1 - x1, (point.x() - gesture["start"].x()) / w))
            dy = max(-y0, min(1 - y1, (point.y() - gesture["start"].y()) / h))
            rect = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        else:
            min_w, min_h = min(0.01, 4 / w, x1 - x0), min(0.01, 4 / h, y1 - y0)
            x, y = max(0, min(1, point.x() / w)), max(0, min(1, point.y() / h))
            if "w" in handle:
                x0 = min(x, x1 - min_w)
            if "e" in handle:
                x1 = max(x, x0 + min_w)
            if "n" in handle:
                y0 = min(y, y1 - min_h)
            if "s" in handle:
                y1 = max(y, y0 + min_h)
            rect = (x0, y0, x1, y1)
        gesture["current"] = rect
        self.geometry_preview.emit(gesture["id"], rect)
        event.accept()
        return True

    def geometry_release(self, event):
        if self._geometry_gesture is None:
            return False
        self.geometry_move(event)
        gesture = self._geometry_gesture
        self._geometry_gesture = None
        if gesture["current"] is not None and gesture["current"] != gesture["rect"]:
            self.geometry_committed.emit(gesture["id"], gesture["current"])
        event.accept()
        return True

    def cancel_geometry(self):
        gesture = getattr(self, "_geometry_gesture", None)
        self._geometry_gesture = None
        if gesture:
            self.geometry_preview.emit(gesture["id"], gesture["rect"])

    def preview_geometry(self, identifier, rect):
        item = self.block_items.get(identifier)
        if item is not None:
            item.set_geometry(replace(item.block, rect=tuple(rect)), *self.page_size)
            self.viewport().update()
