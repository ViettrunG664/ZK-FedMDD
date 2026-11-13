import argparse
import os
import numpy as np
import torch
from torch.utils.data import DataLoader

from src.utils import load_config, set_seed, get_device
from src.dvlog_data_loader import DVlogDataset
from src.dvlog_feature_extraction import DVlogFeatureExtractor, build_dvlog_windowed_dataset
from src.dataset import DepressionDataset
from src.model import MultimodalBiLSTM
from src.train import train
from src.evaluate import (
    evaluate_participant_level, print_evaluation_report,
    _aggregate_by_participant, find_optimal_threshold,
)
from src.visualization import save_all_plots


def run_v6(config):
    device = get_device(config["device"])
    print(f"Using device: {device}")

    dataset = DVlogDataset(config["data"]["dataset_dir"])
    extractor = DVlogFeatureExtractor(config)

    train_pids = dataset.get_participant_ids("train")
    valid_pids = dataset.get_participant_ids("valid")
    test_pids = dataset.get_participant_ids("test")
    train_labels = dataset.load_labels("train")
    valid_labels = dataset.load_labels("valid")
    test_labels = dataset.load_labels("test")

    print("Fitting scalers on D-vlog training data...")
    raw_segments = []
    for pid in train_pids:
        raw = dataset.load_participant(pid)
        processed = extractor.process_participant_segments(raw, normalize=False)
        raw_segments.append(processed)

    extractor.fit_scalers(
        [s["audio"] for s in raw_segments if s["audio"] is not None],
        [s["visual"] for s in raw_segments if s["visual"] is not None],
    )

    wc = config["preprocessing"]["windowing"]
    ws, stride = wc["window_size"], wc["stride"]
    print(f"Windowing: size={ws}, stride={stride}")

    print("Building train windows...")
    train_feat, train_y, train_wpids = build_dvlog_windowed_dataset(
        dataset, train_pids, train_labels, extractor, ws, stride)
    print("Building valid windows...")
    valid_feat, valid_y, valid_wpids = build_dvlog_windowed_dataset(
        dataset, valid_pids, valid_labels, extractor, ws, stride)
    print("Building test windows...")
    test_feat, test_y, test_wpids = build_dvlog_windowed_dataset(
        dataset, test_pids, test_labels, extractor, ws, stride)

    train_ds = DepressionDataset(train_feat["audio"], train_feat["visual"], train_feat["text"], train_y, ws, train_wpids)
    valid_ds = DepressionDataset(valid_feat["audio"], valid_feat["visual"], valid_feat["text"], valid_y, ws, valid_wpids)
    test_ds = DepressionDataset(test_feat["audio"], test_feat["visual"], test_feat["text"], test_y, ws, test_wpids)

    print(f"Train: {len(train_ds)} windows from {len(train_pids)} participants")
    print(f"Valid: {len(valid_ds)} windows from {len(valid_pids)} participants")
    print(f"Test:  {len(test_ds)} windows from {len(test_pids)} participants")

    bs = config["training"]["batch_size"]
    train_loader = DataLoader(train_ds, batch_size=bs, shuffle=True)
    valid_loader = DataLoader(valid_ds, batch_size=bs, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=bs, shuffle=False)

    sample = train_ds[0]
    audio_dim = sample["audio"].shape[-1]
    visual_dim = sample["visual"].shape[-1]
    print(f"Audio dim: {audio_dim}, Visual dim: {visual_dim}")

    mc = config["model"]
    model = MultimodalBiLSTM(
        audio_input_dim=audio_dim, visual_input_dim=visual_dim,
        hidden_dim=mc["hidden_dim"], num_layers=mc["num_layers"],
        dropout=mc["dropout"], fusion=mc["fusion"],
    ).to(device)
    print(f"Model params: {sum(p.numel() for p in model.parameters()):,}")

    save_dir = "checkpoints/dvlog"
    print("\n=== V6: Training on D-vlog ===")
    history = train(model, train_loader, valid_loader, config, device, save_dir=save_dir)

    model.load_state_dict(torch.load(f"{save_dir}/best_model.pt", map_location=device, weights_only=True))
    valid_probs, valid_labels_arr = _aggregate_by_participant(valid_loader, model, device)
    opt_thresh, opt_f1 = find_optimal_threshold(valid_probs, valid_labels_arr)
    print(f"Optimal threshold: {opt_thresh:.2f} (valid F1={opt_f1:.4f})")

    test_metrics = evaluate_participant_level(model, test_loader, device, threshold=opt_thresh)
    print(f"\n=== V6 Test Results ({test_metrics['n_participants']} participants, thresh={opt_thresh:.2f}) ===")
    print_evaluation_report(test_metrics)
    save_all_plots(history, test_metrics, save_dir="results/v6")

    return test_metrics, history


def main():
    parser = argparse.ArgumentParser(description="D-vlog Standalone Experiment")
    parser.add_argument("--config", type=str, default="configs/dvlog_config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    set_seed(config["seed"])

    print("=== V6: D-vlog Only ===\n")
    metrics, history = run_v6(config)
    print("\n=== Done! ===")


if __name__ == "__main__":
    main()
