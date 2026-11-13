import argparse
import os
import numpy as np
import torch
from torch.utils.data import DataLoader

from src.utils import load_config, set_seed, get_device
from src.federated import FederatedModel, FLClient, FLServer
from src.dataset import DepressionDataset
from src.dvlog_data_loader import DVlogDataset
from src.dvlog_feature_extraction import DVlogFeatureExtractor, build_dvlog_windowed_dataset
from src.evaluate import (
    _aggregate_by_participant, find_optimal_threshold,
    evaluate_participant_level, print_evaluation_report,
)
from src.visualization import save_all_plots


def prepare_dvlog_client(config, client_cfg, shared_dim, device):
    dataset = DVlogDataset(client_cfg["dataset_dir"])
    extractor = DVlogFeatureExtractor(config)

    train_pids = dataset.get_participant_ids("train")
    valid_pids = dataset.get_participant_ids("valid")
    test_pids = dataset.get_participant_ids("test")
    train_labels = dataset.load_labels("train")
    valid_labels = dataset.load_labels("valid")
    test_labels = dataset.load_labels("test")

    raw_segs = []
    for pid in train_pids:
        raw = dataset.load_participant(pid)
        processed = extractor.process_participant_segments(raw, normalize=False)
        raw_segs.append(processed)
    extractor.fit_scalers(
        [s["audio"] for s in raw_segs if s["audio"] is not None],
        [s["visual"] for s in raw_segs if s["visual"] is not None],
    )

    wc = config["preprocessing"]["windowing"]
    ws, stride = wc["window_size"], wc["stride"]

    train_feat, train_y, train_wp = build_dvlog_windowed_dataset(
        dataset, train_pids, train_labels, extractor, ws, stride)
    val_feat, val_y, val_wp = build_dvlog_windowed_dataset(
        dataset, valid_pids, valid_labels, extractor, ws, stride)
    test_feat, test_y, test_wp = build_dvlog_windowed_dataset(
        dataset, test_pids, test_labels, extractor, ws, stride)

    train_ds = DepressionDataset(train_feat["audio"], train_feat["visual"], train_feat["text"], train_y, ws, train_wp)
    val_ds = DepressionDataset(val_feat["audio"], val_feat["visual"], val_feat["text"], val_y, ws, val_wp)
    test_ds = DepressionDataset(test_feat["audio"], test_feat["visual"], test_feat["text"], test_y, ws, test_wp)

    audio_dim = train_ds[0]["audio"].shape[-1]
    visual_dim = train_ds[0]["visual"].shape[-1]

    model = FederatedModel(
        local_audio_dim=audio_dim, local_visual_dim=visual_dim,
        shared_dim=shared_dim, hidden_dim=config["model"]["hidden_dim"],
        num_layers=config["model"]["num_layers"], dropout=config["model"]["dropout"],
    ).to(device)

    bs = config["training"]["batch_size"]
    return model, {
        "train": DataLoader(train_ds, batch_size=bs, shuffle=True),
        "val": DataLoader(val_ds, batch_size=bs, shuffle=False),
        "test": DataLoader(test_ds, batch_size=bs, shuffle=False),
        "audio_dim": audio_dim, "visual_dim": visual_dim,
        "n_train": len(train_pids), "n_test": len(test_pids),
    }


def evaluate_client(client: FLClient, test_loader: DataLoader, device: torch.device):
    val_probs, val_labels = _aggregate_by_participant(client.val_loader, client.model, device)
    opt_thresh, _ = find_optimal_threshold(val_probs, val_labels)
    metrics = evaluate_participant_level(client.model, test_loader, device, threshold=opt_thresh)
    return metrics, opt_thresh


def main():
    parser = argparse.ArgumentParser(description="Federated Learning Depression Detection (D-vlog)")
    parser.add_argument("--config", type=str, default="configs/federated_config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    set_seed(config["seed"])
    device = get_device(config["device"])
    print(f"Using device: {device}")

    fl_cfg = config["federated"]
    shared_dim = fl_cfg["shared_dim"]
    num_rounds = fl_cfg["num_rounds"]
    local_epochs = fl_cfg["local_epochs"]
    lr = config["training"]["learning_rate"]
    wd = config["training"]["weight_decay"]
    ls = config["training"]["label_smoothing"]

    print("=" * 60)
    print("Preparing D-vlog clients...")
    print("=" * 60)

    clients_data = {}
    fl_clients = []

    for client_name, client_cfg in config["clients"].items():
        print(f"\n[Client] {client_name}...")
        model, data = prepare_dvlog_client(config, client_cfg, shared_dim, device)
        client = FLClient(client_name, model, data["train"], data["val"], device, lr, wd, ls)
        fl_clients.append(client)
        clients_data[client_name] = data
        print(f"  Audio: {data['audio_dim']}d, Visual: {data['visual_dim']}d")
        print(f"  Train: {len(data['train'].dataset)} windows, Test: {len(data['test'].dataset)} windows")

    print(f"\nTotal clients: {len(fl_clients)}")
    print(f"Total train participants: {sum(d['n_train'] for d in clients_data.values())}")

    server = FLServer(fl_clients)

    print("\n" + "=" * 60)
    print(f"Federated Training: {num_rounds} rounds, {local_epochs} local epochs each")
    print("=" * 60)

    best_avg_f1 = 0.0
    best_round = 0
    history = {"rounds": [], "losses": {c.name: [] for c in fl_clients}}

    for rnd in range(1, num_rounds + 1):
        losses = server.run_round(local_epochs=local_epochs)

        loss_str = " | ".join(f"{name}: {loss:.4f}" for name, loss in losses.items())
        print(f"Round {rnd:3d}/{num_rounds} | {loss_str}")

        for name, loss in losses.items():
            history["losses"][name].append(loss)
        history["rounds"].append(rnd)

        if rnd % 5 == 0 or rnd == num_rounds:
            f1s = []
            for client in fl_clients:
                test_loader = clients_data[client.name]["test"]
                metrics, thresh = evaluate_client(client, test_loader, device)
                f1s.append(metrics["f1"])
                print(f"  [{client.name}] F1={metrics['f1']:.4f} Recall={metrics['recall']:.4f} "
                      f"Precision={metrics['precision']:.4f} (thresh={thresh:.2f})")

            avg_f1 = np.mean(f1s)
            if avg_f1 > best_avg_f1:
                best_avg_f1 = avg_f1
                best_round = rnd
                os.makedirs("checkpoints/federated", exist_ok=True)
                for client in fl_clients:
                    torch.save(client.model.state_dict(),
                               f"checkpoints/federated/best_{client.name.replace('-','_')}.pt")

        if rnd - best_round >= 10 and rnd > 15:
            print(f"No improvement for 10 rounds. Stopping at round {rnd}.")
            break

    print("\n" + "=" * 60)
    print(f"Final Evaluation (best round: {best_round})")
    print("=" * 60)

    all_metrics = {}
    for client in fl_clients:
        ckpt = f"checkpoints/federated/best_{client.name.replace('-','_')}.pt"
        client.model.load_state_dict(torch.load(ckpt, map_location=device, weights_only=True))

        test_loader = clients_data[client.name]["test"]
        metrics, thresh = evaluate_client(client, test_loader, device)
        all_metrics[client.name] = metrics

        print(f"\n=== {client.name} Test Results (thresh={thresh:.2f}) ===")
        print_evaluation_report(metrics)

    first_name = fl_clients[0].name
    if first_name in all_metrics:
        save_all_plots(
            {"train_loss": history["losses"].get(first_name, []),
             "val_loss": [], "val_f1": []},
            all_metrics[first_name], save_dir="results/federated"
        )

    print("\n=== Federated Learning Done! ===")


if __name__ == "__main__":
    main()
