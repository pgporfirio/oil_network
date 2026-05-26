"""Render DESIGN_PRINCIPLES.md (axiom/corollary structure) to PDF.

Markdown -> HTML (via the `markdown` library, with table + fenced-code +
extra extensions) -> PDF (via `xhtml2pdf`). Pure Python; no system
dependencies. Output: outputs/docs/Design_Principles.pdf
"""
from __future__ import annotations

import markdown
from xhtml2pdf import pisa

from paths import CLAUDE_DIR, DOCS_DIR

SRC = CLAUDE_DIR / "DESIGN_PRINCIPLES.md"
OUT = DOCS_DIR / "Design_Principles.pdf"

CSS = """
@page { size: a4; margin: 2cm; }
body { font-family: "Helvetica", "Arial", sans-serif; font-size: 10pt;
       line-height: 1.35; color: #222; }
h1 { font-size: 18pt; color: #1a4d80; border-bottom: 2pt solid #1a4d80;
     padding-bottom: 4pt; margin-top: 16pt; }
h2 { font-size: 14pt; color: #1a4d80; margin-top: 14pt;
     border-bottom: 1pt solid #cccccc; padding-bottom: 2pt; }
h3 { font-size: 11pt; color: #2d5f8f; margin-top: 10pt; }
p  { margin: 4pt 0; text-align: justify; }
ul, ol { margin: 4pt 0 4pt 18pt; }
li { margin: 2pt 0; }
code { font-family: "Courier New", monospace; font-size: 9pt;
       background-color: #f4f4f4; padding: 1pt 3pt; border-radius: 2pt; }
pre  { font-family: "Courier New", monospace; font-size: 9pt;
       background-color: #f4f4f4; padding: 6pt; border-left: 3pt solid #1a4d80;
       margin: 6pt 0; }
table { border-collapse: collapse; margin: 6pt 0; font-size: 9pt; }
th, td { border: 1pt solid #888; padding: 3pt 6pt; text-align: left; }
th { background-color: #e6eef5; font-weight: bold; }
hr { border: none; border-top: 1pt solid #888; margin: 10pt 0; }
strong { color: #1a4d80; }
em { color: #555; }
"""


def main() -> None:
    md_text = SRC.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "extra"],
    )
    html = f"<html><head><style>{CSS}</style></head><body>{html_body}</body></html>"
    with open(OUT, "wb") as f:
        result = pisa.CreatePDF(html, dest=f)
    if result.err:
        raise RuntimeError(f"xhtml2pdf failed with {result.err} error(s)")
    print(f"Wrote {OUT}  ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
