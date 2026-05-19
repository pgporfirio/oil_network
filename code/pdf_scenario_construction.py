"""Render SCENARIO_CONSTRUCTION.md to PDF with a generated figure.

Outputs:
  outputs/docs/figures/fig_scenario_construction.png   (matplotlib)
  outputs/docs/Scenario_Construction.pdf               (markdown -> xhtml2pdf)

Standalone reference document focused on how a scenario is constructed in
the asset-centric framework, organised around the axioms and corollaries
committed to DESIGN_PRINCIPLES.md.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from xhtml2pdf import pisa

from paths import CLAUDE_DIR, DOCS_DIR


SRC_MD = CLAUDE_DIR / "SCENARIO_CONSTRUCTION.md"
OUT_PDF = DOCS_DIR / "Scenario_Construction.pdf"
FIG_DIR = DOCS_DIR / "figures"
FIG_PATH = FIG_DIR / "fig_scenario_construction.png"


CSS = """
@page { size: a4; margin: 2cm; }
body { font-family: "Helvetica", "Arial", sans-serif; font-size: 10pt;
       line-height: 1.4; color: #222; }
h1 { font-size: 20pt; color: #1a4d80; border-bottom: 2pt solid #1a4d80;
     padding-bottom: 4pt; margin-top: 16pt; }
h2 { font-size: 14pt; color: #1a4d80; margin-top: 14pt;
     border-bottom: 1pt solid #cccccc; padding-bottom: 2pt; }
h3 { font-size: 11pt; color: #2d5f8f; margin-top: 10pt; }
p  { margin: 5pt 0; text-align: justify; }
ul, ol { margin: 5pt 0 5pt 18pt; }
li { margin: 2pt 0; }
code { font-family: "Courier New", monospace; font-size: 9pt;
       background-color: #f4f4f4; padding: 1pt 3pt; border-radius: 2pt; }
pre  { font-family: "Courier New", monospace; font-size: 9pt;
       background-color: #f4f4f4; padding: 6pt; border-left: 3pt solid #1a4d80;
       margin: 6pt 0; }
table { border-collapse: collapse; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1pt solid #888; padding: 4pt 7pt; text-align: left; vertical-align: top; }
th { background-color: #e6eef5; font-weight: bold; }
hr { border: none; border-top: 1pt solid #888; margin: 12pt 0; }
strong { color: #1a4d80; }
em { color: #555; }
img { display: block; margin: 8pt auto; max-width: 100%; }
"""


def build_figure(out_path: Path) -> None:
    """Five-stage scenario-construction sequence with axiom/corollary tags."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    out_path.parent.mkdir(parents=True, exist_ok=True)

    stages = [
        ("Stage 1\nAsset graph", "physical + abstract\nassets; nodes; edges",
         "Axiom 1, 2, 4, 6\nCorollary A, B"),
        ("Stage 2\nVariables", "P, C, F_in, F_out\nS, B, phi\nformula_inputs",
         "Axiom 3\nCorollary C"),
        ("Stage 3\nAssignments\n(per scenario)", "TS xor formula\nCHECK constraint\nlatent / zero / sum",
         "Axiom 5\nCorollary D"),
        ("Stage 4\nResolver", "9 dispatch rules\nLOCF for TS\n -> scenario_\n    resolved_values",
         "Corollary D-bis"),
        ("Stage 5\nLayered views\n(scenario state)", "v_flow_edges\nv_partition_tree\nv_node_status\nv_aggregation_consistency",
         "Corollary E"),
    ]

    fig, ax = plt.subplots(figsize=(13, 5.5), dpi=160)
    ax.set_xlim(0, 13.5)
    ax.set_ylim(0, 6.5)
    ax.axis("off")

    stage_w = 2.4
    stage_h = 3.2
    gap = 0.15
    x0 = 0.15

    palette = ["#E8F0F8", "#F0F8E8", "#F4ECF8", "#FFF6E0", "#FFE8E8"]

    for i, (title, body, tags) in enumerate(stages):
        x = x0 + i * (stage_w + gap)
        # Main stage box
        box = FancyBboxPatch((x, 1.7), stage_w, stage_h,
                             boxstyle="round,pad=0.06", linewidth=1.4,
                             edgecolor="#404060", facecolor=palette[i])
        ax.add_patch(box)
        ax.text(x + stage_w / 2, 1.7 + stage_h - 0.25, title,
                ha="center", va="top", fontsize=11, fontweight="bold", color="#202040")
        ax.text(x + stage_w / 2, 1.7 + stage_h - 1.3, body,
                ha="center", va="top", fontsize=8.8, color="#303050")

        # Axiom / corollary tag box below
        tag_box = FancyBboxPatch((x + 0.15, 0.55), stage_w - 0.3, 1.0,
                                  boxstyle="round,pad=0.04", linewidth=1.0,
                                  edgecolor="#406040", facecolor="#EEF6EE")
        ax.add_patch(tag_box)
        ax.text(x + stage_w / 2, 1.45, "Governing rule",
                ha="center", va="top", fontsize=8, fontweight="bold", color="#204020")
        ax.text(x + stage_w / 2, 1.20, tags,
                ha="center", va="top", fontsize=8.2, color="#304030")

    # Arrows between stages
    for i in range(len(stages) - 1):
        x_start = x0 + (i + 1) * stage_w + i * gap
        x_end = x_start + gap
        ax.annotate("", xy=(x_end, 3.3), xytext=(x_start, 3.3),
                    arrowprops=dict(arrowstyle="-|>", color="#404060", lw=1.8))

    # Title and footer
    ax.text(6.75, 6.2, "Scenario construction in five stages",
            ha="center", va="top", fontsize=14, fontweight="bold", color="#202040")
    ax.text(6.75, 0.2,
            "Persistent asset graph (Stage 1) is built once. "
            "Stages 2-5 turn it into a scenario by attaching variables, binding data, resolving, and exposing views.",
            ha="center", va="bottom", fontsize=8.5, fontstyle="italic", color="#404060")

    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    build_figure(FIG_PATH)
    md_text = SRC_MD.read_text(encoding="utf-8")

    # Inline the figure as a base64 data URI — xhtml2pdf has trouble resolving
    # Windows file:// URIs with URL-encoded spaces in the path.
    import base64
    png_b64 = base64.b64encode(FIG_PATH.read_bytes()).decode("ascii")
    data_uri = f"data:image/png;base64,{png_b64}"
    md_text = md_text.replace(
        "../outputs/docs/figures/fig_scenario_construction.png",
        data_uri,
    )

    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "extra"],
    )
    html = f"<html><head><style>{CSS}</style></head><body>{html_body}</body></html>"
    with open(OUT_PDF, "wb") as f:
        result = pisa.CreatePDF(html, dest=f, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"xhtml2pdf failed with {result.err} error(s)")
    size_kb = OUT_PDF.stat().st_size // 1024
    print(f"Wrote {OUT_PDF}  ({size_kb} KB)")
    print(f"Figure at {FIG_PATH}")


if __name__ == "__main__":
    main()
