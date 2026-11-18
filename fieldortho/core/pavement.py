"""AASHTO 1993 rigid pavement check: the concrete (PCCP) thickness the traffic needs.

Rigid pavement design equation, AASHTO Guide for Design of Pavement Structures (1993), with D in inches:
    log10 W18 = ZR S0 + 7.35 log10(D + 1) - 0.06 + log10(ΔPSI / 3.0) / (1 + 1.624e7 / (D + 1)^8.46)
                + (4.22 - 0.32 pt) log10[ S'c Cd (D^0.75 - 1.132) / (215.63 J (D^0.75 - 18.42 / (Ec / k)^0.25)) ]
W18 = 18-kip equivalent single axle loads over the design life, ZR = standard normal deviate for the
reliability, S0 = overall standard deviation, ΔPSI = p0 - pt (serviceability loss), S'c = concrete modulus of
rupture (psi), Cd = drainage coefficient, J = load transfer coefficient, Ec = concrete elastic modulus (psi),
k = modulus of subgrade reaction (pci). The equation is solved for D (bisection). Checked against the
Guide's example (5.1 million ESAL, k 72 pci, Ec 5,000,000 psi, S'c 650 psi, J 3.2, Cd 1.0, S0 0.29, R 95%,
ΔPSI 1.7): D = 9.72 in by the equation (the Guide's chart reads 10.0 in). The app rounds up to 10 mm.
Inputs are metric in the app: MPa (x 145.038 = psi) and MPa/m (x 3.684 = pci).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

SOURCE = "AASHTO Guide for Design of Pavement Structures (1993), rigid pavement design equation"
ZR = {50: 0.0, 60: -0.253, 70: -0.524, 75: -0.674, 80: -0.841, 85: -1.037, 90: -1.282, 95: -1.645, 99: -2.327}
PSI_PER_MPA = 145.038
PCI_PER_MPA_M = 3.684


@dataclass
class Inputs:
    esal_m: float = 0.5          # million ESAL over the design life (example: a low-traffic road)
    reliability: int = 85        # %
    s0: float = 0.35             # overall standard deviation (rigid: 0.30 - 0.40)
    p0: float = 4.5              # initial serviceability
    pt: float = 2.0              # terminal serviceability (2.0 - 2.5 for low-volume roads)
    sc_mpa: float = 4.5          # concrete flexural strength (modulus of rupture)
    cd: float = 1.0              # drainage coefficient
    j: float = 3.2               # load transfer (doweled joints, tied shoulders ~ 2.7 - 3.2)
    ec_mpa: float = 27800.0      # concrete elastic modulus
    k_mpa_m: float = 40.0        # modulus of subgrade reaction (on top of the subbase)

    def as_dict(self) -> dict:
        return asdict(self)


def from_dict(d: dict | None) -> Inputs | None:
    if not d:
        return None
    return Inputs(**{k: v for k, v in d.items() if k in Inputs.__dataclass_fields__})


def _log_w18(d_in: float, p: Inputs) -> float:
    zr = ZR.get(int(p.reliability), ZR[85])
    sc, ec, k = p.sc_mpa * PSI_PER_MPA, p.ec_mpa * PSI_PER_MPA, p.k_mpa_m * PCI_PER_MPA_M
    a = d_in ** 0.75
    inner = sc * p.cd * (a - 1.132) / (215.63 * p.j * (a - 18.42 / (ec / k) ** 0.25))
    return (zr * p.s0 + 7.35 * math.log10(d_in + 1) - 0.06
            + math.log10((p.p0 - p.pt) / 3.0) / (1 + 1.624e7 / (d_in + 1) ** 8.46)
            + (4.22 - 0.32 * p.pt) * math.log10(inner))


def thickness_mm(p: Inputs) -> float:
    """Concrete thickness (mm) the equation needs; NaN when outside 65 - 600 mm or the inputs are impossible."""
    if p.esal_m <= 0 or p.p0 <= p.pt or min(p.sc_mpa, p.ec_mpa, p.k_mpa_m, p.cd, p.j) <= 0:
        return float("nan")
    target = math.log10(p.esal_m * 1e6)
    lo, hi = 2.6, 24.0                      # inches
    try:
        if _log_w18(lo, p) >= target:
            return lo * 25.4
        if _log_w18(hi, p) < target:
            return float("nan")
    except ValueError:
        return float("nan")
    for _ in range(80):
        m = (lo + hi) / 2
        if _log_w18(m, p) < target:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2 * 25.4


def needed_mm(p: Inputs) -> float:
    """Thickness to build: rounded up to the next 10 mm."""
    t = thickness_mm(p)
    return math.ceil(t / 10 - 1e-9) * 10 if math.isfinite(t) else t


def summary(p: Inputs, pccp_mm: float) -> str:
    n = needed_mm(p)
    if not math.isfinite(n):
        return "AASHTO 1993 check: the inputs give no answer (check the traffic and strength values)."
    ok = pccp_mm + 1e-6 >= n
    return (f"AASHTO 1993 check: {p.esal_m:g} million ESAL at {p.reliability}% reliability, k {p.k_mpa_m:g} MPa/m, "
            f"S'c {p.sc_mpa:g} MPa, J {p.j:g}, Cd {p.cd:g}: needs {n:.0f} mm of concrete. "
            + (f"The {pccp_mm:.0f} mm chosen is enough." if ok else f"The {pccp_mm:.0f} mm chosen is NOT enough."))
