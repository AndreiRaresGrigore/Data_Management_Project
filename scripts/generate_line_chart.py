"""
Generates the LINE chart (connected dots, log scale) from benchmark_final.py
results, to show the TREND of the PostgreSQL/Neo4j advantage across the
query sequence, from the simplest to the deepest.

Usage:
    python3 scripts/generate_line_chart.py
"""

import csv
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent   # repository root
INPUT_CSV = ROOT / "results" / "benchmark_final_results.csv"
OUTPUT_PNG = ROOT / "figures" / "final_line_chart.png"

LABEL_MAP = {
    "Q1_shows_by_genre": "Q1\nGenre",
    "Q2_cast_of_show": "Q2\nCast",
    "Q3_directors_who_also_acted": "Q3\nDirector-actors",
    "Q4_actors_at_3_hops": "Q4\n3 hops",
    "Q5a_degrees_of_separation_dist1": "Q5a\ndist=1",
    "Q5b_degrees_of_separation_dist2": "Q5b\ndist=2",
    "Q5c_degrees_of_separation_dist3": "Q5c\ndist=3",
    "Q5d_degrees_of_separation_dist4": "Q5d\ndist=4",
    "Q5e_degrees_of_separation_dist5": "Q5e\ndist=5",
}


def main():
    with open(INPUT_CSV, newline="") as f:
        rows = list(csv.DictReader(f))

    labels = [f'{LABEL_MAP.get(r["query"], r["query"])}\n{r["edges"]} edge{"s" if r["edges"] != "1" else ""}' for r in rows]
    pg_vals = [float(r["pg_warm_median_ms"]) for r in rows]
    neo_vals = [float(r["neo4j_warm_median_ms"]) for r in rows]
    pg_cold = [float(r["pg_cold_ms"]) for r in rows]
    neo_cold = [float(r["neo4j_cold_ms"]) for r in rows]

    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(13, 7))

    ax.plot(x, pg_vals, marker="o", markersize=9, linewidth=2.5,
            color="#336791", label="PostgreSQL (warm median)")
    ax.plot(x, neo_vals, marker="o", markersize=9, linewidth=2.5,
            color="#008CC1", label="Neo4j (warm median)")

    # discreet cold markers: darker/desaturated shade of each system's own
    # color, so they stay visually linked to their technology
    PG_COLD_COLOR = "#1a3a52"    # dark navy (PostgreSQL family)
    NEO4J_COLD_COLOR = "#004d66"  # dark teal (Neo4j family)
    ax.plot(x, pg_cold, linewidth=1, linestyle=":", color=PG_COLD_COLOR, alpha=0.5, zorder=1)
    ax.scatter(x, pg_cold, marker="D", s=30, color=PG_COLD_COLOR, alpha=0.6, zorder=2)
    ax.plot(x, neo_cold, linewidth=1, linestyle=":", color=NEO4J_COLD_COLOR, alpha=0.5, zorder=1)
    ax.scatter(x, neo_cold, marker="D", s=30, color=NEO4J_COLD_COLOR, alpha=0.6, zorder=2)

    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Execution time (ms, log scale)", fontsize=11)
    ax.set_title("Execution time trend across the query sequence",
                  fontsize=13)
    ax.grid(True, alpha=0.3, which="both")

    for xi, pg, neo in zip(x, pg_vals, neo_vals):
        ax.annotate(f"{pg:,.1f}", xy=(xi, pg), xytext=(0, 10),
                    textcoords="offset points", ha="center", fontsize=8.5,
                    color="#336791", fontweight="bold")
        ax.annotate(f"{neo:,.1f}", xy=(xi, neo), xytext=(0, -16),
                    textcoords="offset points", ha="center", fontsize=8.5,
                    color="#008CC1", fontweight="bold")

    # highlight the crossover point (where the lines cross)
    for i in range(len(x) - 1):
        if (pg_vals[i] - neo_vals[i]) * (pg_vals[i+1] - neo_vals[i+1]) < 0:
            ax.axvspan(i, i + 1, color="gray", alpha=0.12)

    from matplotlib.lines import Line2D
    cold_marker_pg = Line2D([0], [0], marker="D", color="w", markerfacecolor=PG_COLD_COLOR,
                             markeredgecolor=PG_COLD_COLOR, alpha=0.6, markersize=8,
                             label="PostgreSQL cold run")
    cold_marker_neo = Line2D([0], [0], marker="D", color="w", markerfacecolor=NEO4J_COLD_COLOR,
                              markeredgecolor=NEO4J_COLD_COLOR, alpha=0.6, markersize=8,
                              label="Neo4j cold run")
    handles, _ = ax.get_legend_handles_labels()
    ax.legend(handles=handles + [cold_marker_pg, cold_marker_neo], fontsize=10)

    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150)
    plt.close()
    print(f"Line chart saved to {OUTPUT_PNG}")


if __name__ == "__main__":
    main()