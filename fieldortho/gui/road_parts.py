"""Guided road steps (1.2): one part of the road per step, each with a picture, a plain explanation, the
suggested (DPWH) values and their source. Values are read-only until "Change dimensions"; each is marked
"standard" or "your value", with the standard value beside a changed one, and DPWH limits are warned about."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox, QGridLayout, QLabel, QVBoxLayout, QWidget)

from ..core import road as rdm
from . import theme
from .widgets import label

PART_STEPS = ("carriage", "shoulders", "layers", "ditch", "slopes")

TITLES = {"std": "Which road standard?", "carriage": "Carriageway: the concrete driving lanes",
          "shoulders": "Shoulders", "layers": "Pavement layers: PCCP and aggregate subbase",
          "ditch": "Road side ditch", "slopes": "Side slopes (cut and fill)"}
SHORT = {"std": "Road standard", "carriage": "Carriageway", "shoulders": "Shoulders", "layers": "Pavement layers",
         "ditch": "Road side ditch", "slopes": "Side slopes"}

EXPLAIN = {
    "std": "Pick the standard to start from. Its values fill in the next steps, where you accept them (OK, next) "
           "or change them (Change dimensions). Changed values are marked “your value” on screen and in the report.",
    "carriage": "The paved part where vehicles drive, split equally either side of the centre line. It falls from "
                "the centre (the crown) toward both edges so rain runs off to the shoulders.",
    "shoulders": "Gravel or aggregate strips on both sides of the carriageway, for stopping and to protect the edge "
                 "of the concrete. They slope more steeply than the concrete so water drains fast.",
    "layers": "The concrete slab (PCCP) sits on a layer of compacted aggregate (the subbase), which also runs under "
              "the shoulders. Cut and fill stop at the bottom of the subbase, so earthworks are measured to that "
              "level, not to the road surface.",
    "ditch": "Catches water from the road and from the slope above it. Where the road is in cut it sits at the "
             "shoulder edge; on fill it can also run along the toe of the embankment. V-shaped is the DPWH form; "
             "a bottom width makes it flat-bottomed.",
    "slopes": "How steeply the sides go down from the road edge to the ground (fill) or up to the ground (cut). "
              "Written horizontal : vertical, so 2 : 1 means 2 m across for every 1 m up or down. Flatter sides are "
              "safer and stand better in rain, but need more land and more earth.",
}
WHY_RADIUS = ("Why a smallest radius? On a tight bend a 6-wheeler truck swings across the centre line, the road "
              "needs a lot of extra width and tilt, and drivers cannot see oncoming traffic. DPWH D.O. 112 s. 2019 "
              "sets 30 m for these roads and 50 m for Tourism roads, which carry buses and faster traffic. Where the "
              "land leaves no choice (a mountain switchback, a house in the way) you can type a smaller value; the "
              "app warns you and the report marks those bends.")
WHY_VC = ("Why at least 60 m? A short vertical curve feels like a bump on a crest or a dip in a sag, and it hides "
          "the road ahead. AASHTO's own minimum at 30 km/h is only 18 m, so 60 m is the office's smoother rule. The "
          "app uses the longest of 60 m, K × A and 0.6 × speed, and keeps proposed grade change points 60 m apart "
          "(30 m from the start and the end). Where points you place are closer, the curves are shortened to fit; "
          "the app warns you and the report marks them.")

PILL = "border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600;"


def source_text(key: str, std_key: str) -> str:
    std = rdm.STANDARDS.get(std_key)
    if not std:
        return "Your own values (they start from the DPWH values for under 200 vehicles a day)."
    dpwh = rdm.DPWH_SOURCE
    return {"carriage": f"Source: {dpwh}.", "shoulders": f"Source: {dpwh}.",
            "layers": f"PCCP minimum: {dpwh}. Aggregate subbase 200 mm: {rdm.SECTION_SOURCE}.",
            "ditch": f"{rdm.SECTION_SOURCE} (the 0.50 m depth is our suggestion).",
            "slopes": f"Fill 2 : 1: {rdm.SECTION_SOURCE}; DPWH allows fill no steeper than 1.5 : 1 and cut 1.5 : 1 to "
                      f"1 : 1 in common soil ({dpwh})."}.get(key, "")


def reference(std_key: str) -> tuple[dict, str]:
    """Values a part is compared with, and their name: the chosen standard, or the DPWH starting values."""
    std = rdm.STANDARDS.get(std_key)
    if std:
        return std, "standard"
    return rdm.STANDARDS["fmr_low"], "starting value"


class PartForm(QWidget):
    """The values of one road part. rows: (caption, widget, key, scale, unit, hint); a checkbox row has no unit."""

    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(4)
        self.marks, self.shown = [], []
        r = 0
        for caption, w, _key, _scale, _unit, hint in rows:
            shown = None
            if isinstance(w, QCheckBox):
                g.addWidget(w, r, 0, 1, 2)
            else:
                g.addWidget(QLabel(caption), r, 0)
                if isinstance(w, QAbstractSpinBox):
                    # read-only values are shown as text; the spin box appears for "Change dimensions"
                    shown = QLabel("")
                    shown.setStyleSheet("font-size: 15px; font-weight: 600; padding: 4px 2px;")
                    shown.setMinimumWidth(w.minimumWidth() or 110)
                    g.addWidget(shown, r, 1)
                g.addWidget(w, r, 1)
            self.shown.append(shown)
            m = QLabel("")
            m.setStyleSheet(PILL)
            m.setWordWrap(True)
            self.marks.append(m)
            g.addWidget(m, r, 2, Qt.AlignLeft | Qt.AlignVCenter)
            r += 1
            if hint:
                g.addWidget(label(hint, "Hint", wrap=True), r, 0, 1, 3)
                r += 1
        g.setColumnStretch(2, 1)
        self.editing = True
        self.set_editing(False)

    def set_editing(self, on: bool):
        self.editing = on
        for (_c, w, *_), shown in zip(self.rows, self.shown):
            if isinstance(w, QAbstractSpinBox):
                w.setVisible(on)
                shown.setVisible(not on)
                shown.setText(w.text())
            elif isinstance(w, (QCheckBox, QComboBox)):
                w.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
                w.setFocusPolicy(Qt.StrongFocus if on else Qt.NoFocus)

    def refresh(self, ref: dict, ref_name: str):
        for (_c, w, *_), shown in zip(self.rows, self.shown):
            if shown is not None:
                shown.setText(w.text())
        for (_c, w, key, scale, unit, _h), m in zip(self.rows, self.marks):
            if key not in ref:
                m.setText("")
                m.setVisible(False)
                continue
            m.setVisible(True)
            if isinstance(w, QCheckBox):
                same, rv = w.isChecked() == bool(ref[key]), ("yes" if ref[key] else "no")
            else:
                v = w.value() / scale
                same = abs(v - ref[key]) < 1e-6
                rv = f"{ref[key] * scale:g}{unit}"
            if same:
                m.setText(ref_name)
                m.setStyleSheet(PILL + f"background: {theme.ACCENT_SOFT}; color: {theme.ACCENT};")
            else:
                m.setText(f"your value · {ref_name} {rv}")
                m.setStyleSheet(PILL + f"background: {theme.WARN_SOFT}; color: {theme.WARN};")


def warnings(key: str, rd: rdm.RoadDesign, std_key: str) -> list[str]:
    """DPWH limits broken by the values of one part (empty when none)."""
    std = rdm.STANDARDS.get(std_key)
    out = []
    if key == "carriage":
        if std and rd.width < std["width"] - 1e-9:
            out.append(f"Narrower than the DPWH minimum of {std['width']:.2f} m for this road.")
        if std and abs(rd.crown_pct - std["crown_pct"]) > 1e-9:
            out.append(f"DPWH uses {std['crown_pct']:g}% on concrete.")
    elif key == "shoulders":
        if std and (not rd.shoulders or rd.shoulder_w < std["shoulder_w"] - 1e-9):
            out.append(f"DPWH asks for {std['shoulder_w']:g} m shoulders on this road.")
    elif key == "layers":
        if std and rd.pccp_t < std["pccp_t"] - 1e-9:
            out.append(f"Thinner than the DPWH minimum of {std['pccp_t'] * 1000:.0f} mm of concrete.")
        if rd.pccp_t <= 0:
            out.append("No concrete layer: the road is earth or gravel only.")
    elif key == "ditch":
        if std and not rd.ditch:
            out.append("No road side ditches: water from the road and the slopes stays on the road side.")
    elif key == "slopes":
        if rd.fill_ratio < 1.5 - 1e-9:
            out.append("Fill sides steeper than the DPWH limit of 1.5 : 1.")
        if rd.cut_ratio < 1.0 - 1e-9:
            out.append("Cut sides steeper than 1 : 1. DPWH allows this only in rock (0.5 : 1 to 1 : 1 rippable rock).")
    return out


def part_page(key: str, preview, form: PartForm) -> tuple[QWidget, QLabel, QLabel]:
    """The page of one part: picture, explanation, values, source line and a warning line."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(8)
    v.addWidget(preview)
    v.addWidget(label(EXPLAIN[key], "", wrap=True))
    v.addWidget(form)
    src = label("", "Hint", wrap=True)
    v.addWidget(src)
    warn = label("", "Warn", wrap=True)
    v.addWidget(warn)
    return w, src, warn
