"""Professional PDF reports (1.2): the agreed "B + C" style on ReportLab.

Navy and amber of style B, clean hairline tables and monospaced numbers of style C, a plain cover. Headings in
Manrope, text in IBM Plex Sans, numbers in IBM Plex Mono (all SIL Open Font License, bundled in
resources/fonts). Report pages are portrait (A4 or long bond 8.5 x 13 in); drawing sheets are landscape
(A3, A4 or long bond) in the same file.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from .. import APP_NAME, __version__

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "fonts")

NAVY = HexColor("#13284F")
AMBER = HexColor("#F5B841")
AMBER_TXT = HexColor("#B07A0E")
INK = HexColor("#1D222B")
INK2 = HexColor("#3D4452")
MUTED = HexColor("#5B6270")
LINE = HexColor("#D5D9E0")
RULE = HexColor("#C9CED8")
SOFT = HexColor("#F2F5FA")
HDR_TXT = HexColor("#C9D4E6")
CUT = HexColor("#C42820")
FILL = HexColor("#1E5AAA")
GROUND = HexColor("#6B4A2B")
TAGS = {"standard": (HexColor("#E7EDF7"), HexColor("#1F3F77")), "suggested": (HexColor("#E7EDF7"), HexColor("#1F3F77")),
        "your value": (HexColor("#FDF0D2"), HexColor("#7A4A00")), "exception": (HexColor("#FBE3E0"), HexColor("#9B2A1F"))}

SANS, SANS_B, SANS_I = "PlexSans", "PlexSans-SemiBold", "PlexSans-Italic"
MONO, MONO_M = "PlexMono", "PlexMono-Medium"
HEAD, HEAD_B = "Manrope-Bold", "Manrope-ExtraBold"
_FILES = {SANS: "IBMPlexSans-Regular.ttf", SANS_B: "IBMPlexSans-SemiBold.ttf", SANS_I: "IBMPlexSans-Italic.ttf",
          MONO: "IBMPlexMono-Regular.ttf", MONO_M: "IBMPlexMono-Medium.ttf", HEAD: "Manrope-Bold.ttf",
          HEAD_B: "Manrope-ExtraBold.ttf"}
_registered = False


def register_fonts():
    global _registered
    if _registered:
        return
    for name, f in _FILES.items():
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, f)))
    pdfmetrics.registerFontFamily(SANS, normal=SANS, bold=SANS_B, italic=SANS_I, boldItalic=SANS_B)
    pdfmetrics.registerFontFamily(MONO, normal=MONO, bold=MONO_M, italic=MONO, boldItalic=MONO_M)
    _registered = True


# ============================================================================ paper
@dataclass(frozen=True)
class Paper:
    key: str
    label: str
    page: tuple                 # report pages (portrait), points
    sheet: tuple                # drawing sheets (landscape), points
    sheet_name: str             # "A3", "A4", "Long bond"
    pp_scale: int               # plan and profile: horizontal scale 1 : n (vertical n / 10)
    xs_rows: tuple              # cross-sections: ((rows per column, smallest scale), ...) tried in order


LONG = (8.5 * inch, 13 * inch)
PAPERS = {
    "a4_a3": Paper("a4_a3", "A4 report, drawings on A3", A4, landscape(A3), "A3", 1250, ((4, 200), (3, 0))),
    "a4": Paper("a4", "A4 only", A4, landscape(A4), "A4", 2000, ((4, 150), (3, 250), (2, 0))),
    "long": Paper("long", "Long bond (8.5 × 13 in)", LONG, landscape(LONG), "Long bond", 2000,
                  ((4, 150), (3, 250), (2, 0))),
}


# ============================================================================ text
def esc(s) -> str:
    return escape(str(s))


def styles() -> dict:
    register_fonts()
    S = {}
    S["body"] = ParagraphStyle("body", fontName=SANS, fontSize=9.2, leading=13.4, textColor=INK, spaceAfter=5)
    S["small"] = ParagraphStyle("small", parent=S["body"], fontSize=7.8, leading=10.6, textColor=MUTED, spaceAfter=4)
    S["eyebrow"] = ParagraphStyle("eyebrow", fontName=MONO_M, fontSize=7.4, leading=9, textColor=AMBER_TXT,
                                  spaceAfter=2)
    S["h2"] = ParagraphStyle("h2", fontName=HEAD_B, fontSize=19, leading=22.5, textColor=NAVY, spaceAfter=9)
    S["h3"] = ParagraphStyle("h3", fontName=HEAD, fontSize=11, leading=14, textColor=NAVY, spaceBefore=9,
                             spaceAfter=4)
    S["cell"] = ParagraphStyle("cell", fontName=SANS, fontSize=8.1, leading=10.4, textColor=INK)
    S["cellb"] = ParagraphStyle("cellb", parent=S["cell"], fontName=SANS_B)
    S["num"] = ParagraphStyle("num", fontName=MONO, fontSize=7.9, leading=10.4, textColor=INK, alignment=2)
    S["numl"] = ParagraphStyle("numl", parent=S["num"], alignment=0)
    S["head"] = ParagraphStyle("head", fontName=SANS_B, fontSize=6.9, leading=8.6, textColor=NAVY)
    S["headr"] = ParagraphStyle("headr", parent=S["head"], alignment=2)
    S["toc0"] = ParagraphStyle("toc0", fontName=SANS, fontSize=10.2, leading=15, textColor=INK, leftIndent=0)
    S["toc1"] = ParagraphStyle("toc1", fontName=SANS, fontSize=8.8, leading=12.5, textColor=INK2, leftIndent=16)
    S["bullet"] = ParagraphStyle("bullet", parent=S["body"], leftIndent=11, bulletIndent=0, spaceAfter=3)
    S["formula"] = ParagraphStyle("formula", fontName=MONO, fontSize=8, leading=11, textColor=INK, leftIndent=12,
                                  backColor=SOFT, borderPadding=(4, 6, 4, 6), spaceBefore=4, spaceAfter=8)
    return S


class TocHeading(Paragraph):
    """A heading that becomes an entry of the contents (level 0 = section, 1 = drawing sheet)."""

    def __init__(self, text, style, toc_text, level=0, key=""):
        super().__init__(text, style)
        self.toc = (level, toc_text, key or f"h{id(self)}")


class TocMark(Flowable):
    """An invisible contents entry (for drawing sheets, which have no heading paragraph)."""

    def __init__(self, toc_text, level=1):
        super().__init__()
        self.toc = (level, toc_text, f"m{id(self)}")

    def wrap(self, aw, ah):
        return 0, 0

    def draw(self):
        pass


def section(S, number: str, title: str, eyebrow: str | None = None) -> list:
    """Eyebrow ("SECTION 1") + heading, kept together; listed in the contents."""
    eb = eyebrow if eyebrow is not None else f"SECTION {number}"
    return [KeepTogether([Paragraph(esc(eb.upper()), S["eyebrow"]),
                          TocHeading(esc(title), S["h2"], f"{number} {title}")])]


def bullets(S, items) -> list:
    return [Paragraph(t, S["bullet"], bulletText="•") for t in items]


def _cell(S, v, num: bool, bold=False):
    if isinstance(v, Flowable):
        return v
    if v is None:
        v = "–"
    return Paragraph(v if isinstance(v, Markup) else esc(v), S["num"] if num else (S["cellb"] if bold else S["cell"]))


class Markup(str):
    """Text already in ReportLab paragraph markup (not escaped)."""


def table(S, rows, widths, header=None, num_cols=(), bold_rows=(), zebra=False, font_size=None) -> Table:
    """Clean hairline table (style C) with a navy header rule. widths: fractions of the frame width or points."""
    data = []
    if header:
        data.append([Paragraph(esc(h).upper(), S["headr"] if i in num_cols else S["head"])
                     for i, h in enumerate(header)])
    for k, r in enumerate(rows):
        data.append([_cell(S, v, i in num_cols, k in bold_rows) for i, v in enumerate(r)])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4),
          ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3.2),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
          ("LINEBELOW", (0, 1 if header else 0), (-1, -1), 0.4, LINE)]
    if header:
        st.append(("LINEBELOW", (0, 0), (-1, 0), 1.2, NAVY))
    if zebra:
        for i in range(1 if header else 0, len(data), 2):
            st.append(("BACKGROUND", (0, i), (-1, i), SOFT))
    t.setStyle(TableStyle(st))
    return t


def fit_widths(fracs, total):
    return [f * total for f in fracs]


class Tag(Flowable):
    """A small rounded label: standard / suggested / your value / exception."""

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.bg, self.fg = TAGS.get(text, TAGS["your value"])

    def wrap(self, aw, ah):
        register_fonts()
        self.w = pdfmetrics.stringWidth(self.text, SANS_B, 6.6) + 8
        return min(self.w, aw), 10

    def draw(self):
        c = self.canv
        c.setFillColor(self.bg)
        c.roundRect(0, 0, self.w, 9.6, 4.8, stroke=0, fill=1)
        c.setFillColor(self.fg)
        c.setFont(SANS_B, 6.6)
        c.drawString(4, 2.9, self.text)


def key_figures(S, items, width) -> Table:
    """[(value, label), ...]: big monospaced numbers with a navy rule on the left (style B + C)."""
    vs = ParagraphStyle("kfv", fontName=MONO_M, fontSize=13.5, leading=16, textColor=NAVY)
    ls = ParagraphStyle("kfl", fontName=SANS, fontSize=7.2, leading=9, textColor=MUTED)
    row = [[Paragraph(esc(v), vs), Paragraph(esc(lbl), ls)] for v, lbl in items]
    n = len(items)
    t = Table([row], colWidths=[width / n] * n, hAlign="LEFT")
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 7),
          ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 2),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for i in range(n):
        st.append(("LINEBEFORE", (i, 0), (i, 0), 2.6, NAVY))
    t.setStyle(TableStyle(st))
    return t


def signatures(S, people, width) -> Table:
    """Signature blocks, two per row: role, signing line, name, position, date."""
    role = ParagraphStyle("role", fontName=MONO_M, fontSize=6.8, leading=8.5, textColor=MUTED)
    name = ParagraphStyle("name", fontName=SANS_B, fontSize=9, leading=11.5, textColor=INK)
    pos = ParagraphStyle("pos", fontName=SANS, fontSize=8, leading=10.5, textColor=INK2)
    cells = []
    for r, n, p in people:
        cells.append([Paragraph(esc(r.upper()), role), Spacer(1, 30), SigLine(),
                      Paragraph(esc(n) or "&nbsp;", name), Paragraph(esc(p) or "&nbsp;", pos),
                      Paragraph("Date: ____________________", pos)])
    rows = [cells[i:i + 2] + ([[""]] if len(cells[i:i + 2]) == 1 else []) for i in range(0, len(cells), 2)]
    t = Table(rows, colWidths=[width / 2] * 2, hAlign="LEFT")
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 26), ("BOTTOMPADDING", (0, 0), (-1, -1), 18)]))
    return t


class SigLine(Flowable):
    def wrap(self, aw, ah):
        self.w = aw
        return aw, 4

    def draw(self):
        self.canv.setStrokeColor(INK)
        self.canv.setLineWidth(0.7)
        self.canv.line(0, 2, self.w, 2)


class Picture(Flowable):
    """A PIL image (embedded as JPEG) scaled to a width, with a thin navy frame."""

    def __init__(self, im, width, max_height=None, frame=True):
        super().__init__()
        self.im = im.convert("RGB")
        w, h = self.im.size
        self.dw = width
        self.dh = width * h / w
        if max_height and self.dh > max_height:
            self.dh = max_height
            self.dw = max_height * w / h
        self.frame = frame

    def wrap(self, aw, ah):
        return self.dw, self.dh

    def draw(self):
        from reportlab.lib.utils import ImageReader
        b = io.BytesIO()
        self.im.save(b, "JPEG", quality=88)
        b.seek(0)
        self.canv.drawImage(ImageReader(b), 0, 0, self.dw, self.dh)
        if self.frame:
            self.canv.setStrokeColor(NAVY)
            self.canv.setLineWidth(0.6)
            self.canv.rect(0, 0, self.dw, self.dh)


def jpeg_reader(im, quality=86):
    from reportlab.lib.utils import ImageReader
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=quality)
    b.seek(0)
    return ImageReader(b)


# ============================================================================ document
@dataclass
class DocInfo:
    title: str                                    # project name (cover title)
    kind: str                                     # "Road design and quantities"
    location: str = ""
    report_no: str = ""
    revision: str = "0"
    date: str = ""
    office: list = field(default_factory=list)    # up to 3 lines, the last one in bold
    logo: str = ""                                # optional image file
    facts: list = field(default_factory=list)     # [(value, label)] x 4 on the cover
    control: list = field(default_factory=list)   # [(role, name, position, date)] document control
    standard: str = ""                            # last row of the document control table
    survey: str = ""                              # cover footer, right side

    @property
    def header_left(self) -> str:
        return self.title + (f" · {self.location}" if self.location else "")

    @property
    def header_right(self) -> str:
        return f"Report {self.report_no}" if self.report_no else self.date


def footer_text() -> str:
    return f"Prepared with {APP_NAME} {__version__}"


def numbered_canvas(info: DocInfo):
    """Canvas class that writes the header band and "Page x of N" footer once the page count is known."""

    class NumberedCanvas(Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []
            self._kind = "body"
            self._sheet_hdr = None

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._saved)
            for i, state in enumerate(self._saved):
                self.__dict__.update(state)
                self._decorate(i + 1, n)
                Canvas.showPage(self)
            Canvas.save(self)

        def _decorate(self, page, n):
            if self._kind == "cover":
                return
            W, H = self._pagesize
            sheet = self._kind == "sheet"
            band = 6.2 * mm if sheet else 9 * mm
            mx = 8 * mm if sheet else 20 * mm
            self.saveState()
            self.setFillColor(NAVY)
            self.rect(0, H - band, W, band, stroke=0, fill=1)
            self.setFont(MONO, 6.6 if sheet else 7.2)
            self.setFillColor(HDR_TXT)
            left, right = self._sheet_hdr if (sheet and self._sheet_hdr) else (info.header_left, info.header_right)
            y = H - band + (band - 5) / 2
            self.drawString(mx, y, _clip(left, MONO, 7.2, W * 0.62))
            self.setFillColor(white)
            self.drawRightString(W - mx, y, right)
            fy = 6.2 * mm if sheet else 12 * mm
            self.setStrokeColor(AMBER)
            self.setLineWidth(1.4)
            self.line(mx, fy + 3.2 * mm, W - mx, fy + 3.2 * mm)
            self.setFillColor(MUTED)
            self.setFont(MONO, 6.6 if sheet else 7.2)
            self.drawString(mx, fy, footer_text())
            self.drawRightString(W - mx, fy, f"Page {page} of {n}")
            self.restoreState()

    return NumberedCanvas


def _clip(text, font, size, width):
    if pdfmetrics.stringWidth(text, font, size) <= width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


class ReportDoc(BaseDocTemplate):
    """Cover, body pages (portrait) and drawing sheets (landscape) with a contents list."""

    def __init__(self, path, paper: Paper, info: DocInfo):
        register_fonts()
        self.paper = paper
        self.info = info
        W, H = paper.page
        super().__init__(path, pagesize=paper.page, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm,
                         bottomMargin=22 * mm, title=info.title, author=APP_NAME, subject=info.kind,
                         creator=f"{APP_NAME} {__version__}")
        body = Frame(20 * mm, 20 * mm, W - 40 * mm, H - 42 * mm, id="body", leftPadding=0, rightPadding=0,
                     topPadding=0, bottomPadding=0)
        cover = Frame(0, 0, W, H, id="cover", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        sw, sh = paper.sheet
        sheet = Frame(0, 0, sw, sh, id="sheet", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([
            PageTemplate("cover", [cover], onPage=_kind("cover"), pagesize=paper.page),
            PageTemplate("body", [body], onPage=_kind("body"), pagesize=paper.page),
            PageTemplate("sheet", [sheet], onPage=_kind("sheet"), pagesize=paper.sheet)])
        self.body_width = W - 40 * mm
        self.body_height = H - 42 * mm

    def afterFlowable(self, f):
        e = getattr(f, "toc", None)
        if e:
            level, text, key = e
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (level, text, self.page, key))

    def make(self, story):
        self.multiBuild(story, canvasmaker=numbered_canvas(self.info))


def _kind(k):
    def on_page(canv, _doc):
        canv._kind = k
        if k != "sheet":
            canv._sheet_hdr = None
    return on_page


def contents(S) -> TableOfContents:
    toc = TableOfContents()
    toc.levelStyles = [S["toc0"], S["toc1"]]
    toc.dotsMinLevel = 0
    return toc


# ============================================================================ cover
class Cover(Flowable):
    """The plain cover (style B + C): office lines and logo, report number, amber bar, title, four key figures,
    a document control table and the navy footer band."""

    def __init__(self, info: DocInfo, size):
        super().__init__()
        self.info = info
        self.W, self.H = size

    def wrap(self, aw, ah):
        return self.W, self.H - 1

    def draw(self):
        c, info, W, H = self.canv, self.info, self.W, self.H
        S = styles()
        mx = 0.11 * W
        c.setFillColor(NAVY)
        c.rect(0, H - 8 * mm, W, 8 * mm, stroke=0, fill=1)
        top = H - 8 * mm - 16 * mm
        x = mx
        if info.logo and os.path.exists(info.logo):
            try:
                c.drawImage(info.logo, mx, top - 24 * mm, 24 * mm, 24 * mm, preserveAspectRatio=True, anchor="c",
                            mask="auto")
                x = mx + 28 * mm
            except Exception:
                pass
        lines = [ln for ln in info.office if ln.strip()][:3]
        y = top - 6
        for i, ln in enumerate(lines):
            last = i == len(lines) - 1
            c.setFont(SANS_B if last else SANS, 9.6 if last else 8.8)
            c.setFillColor(NAVY if last else INK2)
            c.drawString(x, y, _clip(ln, SANS_B, 9.6, W * 0.5))
            y -= 13
        c.setFont(MONO, 8)
        c.setFillColor(MUTED)
        ry = top - 6
        for ln in ([f"REPORT {info.report_no}"] if info.report_no else []) + \
                  ([f"REVISION {info.revision}"] if info.revision else []) + [info.date.upper()]:
            c.drawRightString(W - mx, ry, ln)
            ry -= 12
        # amber bar, kind, title, place
        y = H * 0.66
        c.setFillColor(AMBER)
        c.rect(mx, y, 34 * mm, 2.4 * mm, stroke=0, fill=1)
        y -= 9 * mm
        c.setFont(MONO_M, 9)
        c.setFillColor(AMBER_TXT)
        c.drawString(mx, y, info.kind.upper())
        y -= 4 * mm
        ts = ParagraphStyle("ct", fontName=HEAD_B, fontSize=30, leading=33, textColor=NAVY)
        p = Paragraph(esc(info.title), ts)
        _w, h = p.wrap(W - 2 * mx, H)
        p.drawOn(c, mx, y - h)
        y -= h + 5 * mm
        if info.location:
            sp = Paragraph(esc(info.location), ParagraphStyle("cs", fontName=SANS, fontSize=12.5, leading=16,
                                                              textColor=INK2))
            _w, h = sp.wrap(W - 2 * mx, H)
            sp.drawOn(c, mx, y - h)
            y -= h
        if info.facts:
            kf = key_figures(S, info.facts, W - 2 * mx)
            _w, h = kf.wrap(W - 2 * mx, H)
            kf.drawOn(c, mx, y - 12 * mm - h)
        # document control at the bottom, then the footer band
        rows = [(r, n, p_, d) for r, n, p_, d in info.control]
        if info.standard:
            rows.append(("Standard", Markup(esc(info.standard)), "", ""))
        if rows:
            hs = ParagraphStyle("dch", fontName=MONO_M, fontSize=6.6, leading=8, textColor=NAVY)
            cs = ParagraphStyle("dcc", fontName=SANS, fontSize=8.2, leading=10.4, textColor=INK)
            ms = ParagraphStyle("dcm", fontName=MONO, fontSize=7.8, leading=10.4, textColor=INK)
            data = [[Paragraph(t, hs) for t in ("DOCUMENT CONTROL", "NAME", "POSITION", "DATE")]]
            for r, n, p_, d in rows:
                data.append([Paragraph(esc(r), ParagraphStyle("dcr", parent=cs, textColor=MUTED)),
                             Paragraph(n if isinstance(n, Markup) else esc(n), cs), Paragraph(esc(p_), cs),
                             Paragraph(esc(d), ms)])
            cw = W - 2 * mx
            t = Table(data, colWidths=[cw * 0.24, cw * 0.32, cw * 0.28, cw * 0.16])
            st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                  ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 3.4),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6), ("LINEBELOW", (0, 0), (-1, 0), 1.2, NAVY),
                  ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE)]
            if info.standard:
                st.append(("SPAN", (1, len(data) - 1), (3, len(data) - 1)))
            t.setStyle(TableStyle(st))
            _w, h = t.wrap(cw, H)
            t.drawOn(c, mx, 14 * mm + 12 * mm)
        c.setFillColor(NAVY)
        c.rect(0, 0, W, 14 * mm, stroke=0, fill=1)
        c.setFillColor(AMBER)
        c.rect(0, 14 * mm, W, 1.8 * mm, stroke=0, fill=1)
        c.setFont(MONO, 7.4)
        c.setFillColor(HDR_TXT)
        c.drawString(mx, 5.6 * mm, footer_text())
        if info.survey:
            c.drawRightString(W - mx, 5.6 * mm, info.survey)


class Sheet(Flowable):
    """A whole landscape drawing sheet drawn by fn(canvas, width, height); hdr = (left, right) header texts."""

    def __init__(self, fn, size, hdr=None, toc_text=None):
        super().__init__()
        self.fn, (self.W, self.H), self.hdr = fn, size, hdr
        if toc_text:
            self.toc = (1, toc_text, f"s{id(self)}")

    def wrap(self, aw, ah):
        return self.W, self.H - 1

    def draw(self):
        self.canv._sheet_hdr = self.hdr
        self.canv.saveState()
        self.fn(self.canv, self.W, self.H)
        self.canv.restoreState()


def dms(v: float, pos: str, neg: str) -> str:
    h = pos if v >= 0 else neg
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    s = (v - d - m / 60) * 3600
    if s >= 59.995:
        s, m = 0.0, m + 1
    if m >= 60:
        m, d = 0, d + 1
    return f"{d}°{m:02d}′{s:05.2f}″ {h}"


def kst(s: float) -> str:
    """Station in the DPWH style, K0+060.000."""
    s = max(0.0, s)
    k = int(s // 1000)
    return f"K{k}+{s - 1000 * k:07.3f}"


__all__ = ["PAPERS", "Paper", "DocInfo", "ReportDoc", "Cover", "Sheet", "styles", "section", "table", "Tag",
           "key_figures", "signatures", "contents", "bullets", "Picture", "Markup", "TocMark", "dms", "kst", "esc",
           "jpeg_reader", "Color"]
