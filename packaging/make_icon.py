"""Draws the WhangDoodleGIS logo: navy tile, white terrain contour lines, amber location pin.

    python packaging/make_icon.py        -> fieldortho/resources/icon.png (512 px) and icon.ico
"""
import math
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(os.path.dirname(HERE), "fieldortho", "resources")


def contour(cx, cy, r, k, phase):
    """A closed, gently irregular ring (a terrain contour)."""
    path = QPainterPath()
    n = 120
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        rr = r * (1 + 0.10 * math.sin(2 * a + phase) + 0.06 * math.sin(3 * a + 2 * phase) * k)
        p = QPointF(cx + rr * math.cos(a) * 1.12, cy + rr * math.sin(a) * 0.9)
        path.moveTo(p) if i == 0 else path.lineTo(p)
    path.closeSubpath()
    return path


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 512.0
    tile = QRectF(16 * s, 16 * s, 480 * s, 480 * s)
    g = QLinearGradient(tile.topLeft(), tile.bottomRight())
    g.setColorAt(0, QColor("#24467F"))
    g.setColorAt(1, QColor("#0E1F3D"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(tile, 104 * s, 104 * s)
    # contours around a hill centred lower-left of the pin
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(tile, 104 * s, 104 * s)
    p.setClipPath(clip)
    cx, cy = 236 * s, 286 * s
    small = size <= 32
    rings = (250, 140) if small else (300, 228, 160, 96, 40)
    for i, r in enumerate(rings):
        alpha = (150, 255)[i] if small else (70, 110, 160, 210, 255)[i]
        p.setPen(QPen(QColor(255, 255, 255, alpha), max(1.0, (40 if small else 14 if size >= 64 else 24) * s)))
        p.setBrush(Qt.NoBrush)
        p.drawPath(contour(cx, cy, r * s, 1.0, 0.6 + i * 0.35))
    p.restore()
    # location pin (amber) at the summit, slightly up-right
    px, py, pr = (300 * s, 170 * s, 92 * s) if size <= 32 else (318 * s, 196 * s, 66 * s)
    pin = QPainterPath()
    pin.moveTo(px, py + pr * 2.15)
    pin.cubicTo(px - pr * 0.35, py + pr * 1.45, px - pr, py + pr * 0.85, px - pr, py)
    pin.arcTo(QRectF(px - pr, py - pr, 2 * pr, 2 * pr), 180, -180)
    pin.cubicTo(px + pr, py + pr * 0.85, px + pr * 0.35, py + pr * 1.45, px, py + pr * 2.15)
    pin.closeSubpath()
    p.setPen(QPen(QColor("#0E1F3D"), max(1.0, 10 * s)))
    p.setBrush(QColor("#F5B841"))
    p.drawPath(pin)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0E1F3D"))
    p.drawEllipse(QPointF(px, py), pr * 0.38, pr * 0.38)
    p.end()
    return img


def main():
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    big = draw(512)
    big.save(os.path.join(RES, "icon.png"))
    from PIL import Image
    tmp = os.path.join(RES, "_icon_tmp")
    os.makedirs(tmp, exist_ok=True)
    frames = []
    for n in (256, 128, 64, 48, 32, 24, 16):
        f = os.path.join(tmp, f"{n}.png")
        draw(n).save(f)          # drawn per size (thicker lines when small) instead of scaled down
        frames.append(Image.open(f).convert("RGBA"))
    frames[0].save(os.path.join(RES, "icon.ico"), sizes=[im.size for im in frames], append_images=frames[1:])
    for im in frames:
        im.close()
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    print("icon written to", RES)


if __name__ == "__main__":
    main()
