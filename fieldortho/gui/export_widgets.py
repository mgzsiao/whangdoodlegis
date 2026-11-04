"""Shared export controls: coordinate system picker, format checkboxes, folder picker."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..core import crs as crsmod
from ..core import exporters as ex

OTHER = "__other__"


class CrsPicker(QWidget):
    """Choose WGS 84, PRS92, UTM, Luzon 1911... or type any EPSG code."""
    changed = Signal(str)

    def __init__(self, choices=None, default="EPSG:4326", lonlat=None):
        super().__init__()
        self.lonlat = lonlat
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.combo = QComboBox()
        for code, text in (choices or crsmod.EXPORT_CHOICES):
            self.combo.addItem(f"{text}  [{code}]" if code.startswith("EPSG") else text, code)
        self.combo.addItem("Other coordinate system (type EPSG code or PROJ text)…", OTHER)
        lay.addWidget(self.combo)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("e.g. EPSG:32651")
        self.custom.setVisible(False)
        lay.addWidget(self.custom)
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.combo.currentIndexChanged.connect(self._update)
        self.custom.textChanged.connect(self._update)
        self.set_code(default)

    def set_lonlat(self, lonlat):
        self.lonlat = lonlat
        self._update()

    def set_code(self, code: str):
        i = self.combo.findData(code)
        if i < 0:
            self.combo.setCurrentIndex(self.combo.findData(OTHER))
            self.custom.setText(code)
        else:
            self.combo.setCurrentIndex(i)
        self._update()

    def code(self) -> str | None:
        d = self.combo.currentData()
        if d == OTHER:
            t = self.custom.text().strip()
            if t.isdigit():
                t = f"EPSG:{t}"
            return t if t and crsmod.is_valid(t) else None
        return d

    def _update(self):
        other = self.combo.currentData() == OTHER
        self.custom.setVisible(other)
        c = self.code()
        if c is None:
            self.note.setText("⚠ Not a valid coordinate system." if other and self.custom.text() else "")
            return
        txt = f"Units: {crsmod.units_label(c)}."
        if self.lonlat:
            try:
                n = crsmod.datum_note("EPSG:4326", c, *self.lonlat)
                if not n.startswith("Same"):
                    txt += " " + n
            except Exception:
                pass
        if not other:
            pass
        else:
            try:
                txt = crsmod.resolve(c).name + ". " + txt
            except Exception:
                pass
        self.note.setText(txt)
        self.changed.emit(c)


SHORT = {"gtiff": "GeoTIFF", "png": "PNG", "jpeg": "JPEG", "kmz": "KMZ", "asc": "ASCII grid", "xyz": "XYZ",
         "gpkg": "GeoPackage", "shp": "Shapefile", "geojson": "GeoJSON", "kml": "KML", "dxf": "DXF"}


class FormatChecks(QWidget):
    def __init__(self, formats: list[str], vector: bool = False, checked: list[str] = ()):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.boxes = {}
        table = ex.VECTOR_FORMATS if vector else ex.RASTER_FORMATS
        for f in formats:
            cb = QCheckBox(SHORT.get(f, table[f][0]))
            cb.setToolTip(table[f][0])
            cb.setChecked(f in checked)
            self.boxes[f] = cb
            lay.addWidget(cb)
        lay.addStretch(1)

    def selected(self) -> list[str]:
        return [f for f, cb in self.boxes.items() if cb.isChecked() and cb.isEnabled()]

    def setEnabled(self, on):
        for cb in self.boxes.values():
            cb.setEnabled(on)


class FolderPicker(QWidget):
    def __init__(self, path: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(path)
        lay.addWidget(self.edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        lay.addWidget(b)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Export to folder", self.edit.text() or os.path.expanduser("~"))
        if d:
            self.edit.setText(d)

    def path(self) -> str:
        return self.edit.text().strip()
