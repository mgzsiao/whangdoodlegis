"""Reusable widgets: photo map, zoomable raster viewer, cards."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPixmap, QFont, QPainterPath, QImage
from PySide6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QLabel, QVBoxLayout, QWidget, QSizePolicy)

from . import theme


def card(parent=None, name="Card") -> QFrame:
    f = QFrame(parent)
    f.setObjectName(name)
    return f


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "-", sub: str = ""):
        super().__init__()
        self.setObjectName("Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.t = label(title, "Muted")
        self.v = label(value, "H2")
        self.s = label(sub, "Muted", wrap=True)
        lay.addWidget(self.t)
        lay.addWidget(self.v)
        lay.addWidget(self.s)
        self.s.setVisible(bool(sub))

    def set(self, value: str, sub: str = ""):
        self.v.setText(value)
        self.s.setText(sub)
        self.s.setVisible(bool(sub))


class IssueBox(QFrame):
    """A coloured notice: level = error / warning / info / ok."""

    def __init__(self, level: str, title: str, detail: str = ""):
        super().__init__()
        fg, bg = theme.LEVEL_COLORS.get(level, theme.LEVEL_COLORS["info"])
        self.setObjectName("Issue")
        self.setStyleSheet(f"QFrame#Issue {{ background: {bg}; border-left: 4px solid {fg}; border-radius: 6px; }}"
                           f"QLabel {{ background: transparent; border: none; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        icon = {"error": "⛔", "warning": "⚠", "info": "ℹ", "ok": "✔"}.get(level, "")
        t = QLabel(f"{icon}  {title}")
        t.setStyleSheet(f"font-weight: 600; color: {fg}; font-size: 14px;")
        t.setWordWrap(True)
        lay.addWidget(t)
        if detail:
            d = QLabel(detail)
            d.setWordWrap(True)
            d.setStyleSheet("font-size: 13px;")
            lay.addWidget(d)


# ----------------------------------------------------------------------------
# Photo position map (offline: no basemap tiles needed)
# ----------------------------------------------------------------------------

class PhotoMap(QWidget):
    """Draws photo positions, the flight line and a scale bar in metres."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(360, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.points: list[tuple[float, float, str]] = []   # x, y, state(ok/blurry/excluded)
        self.footprint_r = 0.0
        self.highlight: tuple[float, float] | None = None
        self.extra: list[tuple[float, float, str]] = []    # GCPs: x, y, name

    def set_points(self, pts, footprint_r: float = 0.0):
        self.points = pts
        self.footprint_r = footprint_r
        self.update()

    def _frame(self):
        xs = [p[0] for p in self.points] + [e[0] for e in self.extra]
        ys = [p[1] for p in self.points] + [e[1] for e in self.extra]
        pad = max(self.footprint_r, 10.0)
        x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
        w, h = self.width() - 40, self.height() - 60
        scale = min(w / max(x1 - x0, 1), h / max(y1 - y0, 1))
        ox = 20 + (w - (x1 - x0) * scale) / 2
        oy = 20 + (h - (y1 - y0) * scale) / 2

        def to_px(x, y):
            return QPointF(ox + (x - x0) * scale, oy + (y1 - y) * scale)
        return to_px, scale

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#EEF2F0"))
        if not self.points:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Photo positions will appear here")
            return
        to_px, scale = self._frame()
        # footprints (coverage feel)
        if self.footprint_r > 0:
            r = self.footprint_r * scale
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(31, 111, 92, 18))
            for x, y, st in self.points:
                if st != "excluded":
                    p.drawEllipse(to_px(x, y), r, r)
        # flight line
        path = QPainterPath()
        for i, (x, y, _s) in enumerate(self.points):
            (path.moveTo if i == 0 else path.lineTo)(to_px(x, y))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(31, 111, 92, 90), 1.2))
        p.drawPath(path)
        colors = {"ok": QColor(theme.ACCENT), "blurry": QColor(theme.WARN), "excluded": QColor("#B9C2BE")}
        for x, y, st in self.points:
            p.setPen(QPen(QColor("white"), 1))
            p.setBrush(colors.get(st, colors["ok"]))
            p.drawEllipse(to_px(x, y), 4.5, 4.5)
        for x, y, name in self.extra:
            c = to_px(x, y)
            p.setPen(QPen(QColor(theme.ERROR), 2))
            p.drawLine(c + QPointF(-7, 0), c + QPointF(7, 0))
            p.drawLine(c + QPointF(0, -7), c + QPointF(0, 7))
            p.drawText(c + QPointF(8, -6), name)
        # scale bar
        target = (self.width() * 0.25) / scale
        nice = 10 ** math.floor(math.log10(max(target, 1)))
        for m in (5, 2, 1):
            if nice * m <= target:
                nice *= m
                break
        bar = nice * scale
        y = self.height() - 22
        p.setPen(QPen(QColor(theme.INK), 2))
        p.drawLine(QPointF(20, y), QPointF(20 + bar, y))
        p.drawLine(QPointF(20, y - 4), QPointF(20, y + 4))
        p.drawLine(QPointF(20 + bar, y - 4), QPointF(20 + bar, y + 4))
        p.setFont(QFont(p.font().family(), 9))
        p.drawText(QPointF(26 + bar, y + 4), f"{nice:g} m" if nice < 1000 else f"{nice / 1000:g} km")
        # north arrow
        nx, ny = self.width() - 26, 30
        p.setBrush(QColor(theme.INK))
        p.setPen(Qt.NoPen)
        tri = QPainterPath()
        tri.moveTo(nx, ny - 14)
        tri.lineTo(nx - 6, ny + 4)
        tri.lineTo(nx + 6, ny + 4)
        tri.closeSubpath()
        p.drawPath(tri)
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(nx - 10, ny + 4, 20, 16), Qt.AlignCenter, "N")
        # legend
        p.setFont(QFont(p.font().family(), 9))
        lx = 20
        for st, txt in (("ok", "photo"), ("blurry", "blurry"), ("excluded", "left out")):
            if st == "ok" or any(s == st for *_xy, s in self.points):
                p.setPen(Qt.NoPen)
                p.setBrush(colors[st])
                p.drawEllipse(QPointF(lx + 5, 14), 4.5, 4.5)
                p.setPen(QColor(theme.MUTED))
                p.drawText(QPointF(lx + 14, 18), txt)
                lx += 70


# ----------------------------------------------------------------------------
# Raster viewer with zoom / pan and coordinate readout
# ----------------------------------------------------------------------------

class RasterView(QGraphicsView):
    hovered = Signal(str)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#E9EDEB")))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.item: QGraphicsPixmapItem | None = None
        self.geo = None        # affine (a, b, c, d, e, f)
        self.values = None     # numpy array for value readout (e.g. elevation)
        self.unit = ""
        self._fitted = False

    def load(self, png_path: str, geo=None, values=None, unit: str = ""):
        self.scene().clear()
        pm = QPixmap(png_path)
        self.item = self.scene().addPixmap(pm)
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene().setSceneRect(QRectF(pm.rect()))
        self.geo, self.values, self.unit = geo, values, unit
        self._fitted = False
        self.fit()

    def fit(self):
        if self.item:
            self.fitInView(self.item, Qt.KeepAspectRatio)
            self._fitted = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fitted:
            self.fit()

    def wheelEvent(self, e):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self._fitted = False
        self.scale(factor, factor)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        if not self.item or not self.geo:
            return
        pt = self.mapToScene(e.position().toPoint())
        col, row = pt.x(), pt.y()
        w, h = self.item.pixmap().width(), self.item.pixmap().height()
        if not (0 <= col < w and 0 <= row < h):
            self.hovered.emit("")
            return
        a, b, c, d, ee, f = self.geo
        x = c + col * a + row * b
        y = f + col * d + row * ee
        text = f"X {x:,.2f}   Y {y:,.2f}"
        if self.values is not None:
            vh, vw = self.values.shape
            vr, vc = int(row * vh / h), int(col * vw / w)
            if 0 <= vr < vh and 0 <= vc < vw:
                v = self.values[vr, vc]
                if v == v and v > -9000:
                    text += f"   Elevation {v:,.2f} {self.unit}"
        self.hovered.emit(text)


def numpy_to_qimage(rgba) -> QImage:
    h, w = rgba.shape[:2]
    return QImage(rgba.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()
