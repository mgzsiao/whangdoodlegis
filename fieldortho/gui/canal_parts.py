"""Guided canal steps (1.2): the lined drainage canal part by part, like the road steps, and its picture."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

PART_STEPS = ("c_shape", "c_lining", "c_fb", "c_bed", "c_banks")
START = dict(bw=0.5, depth=0.8, z=0.5, lining_t=0.10, n=0.015, freeboard=0.20, slope_pct=0.5, berm_w=0.5,
             cut_ratio=1.5, fill_ratio=2.0)
SOURCE = "Suggested starting values (agreed 18 November 2026), not a DPWH standard."

TITLES = {"c_shape": "Canal shape", "c_lining": "Concrete lining", "c_fb": "Freeboard", "c_bed": "Bed slope",
          "c_banks": "Beside the canal: berm, cut sides and banks", "c_flow": "How much water must it carry?"}
SHORT = {"c_shape": "Canal shape", "c_lining": "Lining", "c_fb": "Freeboard", "c_bed": "Bed slope",
         "c_banks": "Beside the canal", "c_flow": "Design flow"}
EXPLAIN = {
    "c_shape": "A trapezoid: a flat bed and sloping sides. The depth is from the bed to the top of the lining; the "
               "side slope is horizontal : vertical (0.5 : 1 = half a metre out for every metre up).",
    "c_lining": "The concrete on the bed and sides. Its smoothness (Manning's n) sets how fast the water flows: "
                "smoother concrete carries more water. n = 0.015 is Chow's value for float-finished concrete "
                "(trowel finish 0.013, unfinished 0.017).",
    "c_fb": "Spare height between the highest water level and the top of the lining, so waves, surges and "
            "debris do not spill over. The capacity check fills the canal only to the depth less the freeboard.",
    "c_bed": "The bed drops by the same amount every metre, so the water always flows; it never goes uphill. "
             "Draw the canal from its upstream (high) end to its downstream (low) end. At the start the top of the "
             "lining sits at the ground (or type the bed level). Where the ground drops faster than the bed, the "
             "canal comes out of the ground and gets earth banks; where the ground rises, the cut gets deeper.",
    "c_banks": "A flat strip beside the top of the lining (for walking and cleaning). Where the ground is higher "
               "than the canal top, an earth cut slope rises from it to the ground; where the ground is lower, it "
               "is the top of an earth bank whose sides go down to the ground.",
}


def warnings(key: str, cd) -> list[str]:
    out = []
    if key == "c_shape" and cd.depth <= 0:
        out.append("The canal needs a depth.")
    if key == "c_fb" and cd.freeboard >= cd.depth:
        out.append("The freeboard must be less than the depth.")
    if key == "c_bed" and cd.slope_pct < 0.2:
        out.append("A very flat bed: water may stand and silt may settle. 0.3% or more is better for drainage.")
    if key == "c_lining" and not (0.010 <= cd.n <= 0.020):
        out.append("Concrete is usually n 0.011 - 0.017 (Chow).")
    if key == "c_banks" and cd.fill_ratio < 1.5:
        out.append("Bank sides steeper than 1.5 : 1 may slip when wet.")
    return out


class CanalPreview(QWidget):
    """The canal across, drawn from the answers (to scale), with the part of the current step in amber."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cd = None
        self.highlight = ""

    def set_design(self, cd):
        self.cd = cd
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PLOT_BG))
        cd = self.cd
        if cd is None:
            return
        W, H = self.width(), self.height()
        sq = math.sqrt(1 + cd.z * cd.z)
        b2, d, t, z = cd.bw / 2, cd.depth, cd.lining_t, cd.z
        u_t = b2 + z * d + t * sq
        span = 2 * (u_t + cd.berm_w + 1.6)
        sc = min((W - 30) / span, (H - 70) / (d + t + 0.8))
        cx, base = W / 2, H - 38

        def P(u, e):
            return QPointF(cx + u * sc, base - (e + t) * sc)
        y = max(0.0, d - cd.freeboard)
        water = QPolygonF([P(-b2 - z * y, y), P(b2 + z * y, y), P(b2, 0), P(-b2, 0)])
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(74, 144, 200, 90))
        p.drawPolygon(water)
        u_c = b2 + t * (sq - z)
        lining = QPolygonF([P(-b2 - z * d, d), P(-b2, 0), P(b2, 0), P(b2 + z * d, d), P(u_t, d), P(u_c, -t),
                            P(-u_c, -t), P(-u_t, d)])
        p.setBrush(QColor(170, 176, 186))
        p.setPen(QPen(QColor(theme.INK), 1))
        p.drawPolygon(lining)
        # left: canal in cut (ground higher), right: earth bank (ground lower)
        g = QColor(theme.GROUND)
        ub = u_t + cd.berm_w
        p.setPen(QPen(g, 2))
        p.drawLine(P(-ub, d), P(-u_t, d))
        p.drawLine(P(-ub, d), P(-ub - 0.5 * cd.cut_ratio, d + 0.5))
        p.drawLine(P(-ub - 0.5 * cd.cut_ratio, d + 0.5), P(-span / 2, d + 0.5))
        bank = QPolygonF([P(u_t, d), P(ub, d), P(ub + 0.4 * cd.fill_ratio, d - 0.4), P(u_t, d - 0.4)])
        p.setBrush(QColor(111, 168, 232, 60))
        p.setPen(QPen(QColor(theme.INK), 1))
        p.drawPolygon(bank)
        p.setPen(QPen(g, 2, Qt.DashLine))
        p.drawLine(P(u_c, d - 0.4), P(span / 2, d - 0.4))
        # the part of the current step
        hl = self.highlight
        p.setPen(QPen(QColor(theme.HIGHLIGHT), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if hl == "c_shape":
            path = QPainterPath(P(-b2 - z * d, d))
            for q in (P(-b2, 0), P(b2, 0), P(b2 + z * d, d)):
                path.lineTo(q)
            p.drawPath(path)
        elif hl == "c_lining":
            p.drawPolygon(lining)
        elif hl == "c_fb":
            p.drawLine(P(b2 + z * y + 0.15, y), P(b2 + z * y + 0.15, d))
        elif hl == "c_bed":
            p.drawLine(P(-b2, 0), P(b2, 0))
        elif hl == "c_banks":
            p.drawLine(P(-ub, d), P(-u_t, d))
            p.drawLine(P(-ub, d), P(-ub - 0.5 * cd.cut_ratio, d + 0.5))
            p.drawLine(P(u_t, d), P(ub, d))
            p.drawLine(P(ub, d), P(ub + 0.4 * cd.fill_ratio, d - 0.4))
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(theme.INK))
        p.drawText(QRectF(4, 2, W - 8, 14), Qt.AlignLeft,
                   f"bottom {cd.bw:g} m · depth {cd.depth:g} m · sides {cd.z:g} : 1 · lining {cd.lining_t * 1000:.0f} mm")
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(4, H - 32, W / 2, 14), Qt.AlignLeft, f"in cut: berm {cd.berm_w:g} m, {cd.cut_ratio:g} : 1")
        p.drawText(QRectF(W / 2, H - 32, W / 2 - 4, 14), Qt.AlignRight, f"on a bank: {cd.fill_ratio:g} : 1")
        p.drawText(QRectF(4, H - 16, W - 8, 14), Qt.AlignCenter,
                   f"water up to {y:.2f} m (freeboard {cd.freeboard:g} m) · to scale")
