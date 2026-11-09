"""Visual style: navy, calm, high contrast, big targets for field laptops."""
import os

_RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources").replace("\\", "/")

ACCENT = "#1F3F77"        # navy (main colour)
ACCENT_DARK = "#152E5A"
ACCENT_SOFT = "#E7EDF7"
ACCENT_RGB = (31, 63, 119)
HIGHLIGHT = "#F5B841"     # amber from the logo pin
INK = "#1A2233"
MUTED = "#5D677A"
LINE = "#DADFE8"
BG = "#F3F5F9"
CARD = "#FFFFFF"
NAV_BG = "#0E1B33"
NAV_ACTIVE = "#1D3461"
NAV_INK = "#B4C2DA"
WARN = "#9A5B00"
WARN_SOFT = "#FFF4E0"
ERROR = "#B3261E"
ERROR_SOFT = "#FDECEA"
OK = "#2E7D32"
OK_SOFT = "#E6F2E7"
INFO = "#2B5C8A"
INFO_SOFT = "#EAF1F8"

# Windows 11 icon font, with the Windows 10 one as fallback (same code points)
ICON_FONT = '"Segoe Fluent Icons", "Segoe MDL2 Assets"'
ICONS = {"home": "", "drone": "", "georef": "", "flood": "", "jobs": "",
         "settings": "", "heart": "", "info": "", "help": "", "zoom_in": "",
         "zoom_out": "", "fit": "", "folder": "", "search": "", "open": ""}

LEVEL_COLORS = {"error": (ERROR, ERROR_SOFT), "warning": (WARN, WARN_SOFT), "info": (INFO, INFO_SOFT),
                "ok": (OK, OK_SOFT)}

STYLESHEET = f"""
* {{ font-family: "Segoe UI", "Noto Sans", "Helvetica Neue", Arial; color: {INK}; font-size: 13px; }}
QMainWindow, QWidget#Page, QWidget#Content, QDialog, QMessageBox {{ background: {BG}; }}
QLabel#H1 {{ font-size: 21px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#Section {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#Muted, QLabel#Hint {{ color: {MUTED}; font-size: 12px; }}
QLabel#Error {{ color: {ERROR}; font-size: 12px; }}
QLabel#Warn {{ color: {WARN}; font-size: 12px; }}
QLabel#Good {{ color: {OK}; font-size: 12px; }}
QLabel#Unit {{ color: {MUTED}; font-size: 15px; font-weight: 600; }}
QLabel#Big {{ font-size: 24px; font-weight: 600; }}
QLabel#Value {{ font-size: 19px; font-weight: 600; }}
QLabel#Stage {{ font-size: 20px; font-weight: 600; }}
QLabel#Icon {{ font-family: {ICON_FONT}; font-size: 16px; color: {MUTED}; }}
QFrame#Card {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; }}

/* ---- navigation rail ---- */
QFrame#Nav {{ background: {NAV_BG}; }}
QFrame#Nav QLabel {{ background: transparent; }}
QLabel#Brand {{ color: white; font-size: 17px; font-weight: 700; }}
QLabel#BrandSub {{ color: #8796B3; font-size: 11px; }}
QLabel#NavSection {{ color: #6D7C99; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel#NavFoot {{ color: #8190AD; font-size: 11px; }}
QPushButton#NavItem {{ background: transparent; border: none; border-radius: 6px; color: {NAV_INK};
                      text-align: left; padding: 9px 10px; font-size: 13px; }}
QPushButton#NavItem:hover {{ background: #16284A; color: white; }}
QPushButton#NavItem:checked {{ background: {NAV_ACTIVE}; color: white; font-weight: 600; }}
QPushButton#NavLink {{ background: transparent; border: none; color: #A9C1EE; text-align: left;
                      padding: 3px 0; font-size: 12px; }}
QPushButton#NavLink:hover {{ color: white; text-decoration: underline; }}

/* ---- header and status bar ---- */
QFrame#Header {{ background: white; border-bottom: 1px solid {LINE}; }}
QFrame#Header QLabel {{ background: transparent; }}
QLabel#Crumb {{ color: {MUTED}; font-size: 12px; }}
QFrame#Status {{ background: white; border-top: 1px solid {LINE}; }}
QLabel#StatusText {{ color: {MUTED}; font-size: 12px; background: transparent; }}

/* ---- wizard steps ---- */
QLabel#StepActive {{ color: {ACCENT}; font-weight: 600; font-size: 14px; }}
QLabel#StepDone {{ color: {MUTED}; font-size: 14px; }}
QLabel#StepTodo {{ color: #9AA3B5; font-size: 14px; }}

/* ---- buttons ---- */
QPushButton {{ background: #FFFFFF; border: 1px solid #C8CFDC; border-radius: 6px;
              padding: 7px 16px; font-size: 13px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: #FAFBFE; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #A7AFBF; background: #F0F2F6; }}
QPushButton#Primary {{ background: {ACCENT}; color: white; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #A5B3CC; color: #EEF2F8; }}
QPushButton#Danger {{ color: {ERROR}; }}
QPushButton#Hero {{ background: {ACCENT}; color: white; border: none; font-size: 16px;
                   font-weight: 600; padding: 14px 24px; border-radius: 8px; }}
QPushButton#Hero:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Link {{ border: none; background: transparent; color: {ACCENT}; padding: 4px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#Tool {{ font-family: {ICON_FONT}; font-size: 15px; padding: 5px 9px; }}

/* ---- inputs ---- */
QRadioButton, QCheckBox {{ font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #9AA5BB; border-radius: 4px; background: white; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{_RES}/check.png"); }}
QCheckBox::indicator:checked:disabled {{ background: #A5B3CC; border-color: #A5B3CC; }}
QCheckBox::indicator:disabled {{ background: #EEF1F5; border-color: #C8CFDC; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid #9AA5BB; border-radius: 8px; background: white; }}
QRadioButton::indicator:checked {{ border: 5px solid {ACCENT}; width: 6px; height: 6px; }}
QTableWidget::indicator {{ width: 15px; height: 15px; border: 1px solid #9AA5BB; border-radius: 4px; background: white; }}
QTableWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{_RES}/check.png"); }}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{ background: white; border: 1px solid #C8CFDC;
              border-radius: 5px; padding: 5px 7px; font-size: 13px; min-height: 20px; }}
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit[state="error"], QDoubleSpinBox[state="error"] {{ border: 1.5px solid {ERROR}; background: {ERROR_SOFT}; }}
QLineEdit[state="warn"], QDoubleSpinBox[state="warn"] {{ border: 1.5px solid {WARN}; background: {WARN_SOFT}; }}
QLineEdit[state="auto"] {{ color: {MUTED}; background: #F6F8FB; }}
QAbstractSpinBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{_RES}/chevron.png"); width: 12px; height: 12px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QSlider::groove:horizontal {{ height: 6px; background: #DCE2EC; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; border: 2px solid {ACCENT}; width: 14px; height: 14px;
                              margin: -6px 0; border-radius: 9px; }}
QProgressBar {{ border: none; background: #E3E8F0; border-radius: 6px; height: 12px; text-align: center;
               font-size: 11px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

/* ---- tables, tabs, misc ---- */
QTableWidget {{ background: white; border: 1px solid {LINE}; border-radius: 8px; gridline-color: #EDF0F5;
               font-size: 13px; selection-background-color: {ACCENT_SOFT}; selection-color: {INK}; }}
QTableWidget#Flat {{ border: none; border-radius: 0; }}
QTableWidget::item {{ padding-left: 6px; }}
QHeaderView::section {{ background: #F6F8FB; border: none; border-bottom: 1px solid {LINE};
                        padding: 7px; font-weight: 600; font-size: 11px; color: {MUTED}; }}
QPlainTextEdit {{ background: #161E2E; color: #D5DEEE; font-family: Consolas, "DejaVu Sans Mono", monospace;
                  font-size: 12px; border-radius: 8px; padding: 6px; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 8px; background: white; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 9px 16px; font-size: 13px; color: {MUTED};
               border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {INK}; }}
QFrame#ChoiceCard {{ background: white; border: 2px solid {LINE}; border-radius: 8px; }}
QFrame#ChoiceCard[selected="true"] {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C3CAD8; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:horizontal {{ background: #C3CAD8; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #9AA5BB; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QSplitter::handle {{ background: transparent; width: 8px; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 6px; font-size: 12px; }}
"""


def repolish(w):
    """Re-apply the stylesheet after changing a dynamic property (e.g. state=error)."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()
