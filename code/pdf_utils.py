"""Shared helpers for the three PDF generators (design principles,
resolver walkthrough, graph construction).

Uses reportlab's Platypus framework: flowables added to a SimpleDocTemplate.
Cover page + headings + body + code blocks + tables, neutral thesis-style
typography. No external assets required.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Paragraph, Spacer, PageBreak, Table, TableStyle, SimpleDocTemplate,
    KeepTogether, Preformatted,
)


def build_styles():
    base = getSampleStyleSheet()
    out = {}

    out["title"] = ParagraphStyle(
        "Title", parent=base["Title"], fontName="Times-Bold", fontSize=22,
        leading=26, spaceAfter=12,
    )
    out["subtitle"] = ParagraphStyle(
        "Subtitle", parent=base["Normal"], fontName="Times-Italic", fontSize=13,
        leading=16, spaceAfter=24, textColor=colors.HexColor("#444"),
    )
    out["meta"] = ParagraphStyle(
        "Meta", parent=base["Normal"], fontName="Times-Roman", fontSize=10,
        leading=12, textColor=colors.HexColor("#666"), spaceAfter=4,
    )
    out["h1"] = ParagraphStyle(
        "H1", parent=base["Heading1"], fontName="Times-Bold", fontSize=16,
        leading=20, spaceBefore=18, spaceAfter=8,
        textColor=colors.HexColor("#1a1f2c"),
    )
    out["h2"] = ParagraphStyle(
        "H2", parent=base["Heading2"], fontName="Times-Bold", fontSize=13,
        leading=16, spaceBefore=14, spaceAfter=6,
        textColor=colors.HexColor("#2a3050"),
    )
    out["h3"] = ParagraphStyle(
        "H3", parent=base["Heading3"], fontName="Times-Italic", fontSize=11,
        leading=14, spaceBefore=10, spaceAfter=4,
        textColor=colors.HexColor("#444"),
    )
    out["body"] = ParagraphStyle(
        "Body", parent=base["BodyText"], fontName="Times-Roman", fontSize=11,
        leading=14.5, alignment=TA_JUSTIFY, spaceAfter=8,
    )
    out["body_left"] = ParagraphStyle(
        "BodyLeft", parent=out["body"], alignment=TA_LEFT,
    )
    out["bullet"] = ParagraphStyle(
        "Bullet", parent=out["body"], leftIndent=18, bulletIndent=6,
        spaceAfter=4, leading=14,
    )
    out["code"] = ParagraphStyle(
        "Code", parent=base["Code"], fontName="Courier", fontSize=9,
        leading=11.5, leftIndent=12, rightIndent=12, spaceBefore=4,
        spaceAfter=10, textColor=colors.HexColor("#222"),
        backColor=colors.HexColor("#f3f3f0"), borderColor=colors.HexColor("#ddd"),
        borderWidth=0.4, borderPadding=6,
    )
    out["caption"] = ParagraphStyle(
        "Caption", parent=base["Normal"], fontName="Times-Italic", fontSize=9,
        leading=11, alignment=TA_LEFT, spaceAfter=10,
        textColor=colors.HexColor("#555"),
    )
    return out


def cover(title, subtitle, doc_title, version, styles):
    """Return a list of flowables for the cover page."""
    today = date.today().isoformat()
    return [
        Spacer(1, 6 * cm),
        Paragraph(title, styles["title"]),
        Paragraph(subtitle, styles["subtitle"]),
        Spacer(1, 1 * cm),
        Paragraph(
            "Master&rsquo;s Thesis &mdash; "
            "<i>Asset-Centric Temporal Graphs for Crude-Oil Logistics: "
            "Schema Design and Consistency Guarantees</i>",
            styles["meta"]),
        Paragraph("Pedro Porfirio &middot; NOVA IMS &middot; Universidade Nova de Lisboa",
                  styles["meta"]),
        Paragraph(f"Supervisor: Professor Flavio Pinheiro", styles["meta"]),
        Spacer(1, 2 * cm),
        Paragraph(f"Document: <b>{doc_title}</b> &middot; v{version} &middot; {today}",
                  styles["meta"]),
        PageBreak(),
    ]


def heading(text, level, styles):
    return Paragraph(text, styles[f"h{level}"])


def body(text, styles):
    return Paragraph(text, styles["body"])


def body_left(text, styles):
    return Paragraph(text, styles["body_left"])


def bullets(items, styles, prefix="&bull;"):
    return [Paragraph(f"{prefix} {it}", styles["bullet"]) for it in items]


def code(text, styles):
    return Preformatted(text, styles["code"])


def kv_table(rows, styles, col_widths=(4 * cm, 12 * cm)):
    """Two-column table for key/value rows. Keys are right-aligned italics."""
    tdata = [[Paragraph(f"<i>{k}</i>", styles["body_left"]),
              Paragraph(v, styles["body_left"])] for k, v in rows]
    t = Table(tdata, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def data_table(header, rows, styles, col_widths=None):
    tdata = [[Paragraph(f"<b>{h}</b>", styles["body_left"]) for h in header]]
    for row in rows:
        tdata.append([Paragraph(c, styles["body_left"]) if isinstance(c, str)
                      else c for c in row])
    t = Table(tdata, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1f2c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#1a1f2c")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#aaa")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build_pdf(out_path, flowables, header_text=""):
    """Write the PDF. Includes a footer with page number + optional header."""
    def _on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Times-Italic", 8)
        canvas.setFillColor(colors.HexColor("#888"))
        if header_text:
            canvas.drawString(2 * cm, A4[1] - 1.2 * cm, header_text)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm,
                               f"{doc.page}")
        canvas.restoreState()

    out_path = Path(out_path)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=header_text, author="Pedro Porfirio",
    )
    doc.build(flowables, onFirstPage=_on_page, onLaterPages=_on_page)
    return out_path
