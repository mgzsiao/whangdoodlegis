"""Visual style: calm, high-contrast, big touch targets for field laptops."""
import os

_RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources").replace("\\", "/")

ACCENT = "#1F6F5C"        # deep field green
ACCENT_DARK = "#17574A"
ACCENT_SOFT = "#E3F0EC"
INK = "#1D2421"
MUTED = "#5E6B66"
LINE = "#D9DFDC"
BG = "#F5F7F6"
CARD = "#FFFFFF"
WARN = "#B26B00"
WARN_SOFT = "#FFF4E0"
ERROR = "#B3261E"
ERROR_SOFT = "#FDECEA"
OK = "#2E7D32"
INFO_SOFT = "#EAF1F8"

LEVEL_COLORS = {"error": (ERROR, ERROR_SOFT), "warning": (WARN, WARN_SOFT), "info": ("#2B5C8A", INFO_SOFT),
                "ok": (OK, ACCENT_SOFT)}

STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Noto Sans", "Helvetica Neue", Arial; color: {INK}; }}
QMainWindow, QWidget#Page, QDialog, QMessageBox {{ background: {BG}; }}
QLabel#H1 {{ font-size: 24px; font-weight: 600; }}
QLabel#H2 {{ font-size: 17px; font-weight: 600; }}
QLabel#Muted {{ color: {MUTED}; font-size: 13px; }}
QLabel#Big {{ font-size: 28px; font-weight: 600; }}
QLabel#Stage {{ font-size: 20px; font-weight: 600; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#Sidebar {{ background: #FFFFFF; border-right: 1px solid {LINE}; }}
QLabel#StepActive {{ color: {ACCENT}; font-weight: 600; font-size: 14px; }}
QLabel#StepDone {{ color: {MUTED}; font-size: 14px; }}
QLabel#StepTodo {{ color: #9AA5A0; font-size: 14px; }}
QPushButton {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 8px;
              padding: 9px 18px; font-size: 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #A9B2AE; background: #F0F2F1; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #9DBDB4; color: #EEF4F2; }}
QPushButton#Danger {{ color: {ERROR}; }}
QPushButton#Hero {{ background: {ACCENT}; color: white; border: none; font-size: 17px;
                   font-weight: 600; padding: 18px 28px; border-radius: 10px; }}
QPushButton#Hero:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Link {{ border: none; background: transparent; color: {ACCENT}; padding: 4px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QRadioButton, QCheckBox {{ font-size: 14px; spacing: 8px; }}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{ background: white; border: 1px solid {LINE};
              border-radius: 6px; padding: 6px 8px; font-size: 14px; min-height: 22px; }}
QComboBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QAbstractSpinBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{_RES}/chevron.png"); width: 12px; height: 12px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QProgressBar {{ border: none; background: #E4E9E7; border-radius: 7px; height: 14px; text-align: center;
               font-size: 11px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 7px; }}
QTableWidget {{ background: white; border: 1px solid {LINE}; border-radius: 8px; gridline-color: {LINE};
               font-size: 13px; selection-background-color: {ACCENT_SOFT}; selection-color: {INK}; }}
QHeaderView::section {{ background: #F0F3F2; border: none; border-bottom: 1px solid {LINE};
                        padding: 6px; font-weight: 600; font-size: 12px; color: {MUTED}; }}
QPlainTextEdit {{ background: #1E2422; color: #D8E2DE; font-family: Consolas, "DejaVu Sans Mono", monospace;
                  font-size: 12px; border-radius: 8px; padding: 6px; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 8px; background: white; top: -1px; }}
QTabBar::tab {{ padding: 8px 16px; font-size: 13px; border: 1px solid transparent; }}
QTabBar::tab:selected {{ background: white; border: 1px solid {LINE}; border-bottom-color: white;
                         border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: 600; }}
QFrame#ChoiceCard {{ background: white; border: 2px solid {LINE}; border-radius: 10px; }}
QFrame#ChoiceCard[selected="true"] {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QScrollArea {{ border: none; background: transparent; }}
"""
