"""
Generates the GROUPED BAR chart (log scale) from benchmark_final.py results.

Usage:
    python3 scripts/generate_bar_chart.py
"""

import csv
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent   # repository root
INPUT_CSV = ROOT / "results" / "benchmark_final_results.csv"
OUTPUT_PNG = ROOT / "figures" / "final_bar_chart.png"

LABEL_MAP = {
    "Q1_shows_by_genre": "Q1 – Shows\nby genre",
    "Q2_cast_of_show": "Q2 – Cast\nof a show",
    "Q3_directors_who_also_acted": "Q3 – Directors\nwho also acted",
    "Q4_actors_at_3_hops": "Q4 – Actors\nat 3 hops",
    "Q5a_degrees_of_separation_dist1": "Q5a – Degrees\nof sep. (dist. 1)",
    "Q5b_degrees_of_separation_dist2": "Q5b – Degrees\nof sep. (dist. 2)",
    "Q5c_degrees_of_separation_dist3": "Q5c – Degrees\nof sep. (dist. 3)",
    "Q5d_degrees_of_separation_dist4": "Q5d – Degrees\nof sep. (dist. 4)",
    "Q5e_degrees_of_separation_dist5": "Q5e – Degrees\nof sep. (dist. 5)",
}

def main():
    with open(INPUT_CSV, newline="") as f:
        rows = list(csv.DictReader(f))

    labels = [f'{LABEL_MAP.get(r["query"], r["query"])}\n({r["edges"]} edge{"s" if r["edges"] != "1" else ""})' for r in rows]
    pg_vals = [float(r["pg_warm_median_ms"]) for r in rows]
    neo_vals = [float(r["neo4j_warm_median_ms"]) for r in rows]
    pg_cold = [float(r["pg_cold_ms"]) for r in rows]
    neo_cold = [float(r["neo4j_cold_ms"]) for r in rows]

    x = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(14, 7))

    bars1 = ax.bar(x - width/2, pg_vals, width, label="PostgreSQL (warm median)", color="#336791")
    bars2 = ax.bar(x + width/2, neo_vals, width, label="Neo4j (warm median)", color="#008CC1")

    # discreet cold markers: darker/desaturated shade of each system's own
    # color, so they stay visually linked to their technology while
    # remaining clearly distinct from the warm bars
    PG_COLD_COLOR = "#1a3a52"    # dark navy (PostgreSQL family)
    NEO4J_COLD_COLOR = "#004d66"  # dark teal (Neo4j family)
    ax.scatter(x - width/2, pg_cold, marker="D", s=35, color=PG_COLD_COLOR, alpha=0.6, zorder=4)
    ax.scatter(x + width/2, neo_cold, marker="D", s=35, color=NEO4J_COLD_COLOR, alpha=0.6, zorder=4)

    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Execution time (ms, log scale)", fontsize=11)
    ax.set_title("Warm execution time comparison, PostgreSQL vs Neo4j", fontsize=13)
    ax.grid(axis="y", alpha=0.3, which="both")


    for bars in (bars1, bars2):
        for b in bars:
            h = b.get_height()
            ax.annotate(f"{h:,.1f}", xy=(b.get_x() + b.get_width()/2, h),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", fontsize=8)

    from matplotlib.lines import Line2D
    cold_marker_pg = Line2D([0], [0], marker="D", color="w", markerfacecolor=PG_COLD_COLOR,
                             markeredgecolor=PG_COLD_COLOR, alpha=0.6, markersize=8,
                             label="PostgreSQL cold run")
    cold_marker_neo = Line2D([0], [0], marker="D", color="w", markerfacecolor=NEO4J_COLD_COLOR,
                              markeredgecolor=NEO4J_COLD_COLOR, alpha=0.6, markersize=8,
                              label="Neo4j cold run")
    handles, _ = ax.get_legend_handles_labels()
    ax.legend(handles=handles + [cold_marker_pg, cold_marker_neo], fontsize=9.5)

    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150)
    plt.close()
    print(f"Bar chart saved to {OUTPUT_PNG}")


if __name__ == "__main__":
    main()