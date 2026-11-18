"""Visual style (1.2): dark navy, calm, high contrast, big targets for field laptops.

Panels are dark navy with light text; light blue marks what can be clicked or is selected; amber marks what
needs attention. Things that stand for paper (reports, the "How things are computed" text, the QR code) stay
white with dark text. Every colour used by the GUI is defined here."""
import os

_RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources").replace("\\", "/")

ACCENT = "#8FB1EA"        # light navy blue: links, borders, selected items, primary buttons
ACCENT_DARK = "#B9CDF2"   # primary button hover (lighter on a dark theme)
ACCENT_SOFT = "#1C2C48"   # selected / highlighted backgrounds
ACCENT_FILL = "#3E6AB8"   # filled controls with a white mark (checked boxes, sliders, progress)
ACCENT_RGB = (143, 177, 234)
ON_ACCENT = "#0F1726"     # text on light-blue buttons
HIGHLIGHT = "#F5B841"     # amber from the logo pin
INK = "#E4E9F2"
MUTED = "#9AA6BB"
LINE = "#2A3850"
BG = "#0F1726"
CARD = "#172235"
SUBTLE = "#1B2840"        # table headers, quiet boxes
INPUT_BG = "#111B2C"
NAV_BG = "#0A1222"
NAV_ACTIVE = "#1D3461"
NAV_INK = "#9FB0CC"
WARN = "#F0B35C"
WARN_SOFT = "#3A2A10"
ERROR = "#F28B82"
ERROR_SOFT = "#3B1A1D"
OK = "#7CC783"
OK_SOFT = "#173022"
INFO = "#8FB1EA"
INFO_SOFT = "#1C2C48"
# drawings in the app (side views, cross-sections, photo footprints) and map backgrounds
PLOT_BG = "#121C2E"
PLOT_GRID = "#24324A"
MAP_BG = "#0B1220"
CUT = "#E8735F"           # ground to cut away
FILL = "#6FA8E8"          # ground to fill
GROUND = "#C9A877"
CUT_TEXT = "#F08A7A"
FILL_TEXT = "#8FBDF0"
# paper-like views keep a white page
PAPER = "#FFFFFF"
PAPER_INK = "#1A2233"

# Windows 11 icon font, with the Windows 10 one as fallback (same code points)
ICON_FONT = '"Segoe Fluent Icons", "Segoe MDL2 Assets"'
# (written as code points so editors cannot drop the private-use characters)
ICONS = {"home": chr(0xE80F), "drone": chr(0xE709), "georef": chr(0xE826), "flood": chr(0xEB42),
         "jobs": chr(0xE8B7), "settings": chr(0xE713), "heart": chr(0xEB51), "info": chr(0xE946),
         "help": chr(0xE9CE), "zoom_in": chr(0xE8A3), "zoom_out": chr(0xE71F), "fit": chr(0xE9A6),
         "folder": chr(0xE8B7), "search": chr(0xE721), "open": chr(0xE838)}

ICONS["convert"] = chr(0xE8AB)      # switch arrows
ICONS["measure"] = chr(0xEB3C)     # ruler and set square (same code point in both icon fonts)

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
QFrame#Card QLabel {{ background: transparent; }}

/* ---- navigation rail ---- */
QFrame#Nav {{ background: {NAV_BG}; }}
QFrame#Nav QLabel {{ background: transparent; }}
QLabel#Brand {{ color: white; font-size: 17px; font-weight: 700; }}
QLabel#BrandSub {{ color: #8796B3; font-size: 11px; }}
QLabel#NavSection {{ color: #6D7C99; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel#NavFoot {{ color: #8190AD; font-size: 11px; }}
QPushButton#NavSupport {{ background: transparent; border: none; color: #E58FA6; text-align: left;
    font-size: 11px; padding: 4px 0 0 0; }}
QPushButton#NavSupport:hover {{ color: #FFB3C6; text-decoration: underline; }}
QPushButton#NavItem {{ background: transparent; border: none; border-radius: 6px; color: {NAV_INK};
                      text-align: left; padding: 9px 10px; font-size: 13px; }}
QPushButton#NavItem:hover {{ background: #16284A; color: white; }}
QPushButton#NavItem:checked {{ background: {NAV_ACTIVE}; color: white; font-weight: 600; }}
QPushButton#NavLink {{ background: transparent; border: none; color: #A9C1EE; text-align: left;
                      padding: 3px 0; font-size: 12px; }}
QPushButton#NavLink:hover {{ color: white; text-decoration: underline; }}

/* ---- header and status bar ---- */
QFrame#Header {{ background: {CARD}; border-bottom: 1px solid {LINE}; }}
QFrame#Header QLabel {{ background: transparent; }}
QLabel#Crumb {{ color: {MUTED}; font-size: 12px; }}
QFrame#Status {{ background: {CARD}; border-top: 1px solid {LINE}; }}
QLabel#StatusText {{ color: {MUTED}; font-size: 12px; background: transparent; }}

/* ---- wizard steps ---- */
QLabel#StepActive {{ color: {ACCENT}; font-weight: 600; font-size: 14px; }}
QLabel#StepDone {{ color: {MUTED}; font-size: 14px; }}
QLabel#StepTodo {{ color: #6B7790; font-size: 14px; }}

/* ---- buttons ---- */
QPushButton {{ background: {CARD}; border: 1px solid #34466A; border-radius: 6px;
              padding: 7px 16px; font-size: 13px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: #1D2B45; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #5E6A80; background: #141E30; border-color: #26344D; }}
QPushButton#Primary {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Primary:disabled {{ background: #3A4A66; color: #8592A8; }}
QPushButton#Danger {{ color: {ERROR}; }}
QPushButton#Hero {{ background: {ACCENT}; color: {ON_ACCENT}; border: none; font-size: 16px;
                   font-weight: 600; padding: 14px 24px; border-radius: 8px; }}
QPushButton#Hero:hover {{ background: {ACCENT_DARK}; }}
QPushButton#Link {{ border: none; background: transparent; color: {ACCENT}; padding: 4px; text-align: left; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#Tool {{ font-family: {ICON_FONT}; font-size: 15px; padding: 5px 9px; }}

/* ---- inputs ---- */
QRadioButton, QCheckBox {{ font-size: 13px; spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #5A6B8C; border-radius: 4px; background: {INPUT_BG}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT_FILL}; border-color: {ACCENT}; image: url("{_RES}/check.png"); }}
QCheckBox::indicator:checked:disabled {{ background: #34466A; border-color: #34466A; }}
QCheckBox::indicator:disabled {{ background: #141E30; border-color: #2A3850; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid #5A6B8C; border-radius: 8px; background: {INPUT_BG}; }}
QRadioButton::indicator:checked {{ border: 5px solid {ACCENT}; width: 6px; height: 6px; background: {ON_ACCENT}; }}
QTableWidget::indicator {{ width: 15px; height: 15px; border: 1px solid #5A6B8C; border-radius: 4px; background: {INPUT_BG}; }}
QTableWidget::indicator:checked {{ background: {ACCENT_FILL}; border-color: {ACCENT}; image: url("{_RES}/check.png"); }}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox, QDateEdit {{ background: {INPUT_BG}; border: 1px solid #34466A;
              border-radius: 5px; padding: 5px 7px; font-size: 13px; min-height: 20px; selection-background-color: {ACCENT_FILL}; }}
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox:disabled, QLineEdit:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {{ color: #5E6A80; background: #141E30; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {LINE}; selection-background-color: {ACCENT_SOFT};
              selection-color: {INK}; outline: none; }}
QLineEdit[state="error"], QDoubleSpinBox[state="error"] {{ border: 1.5px solid {ERROR}; background: {ERROR_SOFT}; }}
QLineEdit[state="warn"], QDoubleSpinBox[state="warn"] {{ border: 1.5px solid {WARN}; background: {WARN_SOFT}; }}
QLineEdit[state="auto"] {{ color: {MUTED}; background: {SUBTLE}; }}
QAbstractSpinBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{_RES}/chevron_light.png"); width: 12px; height: 12px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 20px; border: none; background: transparent; }}
QSlider::groove:horizontal {{ height: 6px; background: #2A3850; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT_FILL}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {INK}; border: 2px solid {ACCENT}; width: 14px; height: 14px;
                              margin: -6px 0; border-radius: 9px; }}
QProgressBar {{ border: none; background: #24324A; border-radius: 6px; height: 12px; text-align: center;
               font-size: 11px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

/* ---- tables, lists, tabs, menus, misc ---- */
QTableWidget, QTableView, QListWidget, QTreeWidget {{ background: {CARD}; alternate-background-color: {SUBTLE};
               border: 1px solid {LINE}; border-radius: 8px; gridline-color: {LINE};
               font-size: 13px; selection-background-color: {ACCENT_SOFT}; selection-color: {INK}; }}
QTableWidget#Flat {{ border: none; border-radius: 0; }}
QTableWidget::item {{ padding-left: 6px; }}
QHeaderView {{ background: {SUBTLE}; }}
QHeaderView::section {{ background: {SUBTLE}; border: none; border-bottom: 1px solid {LINE};
                        padding: 7px; font-weight: 600; font-size: 11px; color: {MUTED}; }}
QTableCornerButton::section {{ background: {SUBTLE}; border: none; }}
QPlainTextEdit {{ background: #0B1220; color: #D5DEEE; font-family: Consolas, "DejaVu Sans Mono", monospace;
                  font-size: 12px; border: 1px solid {LINE}; border-radius: 8px; padding: 6px; }}
QTextEdit, QTextBrowser {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; }}
QTabWidget::pane {{ border: 1px solid {LINE}; border-radius: 8px; background: {CARD}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 9px 16px; font-size: 13px; color: {MUTED};
               border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {INK}; }}
QFrame#ChoiceCard {{ background: {CARD}; border: 2px solid {LINE}; border-radius: 8px; }}
QFrame#ChoiceCard[selected="true"] {{ border-color: {ACCENT}; background: {ACCENT_SOFT}; }}
QFrame#ChoiceCard QLabel {{ background: transparent; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 8px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QMenu {{ background: {CARD}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3A4A66; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:horizontal {{ background: #3A4A66; border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #56688A; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QSplitter::handle {{ background: transparent; width: 8px; }}
QToolTip {{ background: #24324A; color: {INK}; border: 1px solid #3A4A66; padding: 6px; font-size: 12px; }}
"""


def palette():
    """Dark Fusion palette so everything the stylesheet does not reach (native-looking parts of
    dialogs, spin box arrows, disabled text) matches, whatever the Windows light/dark setting."""
    from PySide6.QtGui import QColor, QPalette
    p = QPalette()
    roles = {QPalette.Window: BG, QPalette.WindowText: INK, QPalette.Base: INPUT_BG, QPalette.AlternateBase: SUBTLE,
             QPalette.ToolTipBase: "#24324A", QPalette.ToolTipText: INK, QPalette.Text: INK, QPalette.Button: CARD,
             QPalette.ButtonText: INK, QPalette.BrightText: "#FFFFFF", QPalette.Highlight: ACCENT_FILL,
             QPalette.HighlightedText: "#FFFFFF", QPalette.Link: ACCENT, QPalette.PlaceholderText: "#6B7790",
             QPalette.Light: "#2A3850", QPalette.Midlight: "#223049", QPalette.Mid: "#1B2840",
             QPalette.Dark: "#0A1222", QPalette.Shadow: "#000000"}
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#5E6A80"))
    return p


def repolish(w):
    """Re-apply the stylesheet after changing a dynamic property (e.g. state=error)."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()
