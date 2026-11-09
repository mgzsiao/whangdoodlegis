"""Guided inputs (degrees/minutes/seconds, grid spacing, metres/feet) and the Jobs-list records."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from fieldortho.gui.inputs import DmsEdit, IntervalEdit, LengthEdit  # noqa: E402


def _dms(is_lat, d, m="", s="", hemi=None):
    e = DmsEdit(is_lat)
    e.deg.setText(d)
    e.min.setText(m)
    e.sec.setText(s)
    if hemi:
        e.hemi.setCurrentIndex(e.hemi.findText(hemi))
    return e


def test_dms_boxes_give_decimal_degrees():
    assert _dms(False, "124", "47", "0").value() == pytest.approx(124 + 47 / 60)
    assert _dms(True, "8", "52", "30").value() == pytest.approx(8 + 52 / 60 + 30 / 3600)
    assert _dms(False, "124.7833").value() == pytest.approx(124.7833)        # decimal degrees also fine
    assert _dms(True, "8", "52", "", "S").check()[0] == "warn"              # south of the Philippines
    assert _dms(True, "8", "52", "", "S").value() == pytest.approx(-(8 + 52 / 60))


def test_dms_rejects_bad_values_in_plain_words():
    lvl, msg, v = _dms(True, "8", "61").check()
    assert (lvl, v) == ("error", None) and "0–59" in msg
    assert _dms(False, "190").check()[0] == "error"
    assert _dms(False, "", "47").check()[1] == "Type the degrees."
    assert _dms(False, "124.5", "30").check()[0] == "error"                 # mixed decimal + minutes
    assert _dms(False, "").check()[0] == "empty"
    assert _dms(False, "142", "47").check()[0] == "warn"                    # 142 instead of 124: typo


def test_dms_accepts_a_pasted_full_coordinate():
    e = DmsEdit(False)
    e.setText("124°47'30\"E")
    assert (e.deg.text(), e.min.text(), e.sec.text(), e.hemi.currentText()) == ("124", "47", "30", "E")
    e.setText("124 48 0")
    assert e.value() == pytest.approx(124.8)
    assert e.text() == "124°48'00\"E"


def test_interval_and_length():
    iv = IntervalEdit()
    iv.setText("1'")
    assert iv.degrees() == pytest.approx(1 / 60)
    iv.setText('30"')
    assert iv.degrees() == pytest.approx(30 / 3600)
    iv.num.setText("0")
    assert iv.degrees() is None
    le = LengthEdit(typical=(20, 500))
    assert le.metres() is None                       # 0 = not set
    le.spin.setValue(100)
    assert le.metres() == pytest.approx(100) and le.conv.text() == "= 328 ft"
    le.unit.setCurrentIndex(1)                       # switching to feet keeps the height
    assert le.spin.value() == pytest.approx(328, abs=0.5) and le.metres() == pytest.approx(100, abs=0.2)
    le.spin.setValue(5000)                           # 5000 ft = 1524 m: unusual
    assert "Unusual" in le.warning()


def test_activity_records(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from fieldortho.core import activity
    f = tmp_path / "map.georef-session.json"
    f.write_text("{}")
    a = activity.record(activity.Activity(kind="georef", name="map", path=str(f), status="Saved"))
    activity.record(activity.Activity(kind="georef", name="map", path=str(f), status="Exported"))  # replaces
    items = activity.load()
    assert len(items) == 1 and items[0].status == "Exported" and items[0].kind_label == "Georeference"
    activity.remove(items[0].id)
    assert activity.load() == [] and a.exists()
