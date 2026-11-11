"""
LaTeX rows and summary numbers for the revised Section 7 (JISAS-D-26-04548),
computed from results/r1_runs.jsonl, results/ablation_r1.jsonl and results/r1_stats.json.

Run from fl_service/ after analyze_r1.py:
    python paper_tables_r1.py > results/paper_tables_r1.txt
"""
import os
import json
from collections import defaultdict
from statistics import mean, stdev

import numpy as np

from strip_logs import roc_operating_points

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


runs = defaultdict(list)
for r in load("results/r1_runs.jsonl"):
    runs[r["config"]].append(r)
abl = defaultdict(list)
for r in load("results/ablation_r1.jsonl"):
    abl[r["config"]].append(r)
stats = json.load(open("results/r1_stats.json", encoding="utf-8"))

FIELDS = [("Accuracy", "acc"), ("F1-Score", "f1"), ("AUC", "auc"), ("Recall", "sens"), ("Specificity", "spec")]


def ms(xs, d=3):
    return f"${mean(xs):.{d}f}\\pm{stdev(xs) if len(xs) > 1 else 0:.{d}f}$"


def row(rows, fields=FIELDS, d=3):
    return " & ".join(ms([r[f] for r in rows], d) for f, _ in fields)


print("% ---- Table 3: main results, 10 seeds ----")
names = [("centralized", "Centralized"), *[(f"fedavg_k{k}", f"FedAvg, $K={k}$") for k in range(2, 7)],
         ("fedavg_cl", "FedAvg + CL (EWC + replay)")]
for c, label in names:
    print(f"{label} & {row(runs[c])} \\\\")
for c, _ in names:
    s = stats["per_config"][c]["accuracy"]
    print(f"% {c}: acc CI95 [{s['ci95'][0]:.4f}, {s['ci95'][1]:.4f}] n={stats['per_config'][c]['n_seeds']}")

t = stats["tests"]["fedavg_vs_centralized"]
for f in ("Accuracy", "F1-Score", "AUC", "Recall", "Specificity"):
    d = [v["mean_diff"] for v in t[f].values()]
    p = [v["t_p_holm"] for v in t[f].values()]
    w = [v["wilcoxon_p_holm"] for v in t[f].values()]
    print(f"% FedAvg-centralized {f}: diff {min(d):+.4f}..{max(d):+.4f}  Holm t p {min(p):.3f}..{max(p):.3f}"
          f"  Holm Wilcoxon p {min(w):.3f}..{max(w):.3f}")
for f, v in stats["tests"]["fedavg_trend_with_k"].items():
    print(f"% trend {f}: slope {v['slope_per_k']:+.5f}/K p={v['p']:.3f} r={v['r']:.3f} n={v['n_runs']}")

print("\n% ---- Operating point and review cost (summed confusion matrices) ----")
for c, label in names:
    s = stats["per_config"][c]
    tn, fp, fn, tp = s["cm_sum"]
    sens, spec = tp / (tp + fn), tn / (tn + fp)
    prev = (tp + fn) / (tn + fp + fn + tp)
    ppv10 = 0.1 * sens / (0.1 * sens + 0.9 * (1 - spec))
    # descriptive test-ROC trade-off, per run and facility, then averaged
    spec_at_s80, sens_at_sp60 = [], []
    for r in runs[c]:
        for site in r["PerSite"]:
            # released logs carry the precomputed points instead of labels/probs (strip_logs.py)
            sp, se = (roc_operating_points(site["labels"], site["probs"]) if "probs" in site
                      else (site["spec_at_sens80"], site["sens_at_spec60"]))
            spec_at_s80.append(sp)
            sens_at_sp60.append(se)
    rv = s["review"]
    print(f"{label} & ${sens:.3f}$ & ${spec:.3f}$ & ${rv['ppv']:.3f}$ & ${rv['false_pos_per_true_pos']:.2f}$ & "
          f"${rv['flagged_per_100_screened']:.1f}$ & ${ppv10:.3f}$ & ${mean(spec_at_s80):.3f}$ & "
          f"${mean(sens_at_sp60):.3f}$ \\\\   % prevalence {prev:.3f}")

print("\n% ---- Learning dynamics: selected round, train-val gap at the selected round ----")
for c, _ in names[1:6]:
    s = stats["per_config"][c]
    gaps = []
    for r in runs[c]:
        at = r["History"][r["Best Round"] - 1]["sites"]
        gaps += [x["train_acc"] - x["val_acc"] for x in at]
    print(f"% {c}: best round {s['best_round']:.1f} (range {min(r['Best Round'] for r in runs[c])}-"
          f"{max(r['Best Round'] for r in runs[c])}), per-shard gap mean {mean(gaps):+.4f} "
          f"min {min(gaps):+.4f} max {max(gaps):+.4f}")
cent_best = []
for r in runs["centralized"]:
    h = r["History"]["val_f1"]
    cent_best.append(int(np.argmax(h)) + 1)
print(f"% centralized: best epoch by val F1 mean {mean(cent_best):.1f} range {min(cent_best)}-{max(cent_best)}")

print("\n% ---- Heterogeneity table ----")
H = [("IID", 2, "fedavg_k{k}", "FedAvg"), ]
seeds5 = [42, 1, 7, 123, 2024]


def sub(rows, seeds):
    return [r for r in rows if r["seed"] in seeds]


HF = [("Accuracy", "acc"), ("AUC", "auc"), ("Specificity", "spec")]
print("% IID, K=2..6 pooled over K, first five seeds")
for label, pat in (("FedAvg", "fedavg_k{k}"), ("FedProx, $\\mu=0.01$", "fedprox_mu0.01_k{k}"),
                   ("FedProx, $\\mu=0.1$", "fedprox_mu0.1_k{k}")):
    rows = [r for k in range(2, 7) for r in sub(runs[pat.format(k=k)], seeds5)]
    print(f"IID & 2--6 & {label} & {row(rows, HF)} \\\\")
for alpha in ("0.5", "0.1"):
    for k in (3, 6):
        for label, c in (("FedAvg", f"fedavg_dir{alpha}_k{k}"), ("FedProx, $\\mu=0.01$", f"fedprox_mu0.01_dir{alpha}_k{k}")):
            print(f"Dirichlet $\\alpha={alpha}$ & {k} & {label} & {row(runs[c], HF)} \\\\")
for k in (3, 6):
    for label, c in (("Pooled scaler", f"shift_global_k{k}"), ("Per-facility scaler", f"shift_local_k{k}")):
        print(f"Device shift & {k} & {label} & {row(runs[c], HF)} \\\\")
for fam in ("fedprox_vs_fedavg", "noniid_fedprox_vs_fedavg", "shift_local_vs_global"):
    for f in ("Accuracy", "AUC", "Specificity"):
        v = stats["tests"][fam][f]
        print(f"% {fam} {f}: " + ", ".join(f"{k}: {x['mean_diff']:+.4f} (p_holm {x['t_p_holm']:.3f})"
                                            for k, x in v.items() if x))

print("\n% ---- Ablation (Table 7), mean over K per seed, +- std over seeds ----")
AF = [("Accuracy", "accuracy"), ("F1-Score", "f1"), ("AUC", "auc"), ("Specificity", "specificity")]
for c, label, part in (("Fed-full-iid", "Full FedAvg \\scriptsize(179{,}332 fed.)", "IID"),
                       ("Fed-split-iid", "Local--shared \\scriptsize(158{,}340 fed.)", "IID"),
                       ("Fed-full-non", "Full FedAvg \\scriptsize(179{,}332 fed.)", "non-IID"),
                       ("Fed-split-non", "Local--shared \\scriptsize(158{,}340 fed.)", "non-IID")):
    s = stats["ablation"]["per_config"][c]
    cells = " & ".join(f"${s[k]['mean']:.3f}\\pm{s[k]['std_over_seeds']:.3f}$" for _, k in AF)
    print(f"{label} & {part} & {cells} \\\\")
for f in ("Accuracy", "F1-Score", "AUC", "Specificity"):
    v = stats["ablation"]["split_vs_full"][f]
    print(f"% split-full {f}: " + ", ".join(f"{k}: {x['mean_diff']:+.4f} (t_holm {x['t_p_holm']:.4f}, "
                                            f"w_holm {x['wilcoxon_p_holm']:.4f}, n {x['n']})" for k, x in v.items()))

print("\n% ---- Footprint (mean over seeds) ----")
for c in ("centralized", "fedavg_k3", "fedavg_k6"):
    rs = runs[c]
    vals = {k: mean(r[k] for r in rs) for k in ("Training Energy (J)", "Training Time (s)", "Training Memory (MB)",
                                                 "Inference Energy (J)", "Inference Time (s)")}
    print(f"% {c}: " + ", ".join(f"{k}={v:.3f}" for k, v in vals.items()))
