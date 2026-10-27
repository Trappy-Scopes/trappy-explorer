"""
ReportLab rendering backend for :class:`explorer.report.ExpReport`.

ReportLab is pure Python (`py3-none-any`, no compiled extensions), so unlike the
pandoc backend it installs with pip alone -- no pandoc binary, no TeX, nothing
from apt. That is what makes the report usable on a Raspberry Pi scope node.

It renders the report's Markdown, which the report generates itself, so the
parser here only has to handle the constructs that generator emits: headings,
`- **key:** value` bullets, pipe tables, images, block quotes and italic notes.
It is deliberately not a general Markdown implementation.

The theme is the same one the LaTeX backend draws: dark bands top and foot, the
wordmark and cell mark in the header, page number and eid in the footer. Here it
is a `PageTemplate(onPage=...)` callback, which is the mechanism the original
`report.py` reached for before the RML detour -- RML is ReportLab PLUS's
commercial input language and the open-source package cannot read it, so nothing
in this module goes near it.
"""

from __future__ import annotations

import os
import re

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether,
                                LongTable, PageTemplate, Paragraph, Spacer,
                                TableStyle)

from .report import SPAN_MARKER

__all__ = ["render", "SPAN_MARKER"]

RULE = HexColor("#d5dbe0")
ZEBRA = HexColor("#f4f6f8")
MUTED = HexColor("#44505c")


def _fonts():
    """
    Register a Unicode face if one can be found.

    ReportLab's built-in fonts are Latin-1, and the report emits em dashes,
    ellipses and middle dots. DejaVu ships with matplotlib, which is already an
    optional dependency for the plots; when it is absent we fall back to
    Helvetica and strip the handful of characters it cannot encode.
    """
    try:
        import matplotlib
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        root = os.path.join(os.path.dirname(matplotlib.__file__),
                            "mpl-data", "fonts", "ttf")
        faces = {"": "DejaVuSans.ttf", "B": "DejaVuSans-Bold.ttf",
                 "I": "DejaVuSans-Oblique.ttf", "M": "DejaVuSansMono.ttf"}
        paths = {k: os.path.join(root, v) for k, v in faces.items()}
        if not all(os.path.exists(p) for p in paths.values()):
            raise ImportError
        for suffix, path in paths.items():
            pdfmetrics.registerFont(TTFont("DejaVu" + suffix, path))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVuB",
                                      italic="DejaVuI", boldItalic="DejaVuB")
        return "DejaVu", "DejaVuB", "DejaVuI", True
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False


#: Courier is a Latin-1 core font with no box-drawing glyphs, so a tree drawn
#: with them comes out as a row of black blocks. Fall back to ASCII joints.
_ASCII_TREE = {"\u251c": "|", "\u2514": "`", "\u2500": "-", "\u2502": "|"}


_LATIN1_SAFE = {"—": "--", "–": "-", "…": "...", "·": "-",
                "‘": "'", "’": "'", "“": '"', "”": '"'}


def _ascii_fallback(text):
    for bad, good in _LATIN1_SAFE.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


#: Rich's highlighter, toned down for print: one muted hue for quantities and
#: one for clock times, rather than Rich's saturated terminal palette.
NUMBER_INK = "#1f6f8b"
TIME_INK = "#8a6d3b"
_TIME_RE = re.compile(r"(?<![\w:])(\d{1,3}:\d{2}:\d{2})(?![\w:])")
_NUMBER_RE = re.compile(
    r"(?<![\w.:#-])("
    r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"   # 256, 1.18, 1.18e+06
    r")(?![\w.:])")


def _highlight(text):
    """Colour quantities and clock times the way Rich colours its output."""
    text = _TIME_RE.sub(rf'<font color="{TIME_INK}">\1</font>', text)
    return _NUMBER_RE.sub(rf'<font color="{NUMBER_INK}">\1</font>', text)


def _inline(text, unicode_ok=True, highlight=True):
    """Markdown inline markup -> ReportLab's paragraph mini-markup."""
    if not unicode_ok:
        text = _ascii_fallback(text)
    text = text.replace("\\|", "|").replace("\\\\", "\\")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if highlight:
        text = _highlight(text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="7.5">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])_([^_]+)_(?![\w*])", r"<i>\1</i>", text)
    return text


def _split_row(line):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _is_separator(line):
    return bool(re.match(r"^\|[\s:|-]+\|$", line.strip()))


def render(markdown_text, path, ctx):
    """
    Render `markdown_text` to a themed PDF at `path`.

    `ctx` carries the theme: band rgb and heights, logo paths, eid and title.
    """
    regular, bold, italic, unicode_ok = _fonts()
    mono = "DejaVuM" if unicode_ok else "Courier"
    band = Color(*[c / 255.0 for c in ctx["band_rgb"]])
    head_pt, foot_pt = ctx["header_pts"], ctx["footer_pts"]
    page_w, page_h = A4
    margin = 46.0
    frame_w = page_w - 2 * margin

    ss = getSampleStyleSheet()
    st_title = ParagraphStyle("t", parent=ss["Title"], fontName=bold, fontSize=26,
                              leading=30, spaceAfter=2, textColor=HexColor("#0f1419"))
    st_sub = ParagraphStyle("s", parent=ss["Title"], fontName=regular, fontSize=9.5,
                            leading=12.5, textColor=MUTED, spaceAfter=16)
    st_h2 = ParagraphStyle("h2", fontName=bold, fontSize=14, leading=17,
                           spaceBefore=16, spaceAfter=6, textColor=HexColor("#0f1419"))
    st_h3 = ParagraphStyle("h3", fontName=bold, fontSize=10.5, leading=13,
                           spaceBefore=10, spaceAfter=4, textColor=MUTED)
    st_h4 = ParagraphStyle("h4", fontName=bold, fontSize=9, leading=11,
                           spaceBefore=8, spaceAfter=3, textColor=MUTED)
    st_body = ParagraphStyle("b", fontName=regular, fontSize=8.5, leading=11.5,
                             spaceAfter=3)
    st_note = ParagraphStyle("n", parent=st_body, fontName=italic, textColor=MUTED)
    st_quote = ParagraphStyle("q", parent=st_body, leftIndent=10, borderPadding=4,
                              textColor=HexColor("#7a5c00"), backColor=HexColor("#fffbf0"))
    st_cell = ParagraphStyle("c", fontName=regular, fontSize=7.5, leading=9.5)
    st_mono = ParagraphStyle("m", fontName=mono, fontSize=7.0, leading=8.6,
                             leftIndent=6, spaceBefore=2, spaceAfter=6,
                             textColor=HexColor("#22303c"))
    st_head = ParagraphStyle("ch", fontName=bold, fontSize=7.5, leading=9.5,
                             textColor=white)

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(band)
        # a hair of overlap: exact abutment leaves a light seam when rasterised
        canvas.rect(0, page_h - head_pt, page_w, head_pt + 1, fill=1, stroke=0)
        logo = ctx.get("logo")
        if logo and os.path.exists(logo):
            # Inset by 2pt: drawn flush to the band edge, any rounding in the
            # rasteriser leaves a pale seam along the bottom of the image.
            w, h = ImageReader(logo).getSize()
            lh = head_pt - 4
            canvas.drawImage(logo, 18, page_h - head_pt + 2, height=lh,
                             width=lh * w / h, mask="auto")
        icon = ctx.get("icon")
        if icon and os.path.exists(icon):
            w, h = ImageReader(icon).getSize()
            ih = head_pt * ctx.get("icon_scale", 0.62)
            canvas.drawImage(icon, page_w - 22 - ih * w / h,
                             page_h - head_pt + (head_pt - ih) / 2,
                             height=ih, width=ih * w / h, mask="auto")
        canvas.setFillColor(band)
        canvas.rect(0, 0, page_w, foot_pt, fill=1, stroke=0)
        canvas.setFillColor(white)
        canvas.setFont(regular, 6.5)
        canvas.drawCentredString(page_w / 2, foot_pt / 2 - 2, str(canvas.getPageNumber()))
        canvas.drawRightString(page_w - 22, foot_pt / 2 - 2, f"eid: {ctx['eid']}")
        canvas.setFont(regular, 4.5)
        canvas.drawString(18, foot_pt / 2 - 2, f"Autogenerated: {ctx['stamp']}")
        canvas.restoreState()

    doc = BaseDocTemplate(path, pagesize=A4, leftMargin=margin, rightMargin=margin,
                          topMargin=head_pt + 20, bottomMargin=foot_pt + 18,
                          title=ctx.get("title"), author="Trappy-Scopes")
    doc.addPageTemplates([PageTemplate(
        id="main",
        frames=[Frame(margin, foot_pt + 18, frame_w,
                      page_h - head_pt - foot_pt - 38, id="body")],
        onPage=decorate)])

    def make_table(rows):
        header, body = rows[0], rows[1:]
        ncols = len(header)
        spans = [i for i, r in enumerate(rows)
                 if r and r[0].startswith(SPAN_MARKER)]
        rows = [([r[0][len(SPAN_MARKER):]] + list(r[1:])) if i in spans else r
                for i, r in enumerate(rows)]
        header, body = rows[0], rows[1:]
        # width by longest cell, clamped, then scaled to the frame -- banner rows
        # are excluded, since the whole point is that they do not set widths
        sized = [r for i, r in enumerate(rows) if i not in spans]
        raw = [max(len(r[i]) for r in sized) for i in range(ncols)]
        raw = [min(max(w, 4), 60) for w in raw]
        total = float(sum(raw)) or 1.0
        widths = [frame_w * w / total for w in raw]
        # A 60-char File column next to a 7-char Elapsed one squeezes the latter
        # until its own header wraps ("Elapse/d"). Give every column at least the
        # width its header text actually measures, and take the difference back
        # off the widest one.
        from reportlab.pdfbase.pdfmetrics import stringWidth
        for i, width in enumerate(widths):
            needed = stringWidth(header[i], bold, 7.5) + 10
            if width < needed and ncols > 1:
                widest = max(range(ncols), key=lambda j: widths[j])
                if widths[widest] - (needed - width) > needed:
                    widths[widest] -= needed - width
                    widths[i] = needed
        data = [[Paragraph(_inline(c, unicode_ok), st_head) for c in header]]
        data += [[Paragraph(_inline(c, unicode_ok), st_cell) for c in r] for r in body]
        table = LongTable(data, colWidths=widths, repeatRows=1)
        extra = []
        for row in spans:
            extra += [("SPAN", (0, row), (-1, row)),
                      ("BACKGROUND", (0, row), (-1, row), HexColor("#e8edf1")),
                      ("TEXTCOLOR", (0, row), (-1, row), HexColor("#22303c"))]
        table.setStyle(TableStyle(extra + [
            ("BACKGROUND", (0, 0), (-1, 0), band),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, ZEBRA]),
            ("GRID", (0, 0), (-1, -1), 0.25, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        return table

    story, pending, bullets, fence = [], [], [], []
    state = {"after_title": False, "in_fence": False}

    def flush_table():
        if pending:
            story.append(make_table(list(pending)))
            story.append(Spacer(1, 6))
            pending.clear()

    def flush_bullets():
        if bullets:
            for item in bullets:
                story.append(Paragraph("• " + item if unicode_ok else "- " + item,
                                       st_body))
            story.append(Spacer(1, 6))
            bullets.clear()

    for line in markdown_text.splitlines():
        stripped = line.strip()

        # Fenced blocks hold pre-drawn ASCII (the device tree): keep every space.
        if stripped.startswith("```"):
            if state["in_fence"]:
                lines = fence if unicode_ok else [
                    "".join(_ASCII_TREE.get(ch, ch) for ch in l) for l in fence]
                block = "<br/>".join(
                    _inline(l, unicode_ok, highlight=False).replace(" ", "&nbsp;")
                    for l in lines)
                story.append(Paragraph(block, st_mono))
                fence.clear()
            state["in_fence"] = not state["in_fence"]
            continue
        if state["in_fence"]:
            fence.append(line.rstrip())
            continue

        if stripped.startswith("|"):
            flush_bullets()
            if not _is_separator(stripped):
                pending.append(_split_row(stripped))
            continue
        flush_table()

        if not stripped:
            flush_bullets()
            continue

        image = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if image:
            flush_bullets()
            src = image.group(2)
            if os.path.exists(src):
                iw, ih = ImageReader(src).getSize()
                width = min(frame_w, iw * 0.75)
                story.append(Spacer(1, 4))
                story.append(Image(src, width=width, height=width * ih / iw))
                story.append(Spacer(1, 8))
            continue

        if stripped.startswith("- "):
            bullets.append(_inline(stripped[2:], unicode_ok))
            continue
        flush_bullets()

        if stripped.startswith("#### "):
            story.append(Paragraph(_inline(stripped[5:], unicode_ok, False), st_h4))
        elif stripped.startswith("### "):
            story.append(Paragraph(_inline(stripped[4:], unicode_ok, False), st_h3))
        elif stripped.startswith("## "):
            # The heading straight after the title is the experiment's name, and
            # these names are long enough to wrap at heading size. Set it as a
            # subtitle instead.
            style = st_sub if state["after_title"] else st_h2
            state["after_title"] = False
            story.append(Paragraph(_inline(stripped[3:], unicode_ok, False), style))
        elif stripped.startswith("# "):
            story.append(Paragraph(_inline(stripped[2:], unicode_ok, False), st_title))
            state["after_title"] = True
        elif stripped.startswith("> "):
            story.append(Paragraph(_inline(stripped[2:], unicode_ok), st_quote))
        elif stripped.startswith("_") and stripped.endswith("_"):
            story.append(Paragraph(_inline(stripped.strip("_"), unicode_ok), st_note))
        else:
            state["after_title"] = False
            story.append(Paragraph(_inline(stripped, unicode_ok), st_body))

    flush_table()
    flush_bullets()
    doc.build(story)
    return path
