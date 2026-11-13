import os
import sys
import json
import argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

from src.utils import load_config, set_seed, get_device
import benchmark_all as B

SEEDS = [42, 1, 7, 123, 2024, 2, 3, 5, 8, 13]
K_VALUES = [2, 3, 4, 5, 6]
PROX_MUS = [0.01, 0.1]
ALPHAS = [0.5, 0.1]
SHIFT_SIGMA = 0.5


def plan(exps, prox_mu, smoke):
    seeds = [42] if smoke else SEEDS
    ks = [2] if smoke else K_VALUES
    ks_small = [3] if smoke else [3, 6]
    runs = []
    if "main" in exps:
        for seed in seeds:
            runs.append(("centralized", "central", {}, seed))
            runs += [(f"fedavg_k{k}", "fl", {"n_clients": k}, seed) for k in ks]
            runs.append(("fedavg_cl", "cl", {}, seed))
    if "fedprox" in exps:
        for seed in seeds[:5]:
            runs += [(f"fedprox_mu{mu}_k{k}", "fl", {"n_clients": k, "mu": mu}, seed)
                     for mu in PROX_MUS for k in ks]
    if "noniid" in exps:
        for seed in seeds[:5]:
            for alpha in ALPHAS:
                for mu in (0.0, prox_mu):
                    algo = f"fedprox_mu{mu}" if mu else "fedavg"
                    runs += [(f"{algo}_dir{alpha}_k{k}", "fl",
                              {"n_clients": k, "mu": mu, "partition": "dirichlet", "alpha": alpha}, seed)
                             for k in ks_small]
    if "shift" in exps:
        for seed in seeds[:5]:
            runs += [(f"shift_{norm}_k{k}", "fl",
                      {"n_clients": k, "norm": norm, "shift_sigma": SHIFT_SIGMA}, seed)
                     for norm in ("local", "global") for k in ks_small]
    return runs


def load_done(path):
    done = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    done.add((r["config"], r["seed"]))
    return done


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp", nargs="+", default=["main"], choices=["main", "fedprox", "noniid", "shift"])
    ap.add_argument("--prox-mu", type=float, default=0.01, help="FedProx mu used in the non-IID runs")
    ap.add_argument("--out", default=None, help="default results/r1_runs.jsonl (smoke: r1_smoke.jsonl)")
    ap.add_argument("--smoke", action="store_true", help="3 rounds, 2 centralized epochs, seed 42, one K")
    args = ap.parse_args()
    out_path = args.out or os.path.join("results", "r1_smoke.jsonl" if args.smoke else "r1_runs.jsonl")

    runs = plan(args.exp, args.prox_mu, args.smoke)
    done = load_done(out_path)
    todo = [r for r in runs if (r[0], r[3]) not in done]

    print("=" * 60)
    print(f"MULTI-SEED BENCHMARK {args.exp}: {len(runs)} runs, {len(todo)} to do")
    print(f"Output: {out_path}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    config = load_config("configs/dvlog_config.yaml")
    if args.smoke:
        config["training"]["num_epochs"] = 2
    rounds = 3 if args.smoke else 30
    device = get_device("auto")
    tracker = B.GPUEnergyTracker()
    print(f"Device: {device}")

    os.makedirs("results", exist_ok=True)
    with open(os.path.join("results", "r1_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"hardware": B.get_hardware_info(), "seeds": SEEDS, "k_values": K_VALUES,
                   "prox_mus": PROX_MUS, "alphas": ALPHAS, "shift_sigma": SHIFT_SIGMA}, f, indent=2)

    print("\n--- Preparing data (once) ---")
    data = B.prepare_all_data(config)

    for i, (name, kind, kw, seed) in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}] seed={seed} {name}")
        set_seed(seed)
        if kind == "central":
            res = B.run_bilstm_standalone(config, data, device, tracker)
        elif kind == "cl":
            res = B.run_fl_cl(config, data, device, tracker, seed=seed)
        else:
            res = B.run_fl_n_clients(config=config, data=data, device=device, tracker=tracker,
                                     seed=seed, rounds=rounds, **kw)
        rec = {"config": name, "seed": seed, "params": kw,
               "finished": datetime.now().strftime('%Y-%m-%d %H:%M:%S'), **res}
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=_json_default) + "\n")
        print(f"  -> Acc={res['Accuracy']:.4f} F1={res['F1-Score']:.4f} "
              f"AUC={res['AUC']:.4f} Spec={res['Specificity']:.4f}")

    print(f"\nDONE. Results -> {out_path}")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
