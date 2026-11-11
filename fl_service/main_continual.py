"""
Continual Learning for Depression Detection (D-vlog only).
Train sequentially on two disjoint subsets of D-vlog training data.

Task 1: First half of D-vlog train participants
Task 2: Second half of D-vlog train participants
Validate/Test: Shared valid and test sets

V11a: Naive Sequential (no protection -> catastrophic forgetting)
V11b: EWC (Elastic Weight Consolidation -> prevent forgetting)
V11c: EWC + Replay Buffer (strongest protection)

Usage:
    python main_continual.py --config configs/continual_config.yaml
"""

import argparse
import os
import copy
import numpy as np
import torch
from torch.utils.data import DataLoader

from src.utils import load_config, set_seed, get_device
from src.model import MultimodalBiLSTM
from src.continual import EWC, ReplayBuffer, train_with_ewc
from src.dataset import DepressionDataset
from src.dvlog_data_loader import DVlogDataset
from src.dvlog_feature_extraction import DVlogFeatureExtractor, build_dvlog_windowed_dataset
from src.evaluate import (
    _aggregate_by_participant, find_optimal_threshold,
    evaluate_participant_level, print_evaluation_report,
)


def split_train_pids(pids, seed=42):
    """Split training PIDs into two roughly equal halves."""
    np.random.seed(seed)
    shuffled = np.random.permutation(pids)
    mid = len(shuffled) // 2
    return shuffled[:mid].tolist(), shuffled[mid:].tolist()


def prepare_dvlog_data(config):
    """Prepare D-vlog data: split train into Task1/Task2, shared valid/test."""
    dataset = DVlogDataset(config["data"]["dataset_dir"])
    extractor = DVlogFeatureExtractor(config)

    all_train_pids = dataset.get_participant_ids("train")
    valid_pids = dataset.get_participant_ids("valid")
    test_pids = dataset.get_participant_ids("test")

    # Split train into two halves
    task1_pids, task2_pids = split_train_pids(all_train_pids, seed=config["seed"])
    print(f"Task 1: {len(task1_pids)} participants")
    print(f"Task 2: {len(task2_pids)} participants")
    print(f"Valid:  {len(valid_pids)} participants")
    print(f"Test:   {len(test_pids)} participants")

    # Fit scalers on ALL train data (both tasks)
    print("Fitting scalers on all D-vlog training data...")
    raw_segs = []
    for pid in all_train_pids:
        raw = dataset.load_participant(pid)
        processed = extractor.process_participant_segments(raw, normalize=False)
        raw_segs.append(processed)
    extractor.fit_scalers(
        [s["audio"] for s in raw_segs if s["audio"] is not None],
        [s["visual"] for s in raw_segs if s["visual"] is not None],
    )

    wc = config["preprocessing"]["windowing"]
    ws, stride = wc["window_size"], wc["stride"]

    all_labels = dataset.load_labels()

    # Build windowed datasets
    print("Building Task 1 windows...")
    t1_feat, t1_y, t1_wp = build_dvlog_windowed_dataset(
        dataset, task1_pids, all_labels, extractor, ws, stride)
    print("Building Task 2 windows...")
    t2_feat, t2_y, t2_wp = build_dvlog_windowed_dataset(
        dataset, task2_pids, all_labels, extractor, ws, stride)
    print("Building valid windows...")
    val_feat, val_y, val_wp = build_dvlog_windowed_dataset(
        dataset, valid_pids, dataset.load_labels("valid"), extractor, ws, stride)
    print("Building test windows...")
    test_feat, test_y, test_wp = build_dvlog_windowed_dataset(
        dataset, test_pids, dataset.load_labels("test"), extractor, ws, stride)

    t1_ds = DepressionDataset(t1_feat["audio"], t1_feat["visual"], t1_feat["text"], t1_y, ws, t1_wp)
    t2_ds = DepressionDataset(t2_feat["audio"], t2_feat["visual"], t2_feat["text"], t2_y, ws, t2_wp)
    val_ds = DepressionDataset(val_feat["audio"], val_feat["visual"], val_feat["text"], val_y, ws, val_wp)
    test_ds = DepressionDataset(test_feat["audio"], test_feat["visual"], test_feat["text"], test_y, ws, test_wp)

    audio_dim = t1_ds[0]["audio"].shape[-1]
    visual_dim = t1_ds[0]["visual"].shape[-1]

    print(f"Task 1: {len(t1_ds)} windows, Task 2: {len(t2_ds)} windows")
    print(f"Valid: {len(val_ds)} windows, Test: {len(test_ds)} windows")
    print(f"Audio dim: {audio_dim}, Visual dim: {visual_dim}")

    return t1_ds, t2_ds, val_ds, test_ds, audio_dim, visual_dim


def evaluate_model(model, val_loader, test_loader, device, label=""):
    """Evaluate model with optimal threshold from validation set."""
    probs, labels = _aggregate_by_participant(val_loader, model, device)
    thresh, val_f1 = find_optimal_threshold(probs, labels)
    metrics = evaluate_participant_level(model, test_loader, device, threshold=thresh)
    print(f"  {label}: F1={metrics['f1']:.4f} Recall={metrics['recall']:.4f} "
          f"Precision={metrics['precision']:.4f} Acc={metrics['accuracy']:.4f} (thresh={thresh:.2f})")
    return metrics, thresh


def run_continual(config, mode: str):
    """
    Run continual learning experiment on D-vlog only.

    Args:
        mode: "naive" (V11a), "ewc" (V11b), or "ewc_replay" (V11c)
    """
    device = get_device(config["device"])
    mc = config["model"]
    tc = config["training"]
    cc = config["continual"]
    bs = tc["batch_size"]

    # --- Prepare data ---
    t1_ds, t2_ds, val_ds, test_ds, audio_dim, visual_dim = prepare_dvlog_data(config)

    t1_train_loader = DataLoader(t1_ds, batch_size=bs, shuffle=True)
    t2_train_loader = DataLoader(t2_ds, batch_size=bs, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=bs, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=bs, shuffle=False)

    # --- Create model (same dims for both tasks, no projection needed) ---
    model = MultimodalBiLSTM(
        audio_input_dim=audio_dim, visual_input_dim=visual_dim,
        hidden_dim=mc["hidden_dim"], num_layers=mc["num_layers"],
        dropout=mc["dropout"], fusion=mc.get("fusion", "attention"),
    ).to(device)
    print(f"Model params: {sum(p.numel() for p in model.parameters()):,}")

    # ========== TASK 1: Train on first half ==========
    print(f"\n{'='*60}")
    print(f"TASK 1: Training on D-vlog subset 1 ({mode})")
    print(f"{'='*60}")

    train_with_ewc(
        model, t1_train_loader, val_loader, device,
        num_epochs=tc["task1_epochs"], lr=tc["learning_rate"],
        weight_decay=tc["weight_decay"], patience=tc["patience"],
        label_smoothing=tc["label_smoothing"],
    )

    # Evaluate after Task 1
    print("\n--- After Task 1 ---")
    t1_metrics, t1_thresh = evaluate_model(model, val_loader, test_loader, device, "Test")

    # ========== Prepare for Task 2 ==========
    # Compute EWC if needed
    ewc = None
    if mode in ("ewc", "ewc_replay"):
        print("\nComputing Fisher Information Matrix (EWC)...")
        ewc = EWC(model, t1_train_loader, device, exclude_prefixes=())

    # Replay buffer from Task 1
    replay_loader = None
    if mode == "ewc_replay":
        print(f"Creating replay buffer from Task 1 ({cc['replay_buffer_size']} samples)...")
        buffer = ReplayBuffer(t1_ds, buffer_size=cc["replay_buffer_size"])
        replay_loader = buffer.get_loader(batch_size=bs)

    # ========== TASK 2: Continue on second half ==========
    print(f"\n{'='*60}")
    print(f"TASK 2: Continue training on D-vlog subset 2 ({mode})")
    if ewc:
        print(f"  EWC lambda: {cc['ewc_lambda']}")
    if replay_loader:
        print(f"  Replay: {cc['replay_buffer_size']} samples, weight={cc['replay_weight']}")
    print(f"{'='*60}")

    train_with_ewc(
        model, t2_train_loader, val_loader, device,
        ewc=ewc, ewc_lambda=cc["ewc_lambda"],
        replay_loader=replay_loader, replay_weight=cc["replay_weight"],
        num_epochs=tc["task2_epochs"], lr=tc["learning_rate"] * 0.5,
        weight_decay=tc["weight_decay"], patience=tc["patience"],
        label_smoothing=tc["label_smoothing"],
    )

    # Evaluate after Task 2
    print("\n--- After Task 2 ---")
    t2_metrics, t2_thresh = evaluate_model(model, val_loader, test_loader, device, "Test")

    return {
        "test_after_t1": t1_metrics,
        "test_after_t2": t2_metrics,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=str, default="configs/continual_config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    set_seed(config["seed"])

    print("=" * 60)
    print("CONTINUAL LEARNING EXPERIMENTS (D-vlog only)")
    print("=" * 60)

    all_results = {}

    for mode in ["naive", "ewc", "ewc_replay"]:
        set_seed(config["seed"])
        mode_name = {"naive": "V11a Naive", "ewc": "V11b EWC", "ewc_replay": "V11c EWC+Replay"}[mode]
        print(f"\n\n{'#'*60}")
        print(f"# {mode_name}")
        print(f"{'#'*60}")

        results = run_continual(config, mode)
        all_results[mode] = results

        print(f"\n{'='*60}")
        print(f"SUMMARY - {mode_name}")
        print(f"{'='*60}")
        t1_f1 = results['test_after_t1']['f1']
        t2_f1 = results['test_after_t2']['f1']
        print(f"  Test after Task 1: F1={t1_f1:.4f}")
        print(f"  Test after Task 2: F1={t2_f1:.4f}  "
              f"({'FORGOT' if t2_f1 < t1_f1 - 0.05 else 'OK'})")

    # Final comparison table
    print(f"\n\n{'='*60}")
    print("FINAL COMPARISON")
    print(f"{'='*60}")
    print(f"{'Method':<20} {'After T1 F1':>12} {'After T2 F1':>12} {'Forgot?':>10}")
    print("-" * 55)
    for mode in ["naive", "ewc", "ewc_replay"]:
        r = all_results[mode]
        label = {"naive": "V11a Naive", "ewc": "V11b EWC", "ewc_replay": "V11c EWC+Replay"}[mode]
        t1_f1 = r["test_after_t1"]["f1"]
        t2_f1 = r["test_after_t2"]["f1"]
        forgot = t2_f1 < t1_f1 - 0.05
        print(f"{label:<20} {t1_f1:>12.4f} {t2_f1:>12.4f} {'YES' if forgot else 'NO':>10}")

    print("\n=== Continual Learning Done! ===")


if __name__ == "__main__":
    main()
