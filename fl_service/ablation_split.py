"""
Ablation: local-shared parameter split vs full FedAvg, under IID and non-IID
(Dirichlet) partitions of D-vlog, for K in {2..6}.

Both variants use the same architecture (FederatedModel), so the only
difference is the aggregation rule: "full" averages every parameter each
round; "split" averages the shared backbone only and keeps the per-facility
projection layers local. The protocol matches the main federated runs
(benchmark_all.run_fl_n_clients): every facility fits its own scalers,
batch size and patience come from the config, the global model is validated
every round, and each facility picks its threshold on its own validation copy.
Under the split, each facility is evaluated with its own projection layers.

Every run appends one JSON line to --out; runs already present are skipped,
so an interrupted run resumes where it stopped.

Usage (from fl_service/):
    python ablation_split.py                    # seeds 42,1,2, K=2..6, 4 configurations
    python ablation_split.py --seeds 42,1,2,7
    python ablation_split.py --smoke            # 3 rounds, K in {2, 6}, seed 42
"""

import os
import sys
import copy
import json
import time
import argparse
from collections import OrderedDict
from datetime import datetime
from statistics import mean

import numpy as np
import torch
import torch.nn as nn

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

from src.utils import load_config, set_seed, get_device
from src.federated import FederatedModel
import benchmark_all as B

PROJ_PREFIXES = ("audio_proj", "visual_proj")
NUM_ROUNDS = 30
LOCAL_EPOCHS = 3
LR = 5e-4
WD = 1e-3


# ─────────────────────────── partitioning ───────────────────────────
def noniid_partition(pids, label_map, K, alpha=0.5, seed=42, min_per_client=10):
    """Dirichlet label-skew partition at participant granularity.

    alpha controls skew (smaller = more heterogeneous); min_per_client keeps
    every shard trainable by moving participants from the largest shard.
    """
    rng = np.random.default_rng(seed)
    pids = np.asarray(pids)
    labels = np.asarray([label_map[int(p)] for p in pids])
    client_pids = [[] for _ in range(K)]
    for c in (0, 1):
        idx = pids[labels == c].copy()
        rng.shuffle(idx)
        props = rng.dirichlet([alpha] * K)
        counts = np.floor(props * len(idx)).astype(int)
        while counts.sum() < len(idx):
            counts[rng.integers(K)] += 1
        start = 0
        for k in range(K):
            client_pids[k].extend(idx[start:start + counts[k]].tolist())
            start += counts[k]
    # enforce a minimum shard size (moving from the largest shard)
    guard = 0
    while guard < 10000:
        smalls = [k for k in range(K) if len(client_pids[k]) < min_per_client]
        if not smalls:
            break
        k = smalls[0]
        big = max(range(K), key=lambda j: len(client_pids[j]))
        if len(client_pids[big]) <= min_per_client:
            break
        client_pids[k].append(client_pids[big].pop())
        guard += 1
    return client_pids


# ─────────────────────────── local training ───────────────────────────
def train_local(model, site, device, epochs=LOCAL_EPOCHS, lr=LR, wd=WD):
    """Same local update as the main federated runs; returns the mean training loss."""
    model.train()
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=wd)
    crit = nn.BCEWithLogitsLoss(pos_weight=site["pos_weight"])
    loss_sum, n = 0.0, 0
    for _ in range(epochs):
        for batch in site["train"]:
            audio = batch["audio"].to(device) + torch.randn(batch["audio"].shape).to(device) * 0.05
            visual = batch["visual"].to(device) + torch.randn(batch["visual"].shape).to(device) * 0.05
            labels = batch["label"].to(device) * 0.95 + 0.025
            opt.zero_grad()
            loss = crit(model(audio, visual), labels)
            loss_sum, n = loss_sum + loss.item() * len(labels), n + len(labels)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
    return loss_sum / max(n, 1)


def _weighted_mean(states, sizes, keys):
    total = sum(sizes)
    return OrderedDict((k, sum(s[k].float() * (w / total) for s, w in zip(states, sizes))) for k in keys)


def _round_record(rnd, losses, scores):
    return {"round": rnd, "train_loss": mean(losses), "val_f1": mean(s["val_f1"] for s in scores),
            "sites": scores}


# ─────────────────────────── FL: full averaging ───────────────────────────
def run_full(build, sites, device, rounds, patience):
    sizes = [s["size"] for s in sites]
    global_model = build()
    params = sum(p.numel() for p in global_model.parameters())
    best_f1, best_state, best_round, history = -1.0, None, 0, []

    for rnd in range(1, rounds + 1):
        global_state = copy.deepcopy(global_model.state_dict())
        states, losses = [], []
        for site in sites:
            m = build()
            m.load_state_dict(global_state)
            losses.append(train_local(m, site, device))
            states.append(m.state_dict())
        global_model.load_state_dict(_weighted_mean(states, sizes, global_state.keys()))

        scores = [B.site_round_scores(global_model, s, device) for s in sites]
        history.append(_round_record(rnd, losses, scores))
        print(f"    round {rnd:2d} loss={history[-1]['train_loss']:.4f} val_f1={history[-1]['val_f1']:.4f}",
              flush=True)
        if history[-1]["val_f1"] > best_f1:
            best_f1, best_state, best_round = history[-1]["val_f1"], copy.deepcopy(global_model.state_dict()), rnd
        elif rnd - best_round >= patience:
            print(f"    early stop @ {rnd}", flush=True)
            break

    global_model.load_state_dict(best_state)
    per_site = B.evaluate_sites(global_model, sites, device)
    return per_site, {"params": params, "Best Round": best_round, "History": history}


# ─────────────────────────── FL: local-shared split ───────────────────────────
def run_split(build, sites, device, rounds, patience):
    sizes = [s["size"] for s in sites]
    init = build()
    params = sum(p.numel() for p in init.parameters())
    full0 = copy.deepcopy(init.state_dict())
    shared_keys = [k for k in full0 if not k.startswith(PROJ_PREFIXES)]
    proj_keys = [k for k in full0 if k.startswith(PROJ_PREFIXES)]
    n_local = sum(int(np.prod(full0[k].shape)) for k in proj_keys)
    n_shared = sum(int(np.prod(full0[k].shape)) for k in shared_keys)

    global_shared = OrderedDict((k, full0[k].clone()) for k in shared_keys)
    client_proj = [OrderedDict((k, full0[k].clone()) for k in proj_keys) for _ in sites]
    best_f1, best_state, best_round, history = -1.0, None, 0, []

    def site_model(shared, proj):
        m = build()
        m.load_state_dict(OrderedDict(list(shared.items()) + list(proj.items())))
        return m

    for rnd in range(1, rounds + 1):
        client_shared, losses = [], []
        for i, site in enumerate(sites):
            m = site_model(global_shared, client_proj[i])
            losses.append(train_local(m, site, device))
            sd = m.state_dict()
            client_proj[i] = OrderedDict((k, sd[k].detach().clone()) for k in proj_keys)
            client_shared.append(OrderedDict((k, sd[k].detach().clone()) for k in shared_keys))
        # aggregate the SHARED backbone only; projections stay with their facility
        global_shared = _weighted_mean(client_shared, sizes, shared_keys)

        scores = [B.site_round_scores(site_model(global_shared, client_proj[i]), s, device)
                  for i, s in enumerate(sites)]
        history.append(_round_record(rnd, losses, scores))
        print(f"    round {rnd:2d} loss={history[-1]['train_loss']:.4f} val_f1={history[-1]['val_f1']:.4f}",
              flush=True)
        if history[-1]["val_f1"] > best_f1:
            best_f1, best_round = history[-1]["val_f1"], rnd
            best_state = (copy.deepcopy(global_shared), copy.deepcopy(client_proj))
        elif rnd - best_round >= patience:
            print(f"    early stop @ {rnd}", flush=True)
            break

    shared, projs = best_state
    per_site = []
    for i, site in enumerate(sites):  # each facility uses its own projection layers
        per_site += B.evaluate_sites(site_model(shared, projs[i]), [site], device)
    return per_site, {"params": params, "local_params": n_local, "shared_params": n_shared,
                      "Best Round": best_round, "History": history}


# ─────────────────────────── driver ───────────────────────────
CONFIGS = [
    ("Fed-full-iid", "full", "iid"),       # same-architecture full-averaging baseline
    ("Fed-split-iid", "split", "iid"),     # the local-shared split
    ("Fed-full-non", "full", "noniid"),    # heterogeneity
    ("Fed-split-non", "split", "noniid"),
]


def run_one(name, agg, part, K, seed, data, config, device, rounds, alpha):
    from main_fl_clients import split_pids

    mc = config["model"]
    set_seed(seed)
    if part == "iid":
        splits = split_pids(data["train_pids"], K, seed)   # same shards as the main FedAvg runs
    else:
        splits = noniid_partition(data["train_pids"], data["label_map"], K, alpha, seed)
    pos_frac = [round(float(np.mean([data["label_map"][int(p)] for p in s])), 2) for s in splits]
    print(f"\n[{name}] seed={seed} K={K} sizes={[len(s) for s in splits]} pos_frac={pos_frac}", flush=True)
    sites = B.build_sites(data, splits, config["training"]["batch_size"], device)

    def build():
        return FederatedModel(data["audio_dim"], data["visual_dim"], shared_dim=mc["hidden_dim"],
                              hidden_dim=mc["hidden_dim"], num_layers=mc["num_layers"],
                              dropout=mc["dropout"]).to(device)

    tic = time.time()
    run = run_split if agg == "split" else run_full
    per_site, extra = run(build, sites, device, rounds, config["training"]["patience"])
    params = extra.pop("params")
    return {"config": name, "agg": agg, "partition": part, "seed": seed, "K": K,
            "alpha": alpha if part == "noniid" else None,
            "Shards": [len(s) for s in splits], "pos_frac": pos_frac,
            "elapsed_s": round(time.time() - tic, 1),
            "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **B._result(name, params, per_site, extra)}


def _json_default(o):
    return o.item() if hasattr(o, "item") else str(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="3 rounds, K in {2, 6}, seed 42")
    ap.add_argument("--seeds", type=str, default="42,1,2", help="comma-separated seeds")
    ap.add_argument("--alpha", type=float, default=0.5, help="Dirichlet alpha of the non-IID partition")
    ap.add_argument("--out", default=None, help="default results/ablation_r1.jsonl (smoke: ablation_r1_smoke.jsonl)")
    args = ap.parse_args()

    config = load_config("configs/dvlog_config.yaml")
    device = get_device(config.get("device", "auto"))
    Ks = [2, 6] if args.smoke else [2, 3, 4, 5, 6]
    rounds = 3 if args.smoke else NUM_ROUNDS
    seeds = [42] if args.smoke else [int(s) for s in args.seeds.split(",") if s.strip()]
    out_path = args.out or os.path.join("results", "ablation_r1_smoke.jsonl" if args.smoke else "ablation_r1.jsonl")

    done = set()
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            done = {(r["config"], r["seed"], r["K"]) for r in map(json.loads, filter(str.strip, f))}
    todo = [(name, agg, part, K, seed) for seed in seeds for name, agg, part in CONFIGS for K in Ks
            if (name, seed, K) not in done]

    print(f"device={device} | Ks={Ks} | rounds={rounds} | seeds={seeds} | alpha={args.alpha}")
    print(f"{len(todo)} runs to do -> {out_path}", flush=True)
    if not todo:
        return
    data = B.prepare_all_data(config)
    os.makedirs("results", exist_ok=True)

    t0 = time.time()
    for i, (name, agg, part, K, seed) in enumerate(todo, 1):
        print(f"\n[{i}/{len(todo)}]", end="")
        rec = run_one(name, agg, part, K, seed, data, config, device, rounds, args.alpha)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=_json_default) + "\n")
        print(f"  => {name} seed={seed} K={K}: acc={rec['Accuracy']:.4f} f1={rec['F1-Score']:.4f} "
              f"auc={rec['AUC']:.4f} spec={rec['Specificity']:.4f} params={rec['Parameters']:,}", flush=True)

    print(f"\nALL DONE in {round(time.time() - t0)}s ({datetime.now():%H:%M:%S}). Results: {out_path}",
          flush=True)


if __name__ == "__main__":
    main()
