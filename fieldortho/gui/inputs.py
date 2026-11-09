"""Guided input widgets: degrees-minutes-seconds boxes, grid spacing, lengths in metres or feet.

Field staff should never need to type a degree sign or guess a unit: every box shows its unit,
checks its range while typing and says in plain words what is wrong.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QRegularExpression, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QRegularExpressionValidator
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QVBoxLayout, QWidget)

from ..core import georef as G
from .theme import repolish

FT_PER_M = 3.280839895
# Rough box around the Philippines (with margin): values outside are probably typos
PH_LON = (114.0, 129.0)
PH_LAT = (3.0, 23.0)
_NUM = re.compile(r"^\d+(?:[.,]\d*)?$")


def set_state(w: QWidget, state: str = "") -> None:
    """state: '' (normal), 'error', 'warn' or 'auto' (filled in automatically)."""
    if (w.property("state") or "") != state:
        w.setProperty("state", state)
        repolish(w)


def unit_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Unit")
    return lab


def hint(text: str = "", name: str = "Hint") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(True)
    return lab


def _num(t: str) -> float:
    return float(t.replace(",", "."))


class DmsEdit(QWidget):
    """Latitude or longitude as  [deg]° [min]′ [sec]″ [N/S | E/W].

    Typing a space, °, ' or " jumps to the next box; the boxes also jump on when full.
    Typing N/S/E/W anywhere picks the direction. Pasting a full value such as 124°47'0"E
    or 124.7833 into the degrees box fills all boxes."""
    changed = Signal()

    def __init__(self, is_lat: bool, parent=None):
        super().__init__(parent)
        self.is_lat = is_lat
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.deg = QLineEdit()
        self.min = QLineEdit()
        self.sec = QLineEdit()
        specs = ((self.deg, "deg", 50, "°"), (self.min, "min", 44, "′"), (self.sec, "sec", 52, "″"))
        for ed, ph, w, unit in specs:
            ed.setPlaceholderText(ph)
            ed.setFixedWidth(w)
            ed.setAlignment(Qt.AlignRight)
            ed.installEventFilter(self)
            ed.textEdited.connect(self._edited)
            lay.addWidget(ed)
            u = unit_label(unit)
            u.setFixedWidth(9)
            lay.addWidget(u)
        self.min.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,2}([.,]\d{0,6})?$")))
        self.sec.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,2}([.,]\d{0,4})?$")))
        self.hemi = QComboBox()
        self.hemi.addItems(["N", "S"] if is_lat else ["E", "W"])
        self.hemi.setFixedWidth(56)
        self.hemi.setToolTip("North / South" if is_lat else "East / West (the Philippines is East)")
        self.hemi.currentIndexChanged.connect(lambda _i: self.changed.emit())
        lay.addWidget(self.hemi)
        what ="Latitude" if is_lat else "Longitude"
        self.setToolTip(f"{what}: type degrees, minutes and seconds in the boxes - no ° symbol needed. "
                        "Space or Tab moves to the next box.")
        self._fields = [self.deg, self.min, self.sec]

    # ------------------------------------------------------------ typing help
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress and obj in self._fields:
            t = ev.text()
            if t and t.upper() in ("NSEW"):
                i = self.hemi.findText(t.upper())
                if i >= 0:
                    self.hemi.setCurrentIndex(i)
                return True
            if t in (" ", "°", "'", '"', "′", "″", "d", "m", "s", ":"):
                self._next(obj)
                return True
        return super().eventFilter(obj, ev)

    def _next(self, ed):
        i = self._fields.index(ed)
        if i < 2:
            self._fields[i + 1].setFocus()
            self._fields[i + 1].selectAll()
        else:
            self.hemi.setFocus()

    def _edited(self, text):
        ed = self.sender()
        if ed is self.deg and text and not _NUM.match(text.strip()):
            # pasted or typed a whole coordinate into the degrees box
            try:
                self.set_value(G.parse_angle(text))
            except ValueError:
                pass
        for e in self._fields:
            if (e.property("state") or "") == "auto":
                set_state(e, "")
        full = {self.deg: 2 if self.is_lat else 3, self.min: 2}.get(ed)
        if full and len(text) >= full and text.isdigit() and ed.hasFocus():
            self._next(ed)
        self.changed.emit()

    # ------------------------------------------------------------ values
    def is_empty(self) -> bool:
        return not any(e.text().strip() for e in self._fields)

    def check(self) -> tuple[str, str, float | None]:
        """Returns (level, message, value): level '' (ok), 'empty', 'error' or 'warn'."""
        d, m, s = (e.text().strip() for e in self._fields)
        for e in self._fields:
            if (e.property("state") or "") != "auto":
                set_state(e, "")
        if not (d or m or s):
            return "empty", "", None
        if not d:
            set_state(self.deg, "error")
            return "error", "Type the degrees.", None
        try:
            dv = _num(d)
        except ValueError:
            set_state(self.deg, "error")
            return "error", "Degrees must be a number.", None
        lim = 90 if self.is_lat else 180
        if dv > lim:
            set_state(self.deg, "error")
            return "error", f"Degrees must be 0–{lim}.", None
        if "." in d.replace(",", ".") and (m or s):
            set_state(self.deg, "error")
            return "error", "Use either decimal degrees (e.g. 124.7833) or degrees + minutes + seconds.", None
        try:
            mv = _num(m) if m else 0.0
            sv = _num(s) if s else 0.0
        except ValueError:
            return "error", "Minutes and seconds must be numbers.", None
        if mv >= 60:
            set_state(self.min, "error")
            return "error", "Minutes must be 0–59.", None
        if sv >= 60:
            set_state(self.sec, "error")
            return "error", "Seconds must be below 60.", None
        v = dv + mv / 60 + sv / 3600
        if v > lim:
            set_state(self.deg, "error")
            return "error", f"Must not be more than {lim}°.", None
        if self.hemi.currentIndex() == 1:
            v = -v
        lo, hi = PH_LAT if self.is_lat else PH_LON
        if not (lo <= v <= hi):
            set_state(self.deg, "warn")
            side = "N/S" if self.is_lat else "E/W"
            return "warn", (f"{G.format_dms(v, self.is_lat)} is outside the Philippines – check the "
                            f"numbers and {side}."), v
        return "", "", v

    def value(self) -> float | None:
        lvl, _msg, v = self.check()
        return v if lvl in ("", "warn") else None

    def set_value(self, v: float | None, auto: bool = False):
        if v is None:
            self.clear()
            return
        txt = G.format_dms(v, self.is_lat)          # e.g. 124°47'00"E  or 8°51'30.5"N
        m = re.match(r"(\d+)°(\d+)'([\d.]+)\"([NSEW])", txt)
        if not m:
            return
        d, mi, s, h = m.groups()
        s = s.rstrip("0").rstrip(".") if "." in s else s
        self.deg.setText(d)
        self.min.setText(str(int(mi)))
        self.sec.setText(str(int(s)) if s.isdigit() else s)
        self.hemi.blockSignals(True)
        self.hemi.setCurrentIndex(max(0, self.hemi.findText(h)))
        self.hemi.blockSignals(False)
        for e in self._fields:
            set_state(e, "auto" if auto else "")
        self.changed.emit()

    def clear(self):
        for e in self._fields:
            e.clear()
            set_state(e, "")
        self.changed.emit()

    # text API (compatible with the old single text box)
    def setText(self, text: str):
        text = (text or "").strip()
        if not text:
            self.clear()
        else:
            self.set_value(G.parse_angle(text))

    def text(self) -> str:
        v = self.value()
        return "" if v is None else G.format_dms(v, self.is_lat)


class IntervalEdit(QWidget):
    """Grid spacing as a number plus a unit list (minutes / seconds / degrees)."""
    changed = Signal()
    UNITS = (("minutes (′)", 1 / 60), ("seconds (″)", 1 / 3600), ("degrees (°)", 1.0))

    def __init__(self, value: float = 1, unit: int = 0, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.num = QLineEdit(f"{value:g}")
        self.num.setFixedWidth(64)
        self.num.setAlignment(Qt.AlignRight)
        self.num.setValidator(QRegularExpressionValidator(QRegularExpression(r"^\d{0,3}([.,]\d{0,4})?$")))
        self.unit = QComboBox()
        for t, _f in self.UNITS:
            self.unit.addItem(t)
        self.unit.setCurrentIndex(unit)
        self.unit.setFixedWidth(130)
        self.note = hint("")
        lay.addWidget(self.num)
        lay.addWidget(self.unit)
        lay.addWidget(self.note, 1)
        self.num.textChanged.connect(self._update)
        self.unit.currentIndexChanged.connect(self._update)
        self._update()

    def degrees(self) -> float | None:
        t = self.num.text().strip()
        try:
            v = _num(t)
        except ValueError:
            return None
        return v * self.UNITS[self.unit.currentIndex()][1] if v > 0 else None

    def _update(self):
        d = self.degrees()
        if d is None:
            set_state(self.num, "error" if self.num.text().strip() else "")
            self.note.setText("Type the spacing between grid lines, e.g. 1 minute.")
        else:
            set_state(self.num, "")
            km = d * 111.32
            self.note.setText(f"= about {km:.2f} km between grid lines" if km >= 1 else
                              f"= about {km * 1000:.0f} m between grid lines")
        self.changed.emit()

    # text API (compatible with the old text box: "1'", '30"', "0.5")
    def setText(self, text: str):
        t = text.strip().replace(" ", "")
        m = re.match(r"^(\d+(?:[.,]\d+)?)(.*)$", t)
        if not m:
            return
        u = m.group(2).lower()
        idx = 0 if u in ("'", "′", "m", "min") else 1 if u in ('"', "''", "″", "s", "sec") else 2
        self.num.setText(m.group(1))
        self.unit.setCurrentIndex(idx)

    def text(self) -> str:
        return self.num.text().strip() + ("'", '"', "°")[self.unit.currentIndex()]


class LengthEdit(QWidget):
    """A length entered in metres or feet; always reported in metres.
    `spin` shows the value in the chosen unit; `metres()` returns None when left at 0 (unknown)."""
    changed = Signal()

    def __init__(self, max_m: float = 1500, decimals: int = 0, allow_unknown: bool = True,
                 typical: tuple[float, float] | None = None, parent=None):
        super().__init__(parent)
        self.max_m = max_m
        self.typical = typical
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(0, max_m)
        self.spin.setFixedWidth(100)
        self.spin.setAlignment(Qt.AlignRight)
        if allow_unknown:
            self.spin.setSpecialValueText("not set")
        self.unit = QComboBox()
        self.unit.addItems(["metres (m)", "feet (ft)"])
        self.unit.setFixedWidth(120)
        self.conv = QLabel("")
        self.conv.setObjectName("Good")
        self.conv.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.spin)
        lay.addWidget(self.unit)
        lay.addWidget(self.conv)
        lay.addStretch(1)
        self._feet = False
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self.spin.valueChanged.connect(self._update)
        self._update()

    def metres(self) -> float | None:
        v = self.spin.value()
        if v <= 0:
            return None
        return v / FT_PER_M if self._feet else v

    def set_metres(self, m: float | None):
        self.spin.setValue(0 if not m else (m * FT_PER_M if self._feet else m))

    def _unit_changed(self, i):
        m = self.metres()
        self._feet = i == 1
        self.spin.blockSignals(True)
        self.spin.setRange(0, self.max_m * (FT_PER_M if self._feet else 1))
        self.spin.setValue(0 if m is None else (m * FT_PER_M if self._feet else m))
        self.spin.blockSignals(False)
        self._update()

    def warning(self) -> str:
        m = self.metres()
        if m is None or not self.typical:
            return ""
        lo, hi = self.typical
        if m < lo or m > hi:
            return (f"Unusual value: {m:.0f} m = {m * FT_PER_M:.0f} ft (usually {lo:.0f}–{hi:.0f} m). "
                    f"Check the number and that the unit ({'feet' if self._feet else 'metres'}) is right.")
        return ""

    def _update(self, *_):
        m = self.metres()
        if m is None:
            self.conv.setText("")
        elif self._feet:
            self.conv.setText(f"= {m:.0f} m")
        else:
            self.conv.setText(f"= {m * FT_PER_M:.0f} ft")
        set_state(self.spin, "warn" if self.warning() else "")
        self.changed.emit()


class CoordDialog(QDialog):
    """Coordinates of a clicked control point: guided DMS boxes for lat/long systems,
    easting/northing in metres for projected ones."""

    def __init__(self, geographic: bool, crs_label: str, px: float, py: float, parent=None):
        super().__init__(parent)
        self.geo = geographic
        self.setWindowTitle("Coordinates of this point")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(hint(f"Point at map pixel {px:.1f}, {py:.1f}. Coordinate system: {crs_label}."))
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        if geographic:
            self.lon = DmsEdit(False)
            self.lat = DmsEdit(True)
            g.addWidget(QLabel("Longitude"), 0, 0)
            g.addWidget(self.lon, 0, 1)
            g.addWidget(QLabel("Latitude"), 1, 0)
            g.addWidget(self.lat, 1, 1)
            self.lon.changed.connect(self._check)
            self.lat.changed.connect(self._check)
            lay.addLayout(g)
            lay.addWidget(hint("Type the numbers only – no ° symbol needed. Space moves to the next box."))
        else:
            self.x = QLineEdit()
            self.y = QLineEdit()
            for ed, ph in ((self.x, "e.g. 699500"), (self.y, "e.g. 980250")):
                ed.setPlaceholderText(ph)
                v = QDoubleValidator()
                v.setNotation(QDoubleValidator.StandardNotation)
                ed.setValidator(v)
                ed.textChanged.connect(self._check)
            g.addWidget(QLabel("Easting X"), 0, 0)
            g.addWidget(self.x, 0, 1)
            g.addWidget(unit_label("m"), 0, 2)
            g.addWidget(QLabel("Northing Y"), 1, 0)
            g.addWidget(self.y, 1, 1)
            g.addWidget(unit_label("m"), 1, 2)
            lay.addLayout(g)
        self.msg = hint("", "Error")
        lay.addWidget(self.msg)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        self._check()

    def values(self) -> tuple[float, float] | None:
        if self.geo:
            a, b = self.lon.value(), self.lat.value()
            return None if a is None or b is None else (a, b)
        try:
            return _num(self.x.text()), _num(self.y.text())
        except ValueError:
            return None

    def _check(self, *_):
        msg, name = "", "Error"
        if self.geo:
            for what, ed in (("Longitude", self.lon), ("Latitude", self.lat)):
                lvl, m, _v = ed.check()
                if lvl == "error":
                    msg = f"{what}: {m}"
                    break
                if lvl == "warn" and not msg:
                    msg, name = f"{what}: {m}", "Warn"
        self.msg.setObjectName(name)
        repolish(self.msg)
        self.msg.setText(msg)
        self.bb.button(QDialogButtonBox.Ok).setEnabled(self.values() is not None)
