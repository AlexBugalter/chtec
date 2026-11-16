import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from chtec import __version__
from chtec.backends.manager import BackendManager
from chtec.backends.profiles import default_profiles
from chtec.config import SettingsStore
from chtec.domain import AppError
from chtec.formats import default_registry
from chtec.llm import LocalSettings
from chtec.plugins import load_plugins
from chtec.ui.interrupts import ConsoleInterruptHandler
from chtec.ui.window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Чтец")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Chtec")
    registry = default_registry()
    errors = load_plugins(registry)
    # Portable configuration lives beside the working folder; can be overridden.
    config_path = Path(os.environ.get("CHTEC_CONFIG", ".chtec/settings.json"))
    store = SettingsStore(config_path)
    try:
        profiles = store.load_profiles()
    except AppError as exc:
        errors.append(str(exc))
        profiles = default_profiles(LocalSettings())
    manager = BackendManager()
    window = MainWindow(registry, profiles.active.settings, store, profiles, manager)
    window.show()
    if errors:
        QTimer.singleShot(
            0, lambda: QMessageBox.warning(window, "Проверьте настройки", "\n\n".join(errors))
        )
    paths = [path for path in sys.argv[1:] if Path(path).is_file()]
    if paths:
        QTimer.singleShot(0, lambda: window.load_paths(paths))
    interrupts = ConsoleInterruptHandler(window)
    try:
        return app.exec()
    finally:
        interrupts.close()
        try:
            manager.deactivate()
        except AppError as exc:
            # A retained late load has its own cleanup thread; keep its ownership.
            print(str(exc), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
