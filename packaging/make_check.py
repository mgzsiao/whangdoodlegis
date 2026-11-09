"""Draws the white tick used inside checked checkboxes (fieldortho/resources/check.png)."""
import os
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen

RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fieldortho", "resources")

if __name__ == "__main__":
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    img = QImage(64, 64, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("white"), 9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPolyline([QPointF(14, 33), QPointF(27, 46), QPointF(51, 19)])
    p.end()
    img.save(os.path.join(RES, "check.png"))
    print("check.png written")
