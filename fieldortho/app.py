"""WhangDoodleGIS entry point (GUI)."""
from __future__ import annotations

import os
import sys


def _set_gis_data():
    """Frozen conda-forge build: GDAL and PROJ are shared by rasterio, pyogrio and pyproj, and
    their data is bundled in share/. (pip-wheel builds carry their own data; nothing to do.)"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        for var, sub in (("GDAL_DATA", ("share", "gdal")), ("PROJ_DATA", ("share", "proj"))):
            p = os.path.join(base, *sub)
            if os.path.isdir(p):
                os.environ[var] = p
                if var == "PROJ_DATA":
                    os.environ["PROJ_LIB"] = p


def main() -> int:
    _set_gis_data()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .gui.main_window import MainWindow
    from .gui.theme import STYLESHEET, palette

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    # The design is dark navy (1.2) whatever the Windows light/dark setting: tell Qt so its own
    # parts (title bars where supported, message boxes) match, and give Fusion a dark palette
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):          # Qt 6.8+
        hints.setColorScheme(Qt.ColorScheme.Dark)
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(STYLESHEET)
    icon = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.showMaximized()                             # opens filling the screen; F11 = full screen
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
