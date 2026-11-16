import signal

from PySide6.QtCore import QObject, QTimer


class ConsoleInterruptHandler(QObject):
    """Defer Ctrl+C to a safe Qt event instead of raising inside a result callback."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pending = False
        self.previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self.received)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.dispatch)
        self.timer.start(100)

    def received(self, signum, frame):
        # A Python signal may arrive between any two statements in a UI callback.
        # Do not mutate UI or raise an exception from that context.
        self.pending = True

    def dispatch(self):
        if not self.pending:
            return
        self.pending = False
        if self.window.worker is not None:
            self.window.cancel_task()
        else:
            self.window.close()

    def close(self):
        self.timer.stop()
        signal.signal(signal.SIGINT, self.previous)
