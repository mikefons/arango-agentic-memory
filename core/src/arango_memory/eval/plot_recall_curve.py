"""Render the HX-2 recall-vs-corpus-size curve from a `recall_curve` CSV.

Kept separate from the harness so plotting has no place on any hot path and matplotlib stays an
optional dependency (lazy-imported here). The CSV is the durable artifact; this just draws it.

    python -m arango_memory.eval.plot_recall_curve curve.csv curve.png
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

#: Fixed draw order + colourblind-safe colours (Okabe–Ito): fusion the hero line.
_ARM_ORDER = ["fused", "vector", "bm25"]
_ARM_COLOR = {"fused": "#0072B2", "vector": "#D55E00", "bm25": "#009E73"}
_ARM_LABEL = {"fused": "fused (graph+vector+BM25)", "vector": "vector-only", "bm25": "BM25-only"}


def load_series(csv_path: str | Path) -> dict[str, list[tuple[int, float]]]:
    """CSV rows → {arm: [(corpus_size, recall_frac), …]} sorted by size."""
    series: dict[str, list[tuple[int, float]]] = defaultdict(list)
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            series[row["arm"]].append((int(row["corpus_size"]), float(row["recall_frac"])))
    return {arm: sorted(points) for arm, points in series.items()}


def render(csv_path: str | Path, out_path: str | Path) -> Path:
    """Draw the curve to a PNG/SVG. Raises a clear error if matplotlib isn't installed."""
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless; no display needed
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise SystemExit(
            "matplotlib is required to render the plot: `uv pip install matplotlib` "
            "(the CSV from recall_curve is the data; plotting is optional)."
        ) from exc

    series = load_series(csv_path)
    fig, ax = plt.subplots(figsize=(8, 5))
    for arm in _ARM_ORDER:
        pts = series.get(arm)
        if not pts:
            continue
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        ax.plot(
            xs, ys, marker="o", linewidth=2.2 if arm == "fused" else 1.6,
            color=_ARM_COLOR[arm], label=_ARM_LABEL[arm],
        )
    ax.set_xlabel("corpus size (memories in one tenant)")
    ax.set_ylabel("recall-frac (fixed probe set)")
    ax.set_ylim(0, 1)
    ax.set_title("Recall vs corpus size — fusion holds, vector-only degrades")
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    fig.tight_layout()
    out = Path(out_path)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arango_memory.eval.plot_recall_curve")
    parser.add_argument("csv", help="a recall_curve CSV")
    parser.add_argument("out", help="output image path (.png or .svg)")
    args = parser.parse_args(argv)
    out = render(args.csv, args.out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
