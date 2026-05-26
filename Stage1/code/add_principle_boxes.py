"""Replace the inline "Principle N." bold callouts in v2.1 with proper boxed
callouts at the top of each Chapter 4 principle section (§4.1-§4.6).

Box format: single-cell table, light-grey shading, thin border, ~50pt padding.
Cell content: bold "Principle N." followed by the concise principle statement.

Writes v2.2.docx.
"""
from __future__ import annotations
from pathlib import Path
from copy import deepcopy

import docx
from docx.oxml.ns import qn, nsmap
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Cm

SRC = Path(__file__).resolve().parent.parent / "outputs" / "docs" / "Master_Thesis_Pedro_Porfirio_restructured_v2.0.docx"
OUT = SRC.with_name("Master_Thesis_Pedro_Porfirio_restructured_v2.2.docx")

PRINCIPLES = {
    1: ("Orthogonality",
        "Keep the physical layer and the data layer separate. "
        "Data updates must not change the graph; structural changes must not require data revisions."),
    2: ("Assets as nodes",
        "Every physical asset — refinery, pipeline, vessel, terminal, basin, gathering system — is a node "
        "carrying its own inventory and mass balance. Edges are flows; assets are not edge attributes."),
    3: ("Mass balance with B",
        "Every node satisfies ΔS = P + ΣF_in − C − ΣF_out + B. "
        "The balancing item B is a labelled, first-class variable — never an unlabelled adjustment column."),
    4: ("Observed or derived",
        "Every variable, in every scenario, is either observed (TS-bound) or derived (formula-bound) — never both. "
        "Enforced at the schema level by CHECK(num_nonnulls(timeseries_id, formula) = 1); inconsistency is unrepresentable."),
    5: ("Latent flows",
        "Flows the data does not observe — junction splits, fan-outs, in-transit volumes — are declared as "
        "latent variables constrained by mass balance and capacity, not back-filled by ad-hoc allocation rules. "
        "Latent is not missing."),
    6: ("Aggregation views",
        "Administrative aggregates (PADDs, refining districts, basin rollups) are views over physical nodes, not duplicate storage. "
        "Mixed-resolution data feeds in through these views without double-counting."),
}


def _set(elem, tag, **attrs):
    """Add a child element with namespaced attributes."""
    child = OxmlElement(tag)
    for k, v in attrs.items():
        # k may be e.g. 'w:val' or 'val'; coerce to fully-qualified
        if ":" not in k:
            k = f"w:{k}"
        child.set(qn(k), v)
    elem.append(child)
    return child


def build_box(principle_num: int, title: str, statement: str):
    """Build a one-row, one-cell shaded table with bold 'Principle N — Title.'
    header and the rule statement. Returns the <w:tbl> oxml element."""
    tbl = OxmlElement("w:tbl")

    # tblPr — properties
    tblPr = OxmlElement("w:tblPr")
    _set(tblPr, "w:tblW", **{"w:w": "5000", "w:type": "pct"})  # 100% width
    tblBorders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right"):
        _set(tblBorders, f"w:{side}", **{"w:val": "single", "w:sz": "6", "w:space": "0", "w:color": "999999"})
    tblPr.append(tblBorders)
    tblLook = OxmlElement("w:tblLook")
    tblLook.set(qn("w:val"), "04A0")
    tblPr.append(tblLook)
    tbl.append(tblPr)

    # tblGrid — single column
    tblGrid = OxmlElement("w:tblGrid")
    _set(tblGrid, "w:gridCol", **{"w:w": "9000"})
    tbl.append(tblGrid)

    # tr — one row
    tr = OxmlElement("w:tr")
    tc = OxmlElement("w:tc")

    # tcPr — cell properties (width, shading, margins)
    tcPr = OxmlElement("w:tcPr")
    _set(tcPr, "w:tcW", **{"w:w": "5000", "w:type": "pct"})
    _set(tcPr, "w:shd", **{"w:val": "clear", "w:color": "auto", "w:fill": "F2F2F2"})
    tcMar = OxmlElement("w:tcMar")
    for side in ("top", "left", "bottom", "right"):
        _set(tcMar, f"w:{side}", **{"w:w": "120", "w:type": "dxa"})
    tcPr.append(tcMar)
    tc.append(tcPr)

    # Paragraph inside cell
    p = OxmlElement("w:p")
    pPr = OxmlElement("w:pPr")
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:after"), "0")
    pPr.append(spacing)
    p.append(pPr)

    # Run 1: bold "Principle N — Title. "
    r1 = OxmlElement("w:r")
    rPr1 = OxmlElement("w:rPr")
    b = OxmlElement("w:b")
    rPr1.append(b)
    r1.append(rPr1)
    t1 = OxmlElement("w:t")
    t1.text = f"Principle {principle_num} — {title}. "
    t1.set(qn("xml:space"), "preserve")
    r1.append(t1)
    p.append(r1)

    # Run 2: the rule statement (regular)
    r2 = OxmlElement("w:r")
    t2 = OxmlElement("w:t")
    t2.text = statement
    t2.set(qn("xml:space"), "preserve")
    r2.append(t2)
    p.append(r2)

    tc.append(p)
    tr.append(tc)
    tbl.append(tr)
    return tbl


def main():
    d = docx.Document(str(SRC))
    body = d.element.body
    paragraphs = d.paragraphs

    added = 0
    for i, p in enumerate(paragraphs):
        style = p.style.name if p.style else ""
        if "Heading 2" not in style:
            continue
        txt = p.text.strip()
        try:
            num, _rest = txt.split(" ", 1)
            major, minor = num.split(".")
            minor = int(minor)
        except (ValueError, IndexError):
            continue
        if major != "4" or not (1 <= minor <= 6):
            continue

        # Build the box and insert it right after the heading paragraph.
        title, statement = PRINCIPLES[minor]
        box = build_box(minor, title, statement)
        # An empty paragraph after the box to separate it from the body prose.
        spacer = OxmlElement("w:p")

        heading_elem = p._element
        # addnext adds AFTER, so we add spacer first then box-before-spacer to
        # get order: heading, box, spacer, original-body. Use direct insertion
        # via parent for clarity.
        parent = heading_elem.getparent()
        idx = list(parent).index(heading_elem)
        parent.insert(idx + 1, box)
        parent.insert(idx + 2, spacer)
        added += 1

    d.save(str(OUT))
    print(f"Inserted {added} principle boxes")
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    main()
