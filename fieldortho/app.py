"""FieldOrtho entry point (GUI)."""
from __future__ import annotations

import os
import sys


def main() -> int:
    # rasterio, pyogrio and pyproj each find their own bundled GDAL/PROJ data (also when
    # frozen). Do not set PROJ_DATA globally: their PROJ versions can differ.

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .gui.main_window import MainWindow
    from .gui.theme import STYLESHEET

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    icon = os.path.join(os.path.dirname(__file__), "resources", "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
