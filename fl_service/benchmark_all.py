"""
Benchmark ALL experiments and export to Excel.

Experiments:
  1. BiLSTM (standalone)
  2. BiLSTM + FL (2, 3, 4, 5, 6 clients)
  3. BiLSTM + FL + CL (EWC + Replay)

Metrics per experiment:
  - Accuracy, F1, Precision, Recall
  - Training Time (s), Training Energy (J), Training Memory (MB)
  - Inference Time (s), Inference Energy (J)
  - Model Parameters

Usage:
    cd fl_service
    python benchmark_all.py
"""

import os
import sys
import time
import copy
import platform
import psutil
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from collections import OrderedDict
from datetime import datetime
from sklearn.metrics import confusion_matrix, roc_auc_score
from sklearn.preprocessing import StandardScaler

from src.utils import load_config, set_seed, get_device
from src.dvlog_data_loader import DVlogDataset
from src.dvlog_feature_extraction import DVlogFeatureExtractor, build_dvlog_windowed_dataset
from src.dataset import DepressionDataset
from src.model import MultimodalBiLSTM
from src.evaluate import (
    _aggregate_by_participant, find_optimal_threshold,
    evaluate_participant_level,
)
from src.continual import EWC, ReplayBuffer, train_with_ewc


# ─── GPU Energy Measurement ───

class GPUEnergyTracker:
    """Track GPU energy consumption using pynvml."""

    def __init__(self):
        self.supported = False
        try:
            import pynvml
            pynvml.nvmlInit()
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self.supported = True
            self.pynvml = pynvml
        except Exception:
            pass

    def get_power_mw(self):
        if not self.supported:
            return 0
        try:
            return self.pynvml.nvmlDeviceGetPowerUsage(self.handle)  # milliwatts
        except Exception:
            return 0

    def measure_energy(self, func, *args, **kwargs):
        """Run func and measure total energy in Joules."""
        if not self.supported:
            start = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start
            return result, elapsed, 0.0  # no energy data

        # Sample power every 0.1s during execution
        import threading
        power_samples = []
        stop_event = threading.Event()

        def sample_power():
            while not stop_event.is_set():
                p = self.get_power_mw()
                if p > 0:
                    power_samples.append(p)
                time.sleep(0.1)

        sampler = threading.Thread(target=sample_power, daemon=True)
        sampler.start()

        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start

        stop_event.set()
        sampler.join(timeout=1)

        # Energy = avg_power * time (convert mW to W)
        if power_samples:
            avg_power_w = np.mean(power_samples) / 1000.0
            energy_j = avg_power_w * elapsed
        else:
            energy_j = 0.0

        return result, elapsed, energy_j


# ─── Hardware Info ───

def get_hardware_info():
    info = {
        "CPU": platform.processor() or "Unknown",
        "CPU Cores": psutil.cpu_count(logical=False),
        "CPU Threads": psutil.cpu_count(logical=True),
        "RAM (GB)": round(psutil.virtual_memory().total / 1024**3, 1),
        "OS": f"{platform.system()} {platform.release()}",
        "Python": platform.python_version(),
        "PyTorch": torch.__version__,
    }
    if torch.cuda.is_available():
        info["GPU"] = torch.cuda.get_device_name(0)
        info["VRAM (GB)"] = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1)
        info["CUDA"] = torch.version.cuda
    return info


# ─── Data Preparation (shared) ───

def prepare_all_data(config):
    """Prepare D-vlog data once, return everything needed.

    Segment descriptors are cached unnormalized so that every simulated
    facility can fit its own scalers (SiteNormalizer, Section 3.5). The
    datasets returned here use one scaler fitted on all training participants
    and serve the centralized baseline only.
    """
    dataset = DVlogDataset(config["data"]["dataset_dir"])
    extractor = DVlogFeatureExtractor(config)

    train_pids = dataset.get_participant_ids("train")
    valid_pids = dataset.get_participant_ids("valid")
    test_pids = dataset.get_participant_ids("test")
    all_labels = dataset.load_labels()
    train_labels = dataset.load_labels("train")

    print("Caching segment descriptors...")
    segs = {
        pid: extractor.process_participant_segments(dataset.load_participant(pid), normalize=False)
        for pid in train_pids + valid_pids + test_pids
    }
    global_norm = SiteNormalizer(segs, train_pids)

    wc = config["preprocessing"]["windowing"]
    data = {
        "dataset": dataset, "all_labels": all_labels, "segs": segs, "global_norm": global_norm,
        "train_pids": train_pids, "val_pids": valid_pids, "test_pids": test_pids,
        "train_labels": train_labels,
        "label_map": dict(zip(train_labels["Participant_ID"].astype(int),
                              train_labels["PHQ8_Binary"].astype(int))),
        "ws": wc["window_size"], "stride": wc["stride"],
    }

    print("Building windowed datasets...")
    data["train_ds"] = site_dataset(data, global_norm, train_pids)
    data["val_ds"] = site_dataset(data, global_norm, valid_pids)
    data["test_ds"] = site_dataset(data, global_norm, test_pids)
    data["audio_dim"] = data["train_ds"][0]["audio"].shape[-1]
    data["visual_dim"] = data["train_ds"][0]["visual"].shape[-1]

    print(f"Train: {len(data['train_ds'])}, Val: {len(data['val_ds'])}, Test: {len(data['test_ds'])}")
    print(f"Audio dim: {data['audio_dim']}, Visual dim: {data['visual_dim']}")
    return data


# ─── Per-facility normalization and evaluation ───

def _fit_scaler(arrays):
    arrays = [a for a in arrays if a is not None]
    return StandardScaler().fit(np.vstack(arrays)) if arrays else None


def _apply_shift(seg, gain, offset):
    """Device shift x -> gain*x + offset on frames, expressed on [mean | std] segments."""
    d = seg.shape[1] // 2
    out = seg.copy()
    out[:, :d] = seg[:, :d] * gain + offset
    out[:, d:] = seg[:, d:] * np.abs(gain)
    return out


class SiteNormalizer:
    """z-score scalers fitted on one facility's training participants only.

    Reads the cached, unnormalized segment descriptors and stands in for both
    the dataset and the extractor argument of build_dvlog_windowed_dataset.
    `shift` maps modality -> (gain, offset): a facility-specific recording device.
    """

    def __init__(self, segs, fit_pids, shift=None):
        self.segs, self.shift = segs, shift
        raw = [self._raw(pid) for pid in fit_pids]
        self.audio_scaler = _fit_scaler([r["audio"] for r in raw])
        self.visual_scaler = _fit_scaler([r["visual"] for r in raw])

    def _raw(self, pid):
        seg = self.segs[pid]
        if not self.shift:
            return seg
        return {m: None if seg[m] is None else _apply_shift(seg[m], *self.shift[m])
                for m in ("audio", "visual")}

    def load_participant(self, pid):
        return pid

    def process_participant_segments(self, pid, normalize=True):
        raw = self._raw(pid)
        out = {"text": None}
        for m, scaler in (("audio", self.audio_scaler), ("visual", self.visual_scaler)):
            x = raw[m]
            out[m] = scaler.transform(x) if normalize and x is not None and scaler is not None else x
        return out


def site_dataset(data, norm, pids):
    feat, y, wp = build_dvlog_windowed_dataset(
        norm, pids, data["all_labels"], norm, data["ws"], data["stride"])
    return DepressionDataset(feat["audio"], feat["visual"], feat["text"], y, data["ws"], wp)


def device_shifts(data, n_sites, sigma, seed):
    """Per-facility gain exp(N(0, sigma)) and offset N(0, sigma) x pooled feature std."""
    rng = np.random.default_rng(seed + 1000)
    ref = data["global_norm"]
    shifts = []
    for _ in range(n_sites):
        shift = {}
        for m, scaler in (("audio", ref.audio_scaler), ("visual", ref.visual_scaler)):
            d = scaler.scale_.shape[0] // 2
            shift[m] = (np.exp(rng.normal(0, sigma, d)), rng.normal(0, sigma, d) * scaler.scale_[:d])
        shifts.append(shift)
    return shifts


METRIC_KEYS = ("accuracy", "f1", "precision", "recall", "specificity", "auc")


def evaluate_sites(model, sites, device):
    """Each facility picks its threshold on its own validation copy and scores its own test copy."""
    per_site = []
    for site in sites:
        probs, labels = _aggregate_by_participant(site["val"], model, device)
        thresh, _ = find_optimal_threshold(probs, labels)
        m = evaluate_participant_level(model, site["test"], device, threshold=thresh)
        tn, fp, fn, tp = confusion_matrix(m["labels"], m["predictions"], labels=[0, 1]).ravel()
        per_site.append({
            "accuracy": m["accuracy"], "f1": m["f1"], "precision": m["precision"], "recall": m["recall"],
            "specificity": tn / (tn + fp) if tn + fp else 0.0,
            "auc": roc_auc_score(m["labels"], m["probabilities"]),
            "threshold": float(thresh), "cm": [int(tn), int(fp), int(fn), int(tp)],
            "labels": m["labels"].tolist(),
            "probs": [round(float(p), 4) for p in m["probabilities"]],
        })
    return per_site


def site_round_scores(model, site, device):
    """Validation F1/accuracy/AUC and training-shard accuracy at the validation threshold."""
    probs, labels = _aggregate_by_participant(site["val"], model, device)
    thresh, f1 = find_optimal_threshold(probs, labels)
    tr_probs, tr_labels = _aggregate_by_participant(site["train_eval"], model, device)
    return {
        "val_f1": float(f1),
        "val_acc": float(np.mean((probs >= thresh) == labels)),
        "val_auc": float(roc_auc_score(labels, probs)),
        "train_acc": float(np.mean((tr_probs >= thresh) == tr_labels)),
    }


def _result(name, params, per_site, extra):
    """Mean over facilities of the per-facility test metrics, plus the per-facility results."""
    mean = {k: float(np.mean([s[k] for s in per_site])) for k in METRIC_KEYS}
    return {
        "Experiment": name, "Parameters": params,
        "Accuracy": mean["accuracy"], "F1-Score": mean["f1"],
        "Precision": mean["precision"], "Recall": mean["recall"],
        "Specificity": mean["specificity"], "AUC": mean["auc"],
        **extra, "PerSite": per_site,
    }


def create_model(config, audio_dim, visual_dim, device):
    mc = config["model"]
    model = MultimodalBiLSTM(
        audio_input_dim=audio_dim, visual_input_dim=visual_dim,
        hidden_dim=mc["hidden_dim"], num_layers=mc["num_layers"],
        dropout=mc["dropout"], fusion=mc.get("fusion", "attention"),
    ).to(device)
    return model


def measure_inference(model, test_loader, device, tracker, threshold=0.5):
    """Measure inference time and energy on test set."""
    model.eval()

    def run_inference():
        with torch.no_grad():
            for batch in test_loader:
                audio = batch["audio"].to(device)
                visual = batch["visual"].to(device)
                _ = model(audio, visual)

    _, inf_time, inf_energy = tracker.measure_energy(run_inference)

    # Also get metrics
    metrics = evaluate_participant_level(model, test_loader, device, threshold=threshold)
    return metrics, inf_time, inf_energy


# ─── Experiment 1: BiLSTM Standalone ───

def run_bilstm_standalone(config, data, device, tracker):
    print("\n" + "=" * 60)
    print("EXP 1: BiLSTM Standalone")
    print("=" * 60)

    bs = config["training"]["batch_size"]
    train_loader = DataLoader(data["train_ds"], batch_size=bs, shuffle=True)
    val_loader = DataLoader(data["val_ds"], batch_size=bs, shuffle=False)
    test_loader = DataLoader(data["test_ds"], batch_size=bs, shuffle=False)

    model = create_model(config, data["audio_dim"], data["visual_dim"], device)
    params = sum(p.numel() for p in model.parameters())
    print(f"Parameters: {params:,}")

    torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None

    def do_train():
        from src.train import train as train_fn
        return train_fn(model, train_loader, val_loader, config, device,
                        save_dir="checkpoints/benchmark_standalone")

    history, train_time, train_energy = tracker.measure_energy(do_train)

    train_mem = torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else 0

    # Load best model
    best_path = "checkpoints/benchmark_standalone/best_model.pt"
    if os.path.exists(best_path):
        model.load_state_dict(torch.load(best_path, map_location=device, weights_only=True))

    per_site = evaluate_sites(model, [{"val": val_loader, "test": test_loader}], device)
    _, inf_time, inf_energy = measure_inference(model, test_loader, device, tracker)

    return _result("BiLSTM (standalone)", params, per_site, {
        "Training Time (s)": round(train_time, 2),
        "Training Energy (J)": round(train_energy, 2),
        "Training Memory (MB)": round(train_mem, 2),
        "Inference Time (s)": round(inf_time, 4),
        "Inference Energy (J)": round(inf_energy, 4),
        "History": history,
    })


# ─── Experiment 2: BiLSTM + FL (N clients) ───

def build_sites(data, client_pid_splits, bs, device, shifts=None, norm="local"):
    """One simulated facility per shard: its own scalers, data loaders and class weight.

    Every facility normalizes its own training shard and its own copy of the
    shared validation/test participants with its own scalers (norm="local"),
    or all facilities use one scaler fitted on the pooled training data (norm="global").
    """
    shifts = shifts or [None] * len(client_pid_splits)
    norms = [SiteNormalizer(data["segs"], pids, shift) for pids, shift in zip(client_pid_splits, shifts)]
    if norm == "global":
        pooled = [nz._raw(pid) for nz, pids in zip(norms, client_pid_splits) for pid in pids]
        audio_scaler = _fit_scaler([r["audio"] for r in pooled])
        visual_scaler = _fit_scaler([r["visual"] for r in pooled])
        for nz in norms:
            nz.audio_scaler, nz.visual_scaler = audio_scaler, visual_scaler

    sites = []
    for i, (client_pids, nz) in enumerate(zip(client_pid_splits, norms)):
        ds = site_dataset(data, nz, client_pids)
        num_pos = float(np.sum(ds.labels))
        sites.append({
            "train": DataLoader(ds, batch_size=bs, shuffle=True),
            "train_eval": DataLoader(ds, batch_size=bs, shuffle=False),
            "val": DataLoader(site_dataset(data, nz, data["val_pids"]), batch_size=bs, shuffle=False),
            "test": DataLoader(site_dataset(data, nz, data["test_pids"]), batch_size=bs, shuffle=False),
            # clamped so that single-class shards (label skew) keep a bounded class weight
            "pos_weight": torch.tensor([float(np.clip((len(ds) - num_pos) / max(num_pos, 1.0), 0.2, 10.0))]).to(device),
            "size": len(client_pids),
        })
        print(f"  Client {i+1}: {len(client_pids)} participants, {len(ds)} windows")
    return sites


def run_fl_n_clients(n_clients, config, data, device, tracker, seed=None, mu=0.0,
                     partition="iid", alpha=0.5, norm="local", shift_sigma=0.0, rounds=30):
    """FedAvg (mu = 0) or FedProx (mu > 0) across n_clients simulated facilities.

    partition: "iid" random shards, or "dirichlet" label skew with `alpha`.
    norm: "local" = every facility fits its own scalers (Section 3.5);
          "global" = one scaler fitted on the pooled training data (reference for the shift study).
    shift_sigma > 0 gives every facility its own simulated recording device.
    The shard assignment follows `seed`.
    """
    seed = config.get("seed", 42) if seed is None else seed
    algo = f"FedProx mu={mu}" if mu > 0 else "FedAvg"
    print(f"\n{'=' * 60}")
    print(f"EXP: BiLSTM + {algo} ({n_clients} clients, {partition}, {norm} norm, "
          f"shift {shift_sigma}, seed {seed})")
    print(f"{'=' * 60}")

    from main_fl_clients import split_pids
    from ablation_split import noniid_partition

    bs, patience = config["training"]["batch_size"], config["training"]["patience"]

    if partition == "dirichlet":
        client_pid_splits = noniid_partition(data["train_pids"], data["label_map"], n_clients, alpha, seed)
    else:
        client_pid_splits = split_pids(data["train_pids"], n_clients, seed)

    shifts = device_shifts(data, n_clients, shift_sigma, seed) if shift_sigma > 0 else None
    sites = build_sites(data, client_pid_splits, bs, device, shifts, norm)

    global_model = create_model(config, data["audio_dim"], data["visual_dim"], device)
    params = sum(p.numel() for p in global_model.parameters())
    history = []

    torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None

    def do_fl_training():
        local_epochs = 3
        lr = 0.0005
        best_f1, best_state, best_round = -1.0, None, 0
        total_samples = sum(s["size"] for s in sites)

        for rnd in range(1, rounds + 1):
            global_state = copy.deepcopy(global_model.state_dict())
            global_params = [p.detach().clone() for p in global_model.parameters()]
            client_states = []
            loss_sum, loss_n = 0.0, 0

            for site in sites:
                local_model = create_model(config, data["audio_dim"], data["visual_dim"], device)
                local_model.load_state_dict(global_state)

                optimizer = torch.optim.Adam(local_model.parameters(), lr=lr, weight_decay=0.001)
                criterion = nn.BCEWithLogitsLoss(pos_weight=site["pos_weight"])

                local_model.train()
                for ep in range(local_epochs):
                    for batch in site["train"]:
                        audio = batch["audio"].to(device) + torch.randn(batch["audio"].shape).to(device) * 0.05
                        visual = batch["visual"].to(device) + torch.randn(batch["visual"].shape).to(device) * 0.05
                        labels = batch["label"].to(device)
                        labels = labels * 0.95 + 0.025

                        optimizer.zero_grad()
                        logits = local_model(audio, visual)
                        loss = criterion(logits, labels)
                        loss_sum, loss_n = loss_sum + loss.item() * len(labels), loss_n + len(labels)
                        if mu > 0:  # FedProx proximal term
                            prox = sum(((p - g) ** 2).sum()
                                       for p, g in zip(local_model.parameters(), global_params))
                            loss = loss + 0.5 * mu * prox
                        loss.backward()
                        nn.utils.clip_grad_norm_(local_model.parameters(), 1.0)
                        optimizer.step()

                client_states.append(local_model.state_dict())

            # FedAvg
            avg_state = OrderedDict()
            for key in global_state.keys():
                avg_state[key] = sum(
                    client_states[i][key].float() * (sites[i]["size"] / total_samples)
                    for i in range(n_clients)
                )
            global_model.load_state_dict(avg_state)

            # Validate every round: model selection and learning curves
            scores = [site_round_scores(global_model, site, device) for site in sites]
            val_f1 = float(np.mean([s["val_f1"] for s in scores]))
            history.append({"round": rnd, "train_loss": loss_sum / loss_n, "val_f1": val_f1, "sites": scores})
            print(f"  Round {rnd:2d}/{rounds} | Val F1={val_f1:.4f}")
            if val_f1 > best_f1:
                best_f1, best_round = val_f1, rnd
                best_state = copy.deepcopy(global_model.state_dict())

            if rnd - best_round >= patience:
                print(f"  Early stopping at round {rnd}")
                break

        global_model.load_state_dict(best_state)
        return best_round

    best_round, train_time, train_energy = tracker.measure_energy(do_fl_training)
    train_mem = torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else 0

    per_site = evaluate_sites(global_model, sites, device)
    _, inf_time, inf_energy = measure_inference(global_model, sites[0]["test"], device, tracker)

    return _result(f"BiLSTM + {algo} ({n_clients} clients)", params, per_site, {
        "Training Time (s)": round(train_time, 2),
        "Training Energy (J)": round(train_energy, 2),
        "Training Memory (MB)": round(train_mem, 2),
        "Inference Time (s)": round(inf_time, 4),
        "Inference Energy (J)": round(inf_energy, 4),
        "Best Round": best_round,
        "Shards": [s["size"] for s in sites],
        "History": history,
    })


# ─── Experiment 3: BiLSTM + FL + CL ───

def run_fl_cl(config, data, device, tracker, seed=None):
    seed = config.get("seed", 42) if seed is None else seed
    print(f"\n{'=' * 60}")
    print(f"EXP: BiLSTM + FL + CL (EWC + Replay), seed {seed}")
    print(f"{'=' * 60}")

    cl_config = load_config("configs/continual_config.yaml")
    cc = cl_config["continual"]
    bs = 16

    # Split train into 2 tasks
    np.random.seed(seed)
    shuffled = np.random.permutation(data["train_pids"])
    mid = len(shuffled) // 2
    task1_pids, task2_pids = shuffled[:mid].tolist(), shuffled[mid:].tolist()

    # Each task is a facility with its own scalers (Section 3.5)
    tasks = []
    for pids in (task1_pids, task2_pids):
        nz = SiteNormalizer(data["segs"], pids)
        ds = site_dataset(data, nz, pids)
        tasks.append({
            "ds": ds,
            "train": DataLoader(ds, batch_size=bs, shuffle=True),
            "val": DataLoader(site_dataset(data, nz, data["val_pids"]), batch_size=bs, shuffle=False),
            "test": DataLoader(site_dataset(data, nz, data["test_pids"]), batch_size=bs, shuffle=False),
        })
    t1, t2 = tasks

    model = create_model(config, data["audio_dim"], data["visual_dim"], device)
    params = sum(p.numel() for p in model.parameters())

    print(f"Task 1: {len(t1['ds'])} windows, Task 2: {len(t2['ds'])} windows")

    torch.cuda.reset_peak_memory_stats() if torch.cuda.is_available() else None

    def do_fl_cl_training():
        # Task 1
        print("  Task 1: Training...")
        train_with_ewc(model, t1["train"], t1["val"], device,
                        num_epochs=30, lr=0.0005, weight_decay=0.001,
                        patience=15, label_smoothing=0.05)

        # EWC
        print("  Computing Fisher Information Matrix...")
        ewc = EWC(model, t1["train"], device, exclude_prefixes=())

        # Replay buffer
        print(f"  Creating replay buffer ({cc['replay_buffer_size']} samples)...")
        buffer = ReplayBuffer(t1["ds"], buffer_size=cc["replay_buffer_size"])
        replay_loader = buffer.get_loader(batch_size=bs)

        # Task 2 with EWC + Replay
        print("  Task 2: Training with EWC + Replay...")
        train_with_ewc(model, t2["train"], t2["val"], device,
                        ewc=ewc, ewc_lambda=cc["ewc_lambda"],
                        replay_loader=replay_loader, replay_weight=cc["replay_weight"],
                        num_epochs=40, lr=0.00025, weight_decay=0.001,
                        patience=15, label_smoothing=0.05)

    _, train_time, train_energy = tracker.measure_energy(do_fl_cl_training)
    train_mem = torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else 0

    per_site = evaluate_sites(model, tasks, device)
    _, inf_time, inf_energy = measure_inference(model, t1["test"], device, tracker)

    return _result("BiLSTM + FL + CL (EWC+Replay)", params, per_site, {
        "Training Time (s)": round(train_time, 2),
        "Training Energy (J)": round(train_energy, 2),
        "Training Memory (MB)": round(train_mem, 2),
        "Inference Time (s)": round(inf_time, 4),
        "Inference Energy (J)": round(inf_energy, 4),
    })


# ─── Main ───

def main():
    print("=" * 60)
    print("BENCHMARK: All Experiments on GPU")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    config = load_config("configs/dvlog_config.yaml")
    set_seed(config.get("seed", 42))
    device = get_device("auto")
    print(f"Device: {device}")

    tracker = GPUEnergyTracker()
    hw_info = get_hardware_info()
    print("\nHardware:")
    for k, v in hw_info.items():
        print(f"  {k}: {v}")

    # Prepare data once
    print("\n--- Preparing data ---")
    data = prepare_all_data(config)

    all_results = []

    # 1. BiLSTM standalone
    set_seed(config.get("seed", 42))
    result = run_bilstm_standalone(config, data, device, tracker)
    all_results.append(result)
    print(f"  -> F1={result['F1-Score']:.4f}, Time={result['Training Time (s)']}s")

    # 2. FL with 2, 3, 4, 5, 6 clients
    for n in [2, 3, 4, 5, 6]:
        set_seed(config.get("seed", 42))
        result = run_fl_n_clients(n, config, data, device, tracker)
        all_results.append(result)
        print(f"  -> F1={result['F1-Score']:.4f}, Time={result['Training Time (s)']}s")

    # 3. FL + CL
    set_seed(config.get("seed", 42))
    result = run_fl_cl(config, data, device, tracker)
    all_results.append(result)
    print(f"  -> F1={result['F1-Score']:.4f}, Time={result['Training Time (s)']}s")

    # ─── Export to Excel (3 sheets, formatted) ───
    import pandas as pd
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter

    os.makedirs("results", exist_ok=True)
    excel_path = "results/benchmark_results.xlsx"

    df = pd.DataFrame(all_results)

    # Sheet 1: Model Performance
    perf_df = df[["Experiment", "Parameters", "Accuracy", "F1-Score", "Precision", "Recall"]].copy()
    for col in ["Accuracy", "F1-Score", "Precision", "Recall"]:
        perf_df[col] = perf_df[col].round(4)

    # Sheet 2: Resource Usage
    resource_df = df[["Experiment", "Training Time (s)", "Training Energy (J)",
                       "Training Memory (MB)", "Inference Time (s)", "Inference Energy (J)"]].copy()

    # Sheet 3: Hardware Info
    hw_df = pd.DataFrame(list(hw_info.items()), columns=["Property", "Value"])

    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        perf_df.to_excel(writer, sheet_name="Model Performance", index=False, startrow=1)
        resource_df.to_excel(writer, sheet_name="Resource Usage", index=False, startrow=1)
        hw_df.to_excel(writer, sheet_name="Hardware Info", index=False, startrow=1)

        wb = writer.book

        # ─── Styling ───
        header_font = Font(bold=True, size=12, color="FFFFFF")
        header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        title_font = Font(bold=True, size=14, color="1F3864")
        data_font = Font(size=11)
        border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        center = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")

        alt_fill = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")

        for sheet_name, title, df_sheet in [
            ("Model Performance", "Model Performance Metrics", perf_df),
            ("Resource Usage", "Training & Inference Resources", resource_df),
            ("Hardware Info", "Hardware Specifications", hw_df),
        ]:
            ws = wb[sheet_name]

            # Title row
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(df_sheet.columns))
            ws.cell(row=1, column=1, value=title).font = title_font
            ws.cell(row=1, column=1).alignment = Alignment(horizontal="center")

            # Header row (row 2)
            for col_idx in range(1, len(df_sheet.columns) + 1):
                cell = ws.cell(row=2, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center
                cell.border = border

            # Data rows
            for row_idx in range(3, len(df_sheet) + 3):
                for col_idx in range(1, len(df_sheet.columns) + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.font = data_font
                    cell.border = border
                    # First column left-aligned, rest centered
                    cell.alignment = left_align if col_idx == 1 else center
                    # Alternating row colors
                    if (row_idx - 3) % 2 == 1:
                        cell.fill = alt_fill

            # Auto-fit column widths
            for col_idx in range(1, len(df_sheet.columns) + 1):
                col_letter = get_column_letter(col_idx)
                max_len = max(
                    len(str(ws.cell(row=2, column=col_idx).value or "")),
                    max((len(str(ws.cell(row=r, column=col_idx).value or ""))
                         for r in range(3, len(df_sheet) + 3)), default=0)
                )
                ws.column_dimensions[col_letter].width = max(max_len + 4, 15)

            # Freeze header
            ws.freeze_panes = "A3"

    print(f"\n{'=' * 60}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print("\n--- Model Performance ---")
    print(perf_df.to_string(index=False))
    print("\n--- Resource Usage ---")
    print(resource_df.to_string(index=False))
    print(f"\nSaved to: {excel_path}")
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
