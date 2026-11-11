"""
Aggregate multi-seed benchmark results into statistical-significance figures.

Reads results/seed_results.json (produced by benchmark_seeds.py) and reports,
per configuration: mean +/- sample std of Accuracy and F1, a Wilson 95%
confidence interval for the participant-level accuracy (n = test participants),
and a paired t-test of FedAvg K=6 vs the centralized baseline across seeds.

Run from the fl_service/ directory:
    .venv\\Scripts\\python.exe fl_service\\analyze_seeds.py
"""
import os
import json
import math
from statistics import mean, stdev

from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

N_TEST = 212          # D-vlog test participants (see Table tab:cm_table)
Z = 1.959963985       # 95% normal quantile
IN_PATH = os.path.join("results", "seed_results.json")
OUT_PATH = os.path.join("results", "seed_stats.json")

ORDER = ["centralized", "fedavg_k2", "fedavg_k3", "fedavg_k4", "fedavg_k5", "fedavg_k6"]


def wilson_ci(p_hat, n, z=Z):
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    return center - half, center + half


def main():
    with open(IN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    runs = data["runs"]
    by_cfg = {}
    for r in runs:
        by_cfg.setdefault(r["config"], []).append(r)

    out = {"per_config": {}, "tests": {}, "n_test": N_TEST}

    print(f"{'config':<14} {'n':>2} {'acc mean±std':>16} {'acc 95% Wilson CI':>22} {'f1 mean±std':>16}")
    print("-" * 78)
    for cfg in ORDER:
        rows = by_cfg.get(cfg)
        if not rows:
            continue
        accs = [x["accuracy"] for x in rows]
        f1s = [x["f1"] for x in rows]
        te = [x["train_energy_j"] for x in rows]
        ie = [x["infer_energy_j"] for x in rows]
        n = len(accs)
        acc_m = mean(accs)
        acc_s = stdev(accs) if n > 1 else 0.0
        f1_m = mean(f1s)
        f1_s = stdev(f1s) if n > 1 else 0.0
        lo, hi = wilson_ci(acc_m, N_TEST)
        out["per_config"][cfg] = {
            "n_seeds": n,
            "acc_mean": acc_m, "acc_std": acc_s,
            "f1_mean": f1_m, "f1_std": f1_s,
            "acc_wilson_ci": [lo, hi],
            "train_energy_mean": mean(te), "train_energy_std": stdev(te) if n > 1 else 0.0,
            "infer_energy_mean": mean(ie), "infer_energy_std": stdev(ie) if n > 1 else 0.0,
            "acc_raw": accs, "f1_raw": f1s,
        }
        print(f"{cfg:<14} {n:>2} {acc_m:>8.4f}±{acc_s:<6.4f} "
              f"[{lo:.4f}, {hi:.4f}] {f1_m:>8.4f}±{f1_s:<6.4f}")

    # Paired t-test: FedAvg K=6 vs centralized, on seeds present in BOTH
    base = {x["seed"]: x for x in by_cfg.get("centralized", [])}
    k6 = {x["seed"]: x for x in by_cfg.get("fedavg_k6", [])}
    common = sorted(set(base) & set(k6))
    if len(common) >= 2:
        b_acc = [base[s]["accuracy"] for s in common]
        k_acc = [k6[s]["accuracy"] for s in common]
        b_f1 = [base[s]["f1"] for s in common]
        k_f1 = [k6[s]["f1"] for s in common]
        t_acc, p_acc = stats.ttest_rel(k_acc, b_acc)
        t_f1, p_f1 = stats.ttest_rel(k_f1, b_f1)
        out["tests"]["k6_vs_centralized"] = {
            "seeds": common,
            "delta_acc_mean": mean(k_acc) - mean(b_acc),
            "t_acc": t_acc, "p_acc": p_acc,
            "delta_f1_mean": mean(k_f1) - mean(b_f1),
            "t_f1": t_f1, "p_f1": p_f1,
        }
        print("\nPaired t-test  FedAvg K=6 vs Centralized "
              f"(n={len(common)} seeds: {common})")
        print(f"  dAcc = {mean(k_acc)-mean(b_acc):+.4f}  t = {t_acc:.3f}  p = {p_acc:.4f}")
        print(f"  dF1  = {mean(k_f1)-mean(b_f1):+.4f}  t = {t_f1:.3f}  p = {p_f1:.4f}")
    else:
        print(f"\n[paired t-test skipped: only {len(common)} common seed(s)]")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
