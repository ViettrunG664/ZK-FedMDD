"""
Statistics for the multi-seed runs of benchmark_seeds.py (results/r1_runs.jsonl).

Per configuration: mean +/- sample std and a t-based 95% CI over seeds for
accuracy, F1, precision, sensitivity, specificity and ROC-AUC; the summed
confusion matrix and the review cost of the operating point; the per-shard
train/validation accuracy gap at the selected round.
Tests (paired over seeds, Holm-corrected within each family):
  - FedAvg K=2..6 vs centralized (paired t and Wilcoxon signed-rank)
  - trend of FedAvg accuracy / F1 / AUC with K (linear regression over all runs)
  - FedProx vs FedAvg at the same K, non-IID FedProx vs FedAvg, shift local vs global
Also writes the mean validation-F1 learning curve per configuration.

Run from the fl_service/ directory:
    python analyze_r1.py [--in results/r1_runs.jsonl]
Output: results/r1_stats.json, results/r1_curves.csv
"""
import os
import csv
import json
import argparse
from collections import defaultdict
from statistics import mean, stdev

import numpy as np
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

METRICS = [("accuracy", "Accuracy"), ("f1", "F1-Score"), ("precision", "Precision"),
           ("sensitivity", "Recall"), ("specificity", "Specificity"), ("auc", "AUC")]


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def ci95(xs):
    if len(xs) < 2:
        return [xs[0], xs[0]]
    half = stats.t.ppf(0.975, len(xs) - 1) * stdev(xs) / np.sqrt(len(xs))
    return [mean(xs) - half, mean(xs) + half]


def holm(pvals):
    """Holm step-down adjusted p-values, same order as the input."""
    order = np.argsort(pvals)
    adj, running = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = running
    return adj


def summarize(rows):
    out = {"n_seeds": len(rows), "seeds": sorted(r["seed"] for r in rows)}
    for key, field in METRICS:
        xs = [r[field] for r in rows]
        out[key] = {"mean": mean(xs), "std": stdev(xs) if len(xs) > 1 else 0.0, "ci95": ci95(xs)}
    tn, fp, fn, tp = np.sum([s["cm"] for r in rows for s in r["PerSite"]], axis=0).tolist()
    n = tn + fp + fn + tp
    out["cm_sum"] = [tn, fp, fn, tp]
    # Review cost: how many flagged participants a clinician must review per true case found
    out["review"] = {
        "false_pos_per_true_pos": fp / tp if tp else None,
        "flagged_per_100_screened": 100 * (tp + fp) / n,
        "ppv": tp / (tp + fp) if tp + fp else None,
    }
    gaps = []
    for r in rows:
        if r.get("History") and isinstance(r["History"], list) and "sites" in r["History"][0]:
            at_best = r["History"][r["Best Round"] - 1]["sites"]
            gaps.append(mean(s["train_acc"] - s["val_acc"] for s in at_best))
    if gaps:
        out["train_val_gap"] = {"mean": mean(gaps), "std": stdev(gaps) if len(gaps) > 1 else 0.0}
        out["best_round"] = mean(r["Best Round"] for r in rows)
    return out


def paired(a_rows, b_rows, field):
    """Paired comparison a - b over the seeds present in both."""
    a = {r["seed"]: r[field] for r in a_rows}
    b = {r["seed"]: r[field] for r in b_rows}
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return None
    d = [a[s] - b[s] for s in common]
    t = stats.ttest_rel([a[s] for s in common], [b[s] for s in common])
    w = stats.wilcoxon(d) if any(d) else None
    return {"n": len(common), "mean_diff": mean(d), "ci95_diff": ci95(d),
            "t_p": float(t.pvalue), "wilcoxon_p": float(w.pvalue) if w else 1.0}


def family(pairs, field):
    """Paired tests for a list of (label, a_rows, b_rows), Holm-corrected together."""
    res = {label: paired(a, b, field) for label, a, b in pairs}
    valid = [k for k, v in res.items() if v]
    for key in ("t_p", "wilcoxon_p"):
        adj = holm([res[k][key] for k in valid])
        for k, p in zip(valid, adj):
            res[k][key + "_holm"] = p
    return res


def ablation(rows):
    """Table 7: per configuration, the K-averaged metric of every seed -> mean/std over seeds;
    split vs full paired over (seed, K) within each partition, Holm-corrected across partitions."""
    by_cfg = defaultdict(list)
    for r in rows:
        by_cfg[r["config"]].append(r)
    out = {"per_config": {}, "split_vs_full": {}}
    for c, rs in sorted(by_cfg.items()):
        seeds = sorted({r["seed"] for r in rs})
        out["per_config"][c] = {"n_runs": len(rs), "seeds": seeds, "Ks": sorted({r["K"] for r in rs})}
        for key, field in METRICS:
            per_seed = [mean(r[field] for r in rs if r["seed"] == s) for s in seeds]
            out["per_config"][c][key] = {"mean": mean(per_seed),
                                         "std_over_seeds": stdev(per_seed) if len(per_seed) > 1 else 0.0,
                                         "std_over_runs": stdev(r[field] for r in rs) if len(rs) > 1 else 0.0}
    for _, field in METRICS:
        pairs = []
        for part in ("iid", "non"):
            full = {(r["seed"], r["K"]): r for r in by_cfg.get(f"Fed-full-{part}", [])}
            split = {(r["seed"], r["K"]): r for r in by_cfg.get(f"Fed-split-{part}", [])}
            common = sorted(set(full) & set(split))
            # re-key by run index so paired() can match them
            pairs.append((part, [{"seed": i, field: split[k][field]} for i, k in enumerate(common)],
                          [{"seed": i, field: full[k][field]} for i, k in enumerate(common)]))
        out["split_vs_full"][field] = family(pairs, field)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=os.path.join("results", "r1_runs.jsonl"))
    ap.add_argument("--ablation", default=os.path.join("results", "ablation_r1.jsonl"))
    args = ap.parse_args()

    by_cfg = defaultdict(list)
    for r in load(args.inp):
        by_cfg[r["config"]].append(r)

    out = {"per_config": {c: summarize(rows) for c, rows in sorted(by_cfg.items())}, "tests": {}}

    print(f"{'config':<26}{'n':>3}  " + "  ".join(f"{k:>15}" for k, _ in METRICS))
    for c, s in out["per_config"].items():
        cells = "  ".join(f"{s[k]['mean']:.4f}±{s[k]['std']:.4f}" for k, _ in METRICS)
        print(f"{c:<26}{s['n_seeds']:>3}  {cells}")

    ks = sorted(int(c.split("_k")[1]) for c in by_cfg if c.startswith("fedavg_k"))
    if "centralized" in by_cfg and ks:
        out["tests"]["fedavg_vs_centralized"] = {
            field: family([(f"k{k}", by_cfg[f"fedavg_k{k}"], by_cfg["centralized"]) for k in ks], field)
            for _, field in METRICS}
    if len(ks) > 2:
        xs = [(int(c.split("_k")[1]), r) for c in by_cfg if c.startswith("fedavg_k") for r in by_cfg[c]]
        out["tests"]["fedavg_trend_with_k"] = {}
        for _, field in (METRICS[0], METRICS[1], METRICS[5]):
            lr = stats.linregress([k for k, _ in xs], [r[field] for _, r in xs])
            out["tests"]["fedavg_trend_with_k"][field] = {
                "slope_per_k": lr.slope, "p": lr.pvalue, "r": lr.rvalue, "n_runs": len(xs)}

    prox = sorted(c for c in by_cfg if c.startswith("fedprox_mu") and "_dir" not in c)
    if prox:
        out["tests"]["fedprox_vs_fedavg"] = {
            field: family([(c, by_cfg[c], by_cfg.get("fedavg_k" + c.rsplit("_k", 1)[1], [])) for c in prox], field)
            for _, field in METRICS}
    noniid = sorted(c for c in by_cfg if "_dir" in c and c.startswith("fedprox"))
    if noniid:
        out["tests"]["noniid_fedprox_vs_fedavg"] = {
            field: family([(c, by_cfg[c], by_cfg.get("fedavg" + c[c.index("_dir"):], [])) for c in noniid], field)
            for _, field in METRICS}
    shift = sorted(c for c in by_cfg if c.startswith("shift_local"))
    if shift:
        out["tests"]["shift_local_vs_global"] = {
            field: family([(c, by_cfg[c], by_cfg.get(c.replace("local", "global"), [])) for c in shift], field)
            for _, field in METRICS}

    if os.path.exists(args.ablation):
        out["ablation"] = ablation(load(args.ablation))
        print(f"\nAblation ({args.ablation}): mean ± std over seeds of the K-averaged metric")
        for c, s in out["ablation"]["per_config"].items():
            cells = "  ".join(f"{s[k]['mean']:.4f}±{s[k]['std_over_seeds']:.4f}" for k, _ in METRICS)
            print(f"{c:<26}{s['n_runs']:>3}  {cells}")

    with open(os.path.join("results", "r1_stats.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    # Learning curves: mean validation F1 per round (runs that stopped early carry their last value)
    with open(os.path.join("results", "r1_curves.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["config", "round", "val_f1_mean", "val_f1_std", "train_loss_mean", "n_runs"])
        for c, rows in sorted(by_cfg.items()):
            hists = [r["History"] for r in rows
                     if isinstance(r.get("History"), list) and r["History"] and "round" in r["History"][0]]
            if not hists:
                continue
            for rnd in range(1, max(len(h) for h in hists) + 1):
                f1s = [h[min(rnd, len(h)) - 1]["val_f1"] for h in hists]
                losses = [h[rnd - 1]["train_loss"] for h in hists if rnd <= len(h)]
                w.writerow([c, rnd, round(mean(f1s), 4), round(stdev(f1s), 4) if len(f1s) > 1 else 0.0,
                            round(mean(losses), 4), len(losses)])

    print("\nTests and per-config details -> results/r1_stats.json; curves -> results/r1_curves.csv")


if __name__ == "__main__":
    main()
