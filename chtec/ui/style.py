STYLE = """
QMainWindow, QDialog { background: #f5f4f0; color: #252e2a; }
QWidget { font-family: 'Segoe UI'; font-size: 10pt; }
QLabel#brand { font-size: 26pt; font-weight: 650; letter-spacing: -1px; }
QLabel#subtitle { color: #737a74; font-size: 10pt; }
QLabel#section { font-size: 10pt; font-weight: 600; color: #68726c; }
QPushButton { background: #fff; border: 1px solid #d8ded7; border-radius: 7px;
              padding: 9px 15px; color: #303b34; }
QPushButton:hover { background: #e8eee8; border-color: #a6b6a7; }
QPushButton:pressed { background: #dce8dd; }
QPushButton:checked { background: #dce8dd; border-color: #63886d; }
QPushButton:disabled { color: #a7ada7; background: #efefeb; }
QPushButton#primary { background: #325d48; color: white; border-color: #325d48; }
QPushButton#primary:hover { background: #254d39; }
QPushButton#primary:disabled { background: #b0bcb2; border-color: #b0bcb2; }
QPlainTextEdit, QTextEdit, QTextBrowser, QListWidget, QLineEdit, QComboBox, QSpinBox {
    background: #fff; border: 1px solid #dce1d9; border-radius: 7px;
    selection-background-color: #d7e6d7; selection-color: #20382a;
}
QLineEdit, QComboBox, QSpinBox { padding: 8px; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus { border-color: #63886d; }
QListWidget { outline: none; padding: 5px; }
QListWidget::item { padding: 12px 5px; margin-bottom: 4px; border-radius: 5px; }
QListWidget::item:selected { background: #e2ebdf; color: #274f38; }
QScrollArea { background: #e8e9e3; border: 1px solid #dce1d9; border-radius: 7px; }
QSplitter::handle { background: transparent; width: 12px; }
QStatusBar { color: #68726c; padding: 4px 16px; }
QProgressBar { border: none; background: #e1e7df; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #63886d; border-radius: 3px; }
QToolTip { background: #fff; color: #252e2a; border: 1px solid #b5c6b5; padding: 6px; }
"""
