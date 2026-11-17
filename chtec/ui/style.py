"""Semantic themes in Qt logical units; OS DPI is handled by Qt."""


def stylesheet(theme="light", scale=100):
    dark = theme == "dark"
    bg, panel, border = (
        ("#181B20", "#22262D", "#383F49") if dark else ("#F7F8FA", "#FFFFFF", "#DDE1E7")
    )
    text, muted, accent = (
        ("#ECEFF4", "#A2ADBB", "#86B5DE") if dark else ("#20242B", "#657080", "#315D82")
    )
    hover, selected = ("#303843", "#34495D") if dark else ("#EDF2F7", "#DCE9F5")
    s = scale / 100
    pad = round(5 * s)
    return f"""
    QMainWindow, QDialog, QWidget#workspace {{ background:{bg}; color:{text}; }}
    QWidget {{ font-family:'Segoe UI'; font-size:{10 * s}pt; color:{text}; }}
    QMenuBar, QMenu {{ font-family:'Segoe UI'; font-size:{10 * s}pt; }}
    QMenuBar::item {{ font: {10 * s}pt 'Segoe UI'; padding:{pad}px {round(9 * s)}px; }}
    QMenu::item {{ font: {10 * s}pt 'Segoe UI'; padding:{pad}px {round(12 * s)}px; }}
    QLabel#brand {{ font-weight:600; font-size:{12 * s}pt; }}
    QLabel#subtitle, QLabel#section {{ color:{muted}; }}
    QLabel#section {{ font-weight:600; font-size:{9 * s}pt; }}
    QPushButton, QToolButton {{ background:{panel}; border:1px solid {border};
        border-radius:5px; padding:{pad}px {round(9 * s)}px; }}
    QPushButton:hover, QToolButton:hover {{ background:{hover}; border-color:{accent}; }}
    QPushButton:checked, QToolButton:checked {{ background:{selected}; border-color:{accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ color:{muted}; background:{bg}; }}
    QPushButton#primary, QToolButton#primary {{ background:{accent};
        color:{"#15212D" if dark else "#FFFFFF"}; border-color:{accent}; }}
    QPushButton:focus, QToolButton:focus, QLineEdit:focus,
    QPlainTextEdit:focus {{ border:1px solid {accent}; }}
    QPlainTextEdit, QTextEdit, QTextBrowser, QListWidget, QLineEdit, QComboBox,
    QSpinBox, QDoubleSpinBox {{ background:{panel}; border:1px solid {border};
        border-radius:5px; selection-background-color:{selected}; selection-color:{text}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ padding:{pad}px; }}
    QPlainTextEdit {{ padding:{round(9 * s)}px; }}
    QListWidget {{ outline:none; padding:3px; }}
    QListWidget::item {{ padding:{pad}px; margin-bottom:3px; border-radius:4px; }}
    QListWidget::item:selected {{ background:{selected}; color:{text}; }}
    QScrollArea, QGraphicsView {{ background:{bg}; border:1px solid {border}; }}
    QSplitter::handle {{ background:{border}; width:5px; height:5px; }}
    QSplitter::handle:hover {{ background:{accent}; }}
    QMenuBar {{ background:{bg}; padding:2px; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background:{selected}; }}
    QMenu {{ background:{panel}; border:1px solid {border}; padding:4px; }}
    QMenu::item {{ padding:{pad}px {round(20 * s)}px; }}
    QMenu::item:disabled {{ color:{muted}; }}
    QMenu::separator {{ height:1px; background:{border}; margin:4px; }}
    QTabWidget::pane {{ border:0; }}
    QTabBar::tab {{ background:{bg}; padding:{pad}px {round(12 * s)}px; }}
    QTabBar::tab:selected {{ background:{panel}; border-bottom:2px solid {accent}; }}
    QStatusBar {{ color:{muted}; padding:2px 8px; }}
    QProgressBar {{ border:0; background:{border}; height:5px; }}
    QProgressBar::chunk {{ background:{accent}; }}
    QToolTip {{ background:{panel}; color:{text}; border:1px solid {border}; padding:6px; }}
    """


STYLE = stylesheet()
