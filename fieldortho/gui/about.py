"""About box and the 'Support the Developer' window."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import APP_NAME, AUTHOR, DISCLAIMER_PARAGRAPHS, __version__
from . import theme
from .widgets import label

RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")


def logo(size: int) -> QLabel:
    lab = QLabel()
    pm = QPixmap(os.path.join(RES, "icon.png"))
    if not pm.isNull():
        lab.setPixmap(pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    lab.setFixedSize(size, size)
    lab.setStyleSheet("background: transparent;")
    return lab


class AboutDialog(QDialog):
    support = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 18)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addWidget(logo(60))
        col = QVBoxLayout()
        col.setSpacing(0)
        n = QLabel(APP_NAME)
        n.setStyleSheet("font-size: 22px; font-weight: 700;")
        col.addWidget(n)
        col.addWidget(label(f"Version {__version__} · Offline drone mapping, georeferencing and hazard "
                            "scenarios", "Muted"))
        col.addWidget(label(f"Developed by {AUTHOR}", "Muted"))
        top.addLayout(col, 1)
        lay.addLayout(top)
        lay.addSpacing(4)
        lay.addWidget(label("DISCLAIMER", "Section"))
        p1, p2, p3 = DISCLAIMER_PARAGRAPHS
        p2 = p2.replace("user-run simulations", "<b>user-run simulations</b>")
        t = QLabel("".join(f"<p style='margin-bottom:6px'>{x}</p>" for x in (p1, p2, p3)))
        t.setWordWrap(True)
        t.setTextFormat(Qt.RichText)
        t.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(t)
        lay.addWidget(label("Processing engine: OpenDroneMap (AGPL v3). Built with GDAL, PROJ and Qt. Licences: "
                            "LICENSE-NOTICE.txt in the program folder.", "Muted", wrap=True))
        lay.addSpacing(6)
        row = QHBoxLayout()
        s = QPushButton("♥  Support the Developer")
        s.setObjectName("Link")
        s.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        s.setCursor(Qt.PointingHandCursor)
        s.clicked.connect(self._support)
        row.addWidget(s)
        row.addStretch(1)
        c = QPushButton("Close")
        c.setObjectName("Primary")
        c.clicked.connect(self.accept)
        row.addWidget(c)
        lay.addLayout(row)

    def _support(self):
        SupportDialog(self).exec()


class SupportDialog(QDialog):
    """Only the QR code and a thank-you: no name, number or account details are shown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Support the Developer")
        self.setFixedWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 20, 26, 18)
        lay.setSpacing(10)
        h = QLabel(f"Thank you for using {APP_NAME}!")
        h.setStyleSheet("font-size: 16px; font-weight: 600;")
        h.setAlignment(Qt.AlignCenter)
        lay.addWidget(h)
        sub = label("It is free to use. If it helps your work, you can support its development with a "
                    "voluntary donation. Thank you!", "Muted", wrap=True)
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)
        q = QLabel()
        pm = QPixmap(os.path.join(RES, "support_qr.png"))
        if not pm.isNull():
            q.setPixmap(pm.scaled(320, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        q.setAlignment(Qt.AlignCenter)
        q.setStyleSheet("background: white; border-radius: 8px;")
        lay.addWidget(q)
        i = label("Scan with any bank or e-wallet app that supports InstaPay.\nTransfer fees may apply.",
                  "Muted", wrap=True)
        i.setAlignment(Qt.AlignCenter)
        lay.addWidget(i)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Close")
        c.setObjectName("Primary")
        c.clicked.connect(self.accept)
        row.addWidget(c)
        lay.addLayout(row)
