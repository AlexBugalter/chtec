import threading
import traceback

from PySide6.QtCore import QThread, Signal

from chtec.domain import AppError


class Worker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.operation(self))
        except AppError as exc:
            self.error.emit(str(exc))
        except Exception:
            # Keep traceback in terminal for diagnosis, never log document contents or keys.
            traceback.print_exc()
            self.error.emit("Неожиданная ошибка. Подробности находятся в журнале терминала.")
