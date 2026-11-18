"""AASHTO 1993 rigid pavement check against the Guide's own example."""
import math

import pytest

from fieldortho.core import pavement as pv


def test_aashto_1993_guide_example():
    # Guide example: W18 5.1 million, k 72 pci, Ec 5,000,000 psi, S'c 650 psi, J 3.2, Cd 1.0, S0 0.29, R 95%,
    # p0 4.2 / pt 2.5 (ΔPSI 1.7). The equation gives 9.72 in; the Guide's chart reads it as 10.0 in.
    p = pv.Inputs(esal_m=5.1, reliability=95, s0=0.29, p0=4.2, pt=2.5, sc_mpa=650 / pv.PSI_PER_MPA, cd=1.0, j=3.2,
                  ec_mpa=5e6 / pv.PSI_PER_MPA, k_mpa_m=72 / pv.PCI_PER_MPA_M)
    assert pv.thickness_mm(p) / 25.4 == pytest.approx(9.72, abs=0.01)
    assert pv.needed_mm(p) == 250                                  # 246.9 mm rounded up to 10 mm
    assert 10 ** pv._log_w18(9.72, p) / 1e6 == pytest.approx(5.1, rel=0.01)


def test_more_traffic_needs_more_concrete_and_summary():
    a, b = pv.Inputs(esal_m=0.5), pv.Inputs(esal_m=5.0)
    assert pv.thickness_mm(a) < pv.thickness_mm(b)
    assert "is enough" in pv.summary(a, 200) and "NOT enough" in pv.summary(b, 200)
    assert math.isnan(pv.thickness_mm(pv.Inputs(p0=2.0, pt=2.5)))
    assert pv.from_dict(a.as_dict()) == a and pv.from_dict({}) is None
