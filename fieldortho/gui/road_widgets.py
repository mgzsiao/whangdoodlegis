"""Small drawings for the road assistant: the road shape, a cross-section and the side view."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

CUT = QColor(196, 40, 32)
FILL = QColor(30, 90, 170)
GROUND = QColor(138, 106, 58)
NAVY = QColor(theme.ACCENT)


def soft(c: QColor, a=90) -> QColor:
    q = QColor(c)
    q.setAlpha(a)
    return q


class TemplatePreview(QWidget):
    """The road shape across, drawn from the answers (not to scale vertically)."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rd = None

    def set_design(self, rd):
        self.rd = rd
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#F6F8FB"))
        rd = self.rd
        if rd is None:
            return
        from ..core import road as rdm
        W, H = self.width(), self.height()
        half = rd.half
        ditch = (rd.ditch_d + rd.ditch_w + 1.0) if rd.ditch else 0.0
        side = 2.0
        span = 2 * (half + ditch + side)
        sx = (W - 30) / span
        vz = 22.0                               # pixels per metre of height (exaggerated)
        cx, cy = W / 2, H * 0.42

        def P(u, z):
            return QPointF(cx + u * sx, cy - z * vz)
        u = np.linspace(0, half, 40)
        top = rdm.template(u, 0.0, rd, False)
        pts = [P(-a, b) for a, b in zip(u[::-1], top[::-1])] + [P(a, b) for a, b in zip(u, top)]
        zh = top[-1]
        # pavement layer
        if rd.pavement_t > 0:
            bot = rdm.template(u, 0.0, rd, True)
            poly = QPolygonF(pts + [P(a, b) for a, b in zip(u[::-1], bot[::-1])] +
                             [P(-a, b) for a, b in zip(u, bot)])
            p.setBrush(QColor(170, 170, 170))
            p.setPen(Qt.NoPen)
            p.drawPolygon(poly)
        # left side: fill slope down, right side: cut with optional ditch
        p.setPen(QPen(NAVY, 2.5))
        path = QPainterPath(P(-half - 1.2 * rd.fill_ratio, zh - 1.2))
        path.lineTo(P(-half, zh))
        for q in pts:
            path.lineTo(q)
        if rd.ditch:
            path.lineTo(P(half + rd.ditch_d, zh - rd.ditch_d))
            path.lineTo(P(half + rd.ditch_d + rd.ditch_w, zh - rd.ditch_d))
            path.lineTo(P(half + rd.ditch_d + rd.ditch_w + 1.2 * rd.cut_ratio, zh - rd.ditch_d + 1.2))
        else:
            path.lineTo(P(half + 1.2 * rd.cut_ratio, zh + 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        f = QFont(self.font().family(), 8)
        p.setFont(f)
        p.setPen(QColor(theme.INK))
        w2 = rd.width / 2
        y = cy + 26
        p.drawLine(P(-w2, 0).x(), y, P(w2, 0).x(), y)
        p.drawText(QRectF(P(-w2, 0).x(), y + 2, w2 * 2 * sx, 14), Qt.AlignCenter, f"road {rd.width:g} m")
        if rd.shoulders:
            p.drawText(QRectF(P(w2, 0).x() + 4, y + 2, 120, 14), Qt.AlignLeft,
                       f"shoulder {rd.shoulder_w:g} m")
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(4, H - 34, W / 2, 14), Qt.AlignLeft, f"fill side {rd.fill_ratio:g} : 1")
        p.drawText(QRectF(W / 2, H - 34, W / 2 - 4, 14), Qt.AlignRight,
                   ("ditch + " if rd.ditch else "") + f"cut side {rd.cut_ratio:g} : 1")
        p.drawText(QRectF(4, H - 18, W - 8, 14), Qt.AlignCenter,
                   f"crown {rd.crown_pct:g}%" + (f" · pavement {rd.pavement_t:g} m (grey)" if rd.pavement_t > 0 else "")
                   + " · heights not to scale")


class SectionView(QWidget):
    """One cross-section: ground (brown), road and sides (navy), dig / fill shaded."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.st = None

    def set_station(self, st):
        self.st = st
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        st = self.st
        if st is None or st.offsets is None:
            return
        o, g, d = st.offsets, st.ground_xs, st.design_xs
        ok = np.isfinite(g)
        if not ok.any():
            return
        zs = np.concatenate([g[ok], d[np.isfinite(d)]])
        z0, z1 = float(zs.min()), float(zs.max())
        L, R, T, B = 44, 8, 8, 20
        W, H = self.width() - L - R, self.height() - T - B
        span = float(o[-1] - o[0]) or 1.0
        sx = W / span
        sz = max(sx, min(H * 0.85 / max(z1 - z0, 0.3), 5 * sx))
        zc = (z0 + z1) / 2

        def P(a, b):
            return QPointF(L + (a - o[0]) * sx, T + H / 2 - (b - zc) * sz)
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(theme.MUTED))
        for k in (-1, 0, 1):
            zz = zc + k * H / 3 / sz
            y = T + H / 2 - k * H / 3
            p.setPen(QPen(QColor("#EDF0F5"), 1))
            p.drawLine(QPointF(L, y), QPointF(L + W, y))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y - 7, L - 4, 14), Qt.AlignRight | Qt.AlignVCenter, f"{zz:.1f}")
        for oo in np.arange(np.ceil(o[0] / 5) * 5, o[-1] + 1e-9, 5):
            q = P(oo, zc)
            p.drawText(QRectF(q.x() - 15, T + H + 2, 30, 14), Qt.AlignCenter, f"{oo:g}")
        both = ok & np.isfinite(d)
        for i in range(len(o) - 1):
            if both[i] and both[i + 1]:
                c = CUT if g[i] > d[i] else FILL
                poly = QPolygonF([P(o[i], g[i]), P(o[i + 1], g[i + 1]), P(o[i + 1], d[i + 1]), P(o[i], d[i])])
                p.setPen(Qt.NoPen)
                p.setBrush(soft(c, 80))
                p.drawPolygon(poly)
        p.setBrush(Qt.NoBrush)
        for arr, col, w in ((g, GROUND, 2.0), (d, NAVY, 2.6)):
            path, started = QPainterPath(), False
            for a, b in zip(o, arr):
                if np.isfinite(b):
                    (path.lineTo if started else path.moveTo)(P(a, b))
                    started = True
                else:
                    started = False
            p.setPen(QPen(col, w))
            p.drawPath(path)
        p.setPen(QPen(QColor("#C8CFDC"), 1, Qt.DashLine))
        p.drawLine(P(0, z1 + 5), P(0, z0 - 5))


class RoadProfileView(QWidget):
    """Side view along the road; click to pick a station."""
    picked = Signal(int)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(190)
        self.r = None
        self.sel = 0
        self.mode = "profile"          # profile | mass

    def set_mode(self, m):
        self.mode = m
        self.update()

    def set_result(self, r):
        self.r = r
        self.update()

    def set_selected(self, i):
        self.sel = i
        self.update()

    def _frame(self):
        r = self.r
        d, g, z = r.prof_d, r.prof_g, r.prof_z
        ok = np.isfinite(g)
        allz = np.concatenate([g[ok], z])
        z0, z1 = float(allz.min()), float(allz.max())
        pad = max(0.3, (z1 - z0) * 0.1)
        return d, g, z, ok, z0 - pad, z1 + pad

    def _paint_mass(self, p):
        from ..core.road import station_label
        from .measure_page import nice_step
        st = self.r.stations
        sd = np.array([s.s for s in st])
        m = np.array([s.mass for s in st])
        lo, hi = min(0.0, float(m.min())), max(0.0, float(m.max()))
        pad = max(1.0, (hi - lo) * 0.1)
        lo, hi = lo - pad, hi + pad
        L, R, T, B = 64, 12, 22, 22
        W, H = self.width() - L - R, self.height() - T - B
        dm = float(sd[-1]) or 1.0

        def P(a, b):
            return QPointF(L + a / dm * W, T + (hi - b) / (hi - lo) * H)
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(L, 2, W, 16), Qt.AlignLeft,
                   "Earth balance along the road (m³): above zero = more dug than the fill needs so far")
        p.setPen(QColor(theme.MUTED))
        for v in (lo, 0.0, hi):
            y = P(0, v).y()
            p.drawText(QRectF(0, y - 7, L - 4, 14), Qt.AlignRight | Qt.AlignVCenter, f"{v:,.0f}")
        stp = nice_step(dm / 6)
        x = 0.0
        while x <= dm + 1e-9:
            q = P(x, lo)
            p.drawText(QRectF(q.x() - 30, T + H + 4, 60, 14), Qt.AlignCenter, station_label(x))
            x += stp
        p.setPen(QPen(QColor(theme.INK), 1))
        p.drawLine(P(0, 0), P(dm, 0))
        for a, b, mb, ma in zip(sd[:-1], sd[1:], m[1:], m[:-1]):
            c = CUT if mb >= 0 else FILL
            p.setPen(Qt.NoPen)
            p.setBrush(soft(c, 60))
            p.drawPolygon(QPolygonF([P(a, 0), P(a, ma), P(b, mb), P(b, 0)]))
        path = QPainterPath(P(sd[0], m[0]))
        for a, b in zip(sd[1:], m[1:]):
            path.lineTo(P(a, b))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(NAVY, 2.2))
        p.drawPath(path)
        if 0 <= self.sel < len(st):
            q = P(st[self.sel].s, lo)
            p.setPen(QPen(QColor(theme.INK), 1.5, Qt.DashLine))
            p.drawLine(QPointF(q.x(), T), QPointF(q.x(), T + H))

    def mousePressEvent(self, e):
        if self.r is None:
            return
        L, R = 54, 12
        frac = (e.position().x() - L) / max(1, self.width() - L - R)
        s = frac * self.r.length
        i = int(np.argmin([abs(st.s - s) for st in self.r.stations]))
        self.picked.emit(i)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        if self.r is None:
            return
        if self.mode == "mass":
            self._paint_mass(p)
            return
        d, g, z, ok, z0, z1 = self._frame()
        L, R, T, B = 54, 12, 22, 22
        W, H = self.width() - L - R, self.height() - T - B
        dm = float(d[-1]) or 1.0

        def P(a, b):
            return QPointF(L + a / dm * W, T + (z1 - b) / (z1 - z0) * H)
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(L, 2, W, 16), Qt.AlignLeft, "Side view along the road (click to pick a station)")
        p.setPen(QColor(theme.MUTED))
        for k in range(3):
            zz = z0 + (z1 - z0) * k / 2
            y = T + H - H * k / 2
            p.setPen(QPen(QColor("#EDF0F5"), 1))
            p.drawLine(QPointF(L, y), QPointF(L + W, y))
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(0, y - 7, L - 4, 14), Qt.AlignRight | Qt.AlignVCenter, f"{zz:.1f} m")
        from ..core.road import station_label
        from .measure_page import nice_step
        stp = nice_step(dm / 6)
        x = 0.0
        while x <= dm + 1e-9:
            q = P(x, z0)
            p.drawText(QRectF(q.x() - 30, T + H + 4, 60, 14), Qt.AlignCenter, station_label(x))
            x += stp
        for i in range(len(d) - 1):
            if ok[i] and ok[i + 1]:
                c = CUT if g[i] > z[i] else FILL
                p.setPen(Qt.NoPen)
                p.setBrush(soft(c, 70))
                p.drawPolygon(QPolygonF([P(d[i], g[i]), P(d[i + 1], g[i + 1]), P(d[i + 1], z[i + 1]),
                                         P(d[i], z[i])]))
        p.setBrush(Qt.NoBrush)
        path, started = QPainterPath(), False
        for a, b in zip(d, g):
            if np.isfinite(b):
                (path.lineTo if started else path.moveTo)(P(a, b))
                started = True
            else:
                started = False
        p.setPen(QPen(GROUND, 2))
        p.drawPath(path)
        path = QPainterPath(P(d[0], z[0]))
        for a, b in zip(d[1:], z[1:]):
            path.lineTo(P(a, b))
        p.setPen(QPen(NAVY, 2.4))
        p.drawPath(path)
        if 0 <= self.sel < len(self.r.stations):
            st = self.r.stations[self.sel]
            q = P(st.s, z0)
            p.setPen(QPen(QColor(theme.INK), 1.5, Qt.DashLine))
            p.drawLine(QPointF(q.x(), T), QPointF(q.x(), T + H))
