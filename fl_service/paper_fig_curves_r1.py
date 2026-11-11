"""
Learning-curve figure for the revised Section 7 (JISAS-D-26-04548):
(a) mean validation F1 per FedAvg round, +-1 std over ten seeds, K = 2..6;
(b) mean local training loss per round.
Markers: the mean selected (best-validation) round of each K.

Run from fl_service/ after analyze_r1.py:
    python paper_fig_curves_r1.py --out <path>/fig_learning_curves.pdf
"""
import os
import csv
import json
import argparse
from collections import defaultdict
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# Ordinal single-hue ramp (light -> dark with K), validated for a light surface.
COLORS = {2: "#86b6ef", 3: "#5598e7", 4: "#2a78d6", 5: "#1c5cab", 6: "#104281"}
INK, MUTED, GRID = "#2b2b29", "#6b6a66", "#e4e3df"
MAX_ROUND = 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("results", "fig_learning_curves.pdf"))
    args = ap.parse_args()

    curves = defaultdict(list)
    with open(os.path.join("results", "r1_curves.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            curves[r["config"]].append({k: float(v) for k, v in r.items() if k != "config"})
    stats = json.load(open(os.path.join("results", "r1_stats.json"), encoding="utf-8"))

    plt.rcParams.update({"font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.linewidth": 0.6})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.6))
    for k in range(2, 7):
        rows = [r for r in curves[f"fedavg_k{k}"] if r["round"] <= MAX_ROUND]
        x = [r["round"] for r in rows]
        m = [r["val_f1_mean"] for r in rows]
        s = [r["val_f1_std"] for r in rows]
        c = COLORS[k]
        ax1.fill_between(x, [a - b for a, b in zip(m, s)], [a + b for a, b in zip(m, s)],
                         color=c, alpha=0.12, linewidth=0)
        ax1.plot(x, m, color=c, linewidth=1.4, label=f"K = {k}")
        best = round(stats["per_config"][f"fedavg_k{k}"]["best_round"])
        ax1.plot([best], [m[best - 1]], marker="o", markersize=4.5, color=c,
                 markeredgecolor="white", markeredgewidth=0.8, zorder=5)
        lx = [r["round"] for r in rows if r["n_runs"] >= 3]
        ly = [r["train_loss_mean"] for r in rows if r["n_runs"] >= 3]
        ax2.plot(lx, ly, color=c, linewidth=1.4, label=f"K = {k}")

    for ax, title, ylabel in ((ax1, "(a) Validation F1 (mean over facilities)", "F1"),
                              (ax2, "(b) Local training loss", "Weighted BCE loss")):
        ax.set_title(title, fontsize=8, color=INK, loc="left")
        ax.set_xlabel("Federated round")
        ax.set_ylabel(ylabel)
        ax.set_xlim(1, MAX_ROUND)
        ax.set_xticks([1, 5, 10, 15, 20])
        ax.grid(True, color=GRID, linewidth=0.5)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    ax1.legend(frameon=False, fontsize=7, ncol=1, loc="upper right", labelcolor=INK)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fig.savefig(args.out)
    fig.savefig(os.path.splitext(args.out)[0] + ".png", dpi=200)
    print("saved", args.out)


if __name__ == "__main__":
    main()
