"""Render Figure 3.1 — the crude-oil physical supply chain schematic.

Four left-to-right stages (upstream production -> midstream gathering &
transport -> storage -> downstream refining & products), each shown as a
labelled bucket with its principal asset types. Arrows show direction of
crude flow. This is the figure referenced by the caption in Section 3.1
of the thesis.

Output: outputs/docs/fig_3_1_physical_supply_chain.png
"""
from __future__ import annotations
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import os

OUT = os.path.join(os.path.dirname(__file__), '..', 'outputs', 'docs',
                   'fig_3_1_physical_supply_chain.png')

# Stage definitions: (title, items, fill colour)
STAGES = [
    ("UPSTREAM\nPRODUCTION",
     ["Onshore wells", "Offshore platforms", "Shale & tight oil"],
     "#dfe9f5"),
    ("MIDSTREAM\nGATHERING &\nTRANSPORT",
     ["Gathering systems", "Pipelines",
      "Marine tankers", "Rail & truck"],
     "#e8f3e1"),
    ("STORAGE",
     ["Commercial tank farms\n(Cushing, Patoka, Houston,\nSt. James, Nederland)",
      "Strategic Petroleum\nReserve (SPR)"],
     "#fdecd0"),
    ("DOWNSTREAM\nREFINING",
     ["Refineries", "(Refined products\nleave framework here)"],
     "#f5dee5"),
]


def render(out_path: str = OUT) -> str:
    # Aspect 1.654:1 matches the wp:extent of the existing inline image slot
    # in document.xml (Section 3.1), so the replaced figure is not distorted.
    fig, ax = plt.subplots(figsize=(11.0, 6.65))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis('off')

    # Title
    ax.text(50, 96,
            "The U.S. crude-oil physical supply chain",
            ha='center', va='top', fontsize=14, weight='bold')

    # Stage layout
    n = len(STAGES)
    margin_l, margin_r = 2.0, 2.0
    gap = 2.0
    usable = 100 - margin_l - margin_r - gap * (n - 1)
    box_w = usable / n
    box_top = 88
    box_bot = 12
    box_h = box_top - box_bot

    centres_x = []
    for i, (title, items, colour) in enumerate(STAGES):
        x = margin_l + i * (box_w + gap)
        # Stage container
        ax.add_patch(FancyBboxPatch(
            (x, box_bot), box_w, box_h,
            boxstyle="round,pad=0.6,rounding_size=1.4",
            linewidth=1.4, edgecolor="#444", facecolor=colour))
        # Stage title
        ax.text(x + box_w / 2, box_top - 5, title,
                ha='center', va='top', fontsize=11.5, weight='bold')
        # Items as a vertical bullet list
        item_top = box_top - 22
        item_step = (item_top - (box_bot + 6)) / max(len(items), 1)
        for j, item in enumerate(items):
            cy = item_top - j * item_step
            ax.text(x + box_w / 2, cy, item,
                    ha='center', va='center', fontsize=10)
        centres_x.append(x + box_w / 2)

    # Direction-of-flow arrows between stages
    arrow_y = (box_top + box_bot) / 2
    for i in range(n - 1):
        # arrow from right edge of i to left edge of i+1
        x_start = margin_l + i * (box_w + gap) + box_w + 0.2
        x_end = margin_l + (i + 1) * (box_w + gap) - 0.2
        ax.add_patch(FancyArrowPatch(
            (x_start, arrow_y), (x_end, arrow_y),
            arrowstyle='-|>', mutation_scale=18,
            linewidth=1.6, color="#555"))

    # Footer caption-style annotation
    ax.text(50, 5,
            "Crude flows left to right.   Each stage maps to a named asset class in the framework "
            "(production, gathering, pipeline, terminal, refinery).",
            ha='center', va='center', fontsize=9.5, style='italic', color='#444')

    plt.tight_layout(pad=0.4)
    fig.savefig(out_path, dpi=240, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return out_path


if __name__ == '__main__':
    p = render()
    print('wrote', p)
