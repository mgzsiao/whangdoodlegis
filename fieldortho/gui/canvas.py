"""Zoomable map canvas with overlays, used by the georeferencer and the models."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGraphicsItemGroup, QGraphicsScene, QGraphicsView


def qimage_from(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        return QImage(arr.data, w, h, w, QImage.Format_Grayscale8).copy()
    if arr.shape[2] == 3:
        return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QImage(arr.data, w, h, 4 * w, QImage.Format_RGBA8888).copy()


class MapCanvas(QGraphicsView):
    clicked = Signal(float, float)          # scene (= image pixel) coordinates
    double_clicked = Signal(float, float)
    moved = Signal(float, float)

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
        self.base = None
        self.overlay = None
        self.vectors = None
        self._press = None
        self._fitted = False
        self.pick_cursor = False

    # ---------------------------------------------------------------- content
    def set_base(self, img, scale: float = 1.0):
        """img: numpy array or QImage. scale: scene units per image pixel."""
        self.scene().clear()
        self.overlay = None
        self.vectors = None
        qi = qimage_from(img) if isinstance(img, np.ndarray) else img
        self.base = self.scene().addPixmap(QPixmap.fromImage(qi))
        self.base.setTransformationMode(Qt.SmoothTransformation)
        self.base.setScale(scale)
        self.base.setZValue(0)
        self.scene().setSceneRect(self.base.sceneBoundingRect())
        self._fitted = False
        self.fit()

    def set_overlay(self, rgba: np.ndarray | None, scale: float = 1.0, smooth: bool = False):
        if self.overlay is not None:
            self.scene().removeItem(self.overlay)
            self.overlay = None
        if rgba is None:
            return
        self.overlay = self.scene().addPixmap(QPixmap.fromImage(qimage_from(rgba)))
        self.overlay.setScale(scale)
        self.overlay.setTransformationMode(Qt.SmoothTransformation if smooth else Qt.FastTransformation)
        self.overlay.setZValue(1)

    def clear_vectors(self):
        if self.vectors is not None:
            self.scene().removeItem(self.vectors)
        self.vectors = QGraphicsItemGroup()
        self.vectors.setZValue(2)
        self.scene().addItem(self.vectors)

    def _cos_pen(self, color, width=2.0, style=Qt.SolidLine):
        pen = QPen(QColor(*color) if isinstance(color, tuple) else QColor(color), width)
        pen.setCosmetic(True)
        pen.setStyle(style)
        return pen

    def add_polyline(self, pts, color="#00B5E2", width=1.5, closed=False, style=Qt.SolidLine, fill=None):
        if self.vectors is None:
            self.clear_vectors()
        if len(pts) < 2:
            return
        path = QPainterPath(QPointF(*pts[0]))
        for p in pts[1:]:
            path.lineTo(QPointF(*p))
        if closed:
            path.closeSubpath()
        it = self.scene().addPath(path, self._cos_pen(color, width, style),
                                  QBrush(QColor(*fill)) if fill else QBrush(Qt.NoBrush))
        self.vectors.addToGroup(it)

    def add_marker(self, x, y, label="", color="#D32F2F", size=7.0):
        """Screen-size-constant marker (circle + cross) with a label."""
        if self.vectors is None:
            self.clear_vectors()
        from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsSimpleTextItem, QGraphicsLineItem
        pen = self._cos_pen(color, 2)
        e = QGraphicsEllipseItem(-size, -size, 2 * size, 2 * size)
        e.setPen(pen)
        e.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations)
        e.setPos(x, y)
        self.vectors.addToGroup(e)
        for dx, dy in ((1, 0), (0, 1)):
            ln = QGraphicsLineItem(-size * 1.6 * dx, -size * 1.6 * dy, size * 1.6 * dx, size * 1.6 * dy)
            ln.setPen(pen)
            ln.setFlag(QGraphicsLineItem.ItemIgnoresTransformations)
            ln.setPos(x, y)
            self.vectors.addToGroup(ln)
        if label:
            t = QGraphicsSimpleTextItem(label)
            t.setBrush(QBrush(QColor(color)))
            f = QFont()
            f.setPointSize(9)
            f.setBold(True)
            t.setFont(f)
            t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            t.setPos(x, y)
            t.setTransform(t.transform().translate(size + 3, -size - 14))
            self.vectors.addToGroup(t)

    # ---------------------------------------------------------------- view
    def fit(self):
        if self.base is not None:
            self.fitInView(self.base.sceneBoundingRect(), Qt.KeepAspectRatio)
            self._fitted = True

    def zoom(self, f: float):
        self._fitted = False
        self.scale(f, f)

    def zoom_to(self, x, y, scale_px: float = 4.0):
        self.resetTransform()
        self.scale(scale_px, scale_px)
        self.centerOn(QPointF(x, y))
        self._fitted = False

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fitted:
            self.fit()

    def wheelEvent(self, e):
        f = 1.25 if e.angleDelta().y() > 0 else 0.8
        self._fitted = False
        self.scale(f, f)

    def mousePressEvent(self, e):
        self._press = e.position()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self._press is not None and (e.position() - self._press).manhattanLength() < 4 \
                and e.button() == Qt.LeftButton:
            p = self.mapToScene(e.position().toPoint())
            self.clicked.emit(p.x(), p.y())
        self._press = None
        if self.pick_cursor:
            self.viewport().setCursor(Qt.CrossCursor)

    def mouseDoubleClickEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        self.double_clicked.emit(p.x(), p.y())

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        p = self.mapToScene(e.position().toPoint())
        self.moved.emit(p.x(), p.y())

    def set_pick_cursor(self, on: bool):
        self.pick_cursor = on
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)


def map_card(canvas: MapCanvas, extra=()):
    """The map inside a card, with zoom in / zoom out / whole map buttons above it."""
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout
    from . import theme
    card = QFrame()
    card.setObjectName("Card")
    v = QVBoxLayout(card)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    bar = QHBoxLayout()
    bar.setContentsMargins(10, 6, 10, 6)
    bar.setSpacing(4)
    for icon, tip, fn in (("zoom_in", "Zoom in", lambda: canvas.zoom(1.4)),
                          ("zoom_out", "Zoom out", lambda: canvas.zoom(1 / 1.4)),
                          ("fit", "Show the whole map", canvas.fit)):
        b = QPushButton(theme.ICONS[icon])
        b.setObjectName("Tool")
        b.setToolTip(tip)
        b.clicked.connect(fn)
        bar.addWidget(b)
    bar.addSpacing(8)
    for w in extra:
        if isinstance(w, int):
            bar.addSpacing(w)
        else:
            bar.addWidget(w)
    bar.addStretch(1)
    v.addLayout(bar)
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.LINE};")
    v.addWidget(line)
    v.addWidget(canvas, 1)
    return card
