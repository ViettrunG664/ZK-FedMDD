import os
import copy
import numpy as np
import torch
from torch.utils.data import DataLoader, Subset
from collections import OrderedDict

from src.utils import load_config, set_seed, get_device
from src.dvlog_data_loader import DVlogDataset
from src.dvlog_feature_extraction import DVlogFeatureExtractor, build_dvlog_windowed_dataset
from src.dataset import DepressionDataset
from src.model import MultimodalBiLSTM
from src.evaluate import (
    _aggregate_by_participant, find_optimal_threshold,
    evaluate_participant_level, print_evaluation_report,
)


def split_pids(pids, n_clients, seed=42):
    np.random.seed(seed)
    shuffled = np.random.permutation(pids)
    splits = np.array_split(shuffled, n_clients)
    return [s.tolist() for s in splits]


def run_fl_dvlog(n_clients, config, device):
    dataset = DVlogDataset(config["data"]["dataset_dir"])
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

    val_feat, val_y, val_wp = build_dvlog_windowed_dataset(
        dataset, valid_pids, valid_labels, extractor, ws, stride)
    test_feat, test_y, test_wp = build_dvlog_windowed_dataset(
        dataset, test_pids, test_labels, extractor, ws, stride)

    val_ds = DepressionDataset(val_feat["audio"], val_feat["visual"], val_feat["text"], val_y, ws, val_wp)
    test_ds = DepressionDataset(test_feat["audio"], test_feat["visual"], test_feat["text"], test_y, ws, test_wp)
    val_loader = DataLoader(val_ds, batch_size=16, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=16, shuffle=False)

    client_pid_splits = split_pids(train_pids, n_clients)

    client_loaders = []
    client_sizes = []
    for i, client_pids in enumerate(client_pid_splits):
        client_labels = train_labels[train_labels["Participant_ID"].isin(client_pids)]
        feat, y, wp = build_dvlog_windowed_dataset(
            dataset, client_pids, client_labels, extractor, ws, stride)
        ds = DepressionDataset(feat["audio"], feat["visual"], feat["text"], y, ws, wp)
        loader = DataLoader(ds, batch_size=16, shuffle=True)
        client_loaders.append(loader)
        client_sizes.append(len(client_pids))
        print(f"  Client {i+1}: {len(client_pids)} participants, {len(ds)} windows")

    audio_dim = val_ds[0]["audio"].shape[-1]
    visual_dim = val_ds[0]["visual"].shape[-1]

    mc = config["model"]
    global_model = MultimodalBiLSTM(
        audio_input_dim=audio_dim, visual_input_dim=visual_dim,
        hidden_dim=mc["hidden_dim"], num_layers=mc["num_layers"],
        dropout=mc["dropout"], fusion=mc["fusion"],
    ).to(device)

    num_rounds = 30
    local_epochs = 3
    lr = 0.0005
    best_f1 = 0.0
    best_state = None
    best_round = 0
    total_samples = sum(client_sizes)

    for rnd in range(1, num_rounds + 1):
        global_state = copy.deepcopy(global_model.state_dict())
        client_states = []

        for i, loader in enumerate(client_loaders):
            local_model = MultimodalBiLSTM(
                audio_input_dim=audio_dim, visual_input_dim=visual_dim,
                hidden_dim=mc["hidden_dim"], num_layers=mc["num_layers"],
                dropout=mc["dropout"], fusion=mc["fusion"],
            ).to(device)
            local_model.load_state_dict(global_state)

            optimizer = torch.optim.Adam(local_model.parameters(), lr=lr, weight_decay=0.001)
            all_labels = torch.cat([b["label"] for b in loader]).squeeze()
            num_pos = all_labels.sum().item()
            num_neg = len(all_labels) - num_pos
            pos_weight = torch.tensor([num_neg / (num_pos + 1e-8)]).to(device)
            criterion = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)

            local_model.train()
            for ep in range(local_epochs):
                for batch in loader:
                    audio = batch["audio"].to(device) + torch.randn(batch["audio"].shape).to(device) * 0.05
                    visual = batch["visual"].to(device) + torch.randn(batch["visual"].shape).to(device) * 0.05
                    labels = batch["label"].to(device)
                    labels = labels * 0.95 + 0.025

                    optimizer.zero_grad()
                    logits = local_model(audio, visual)
                    loss = criterion(logits, labels)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(local_model.parameters(), 1.0)
                    optimizer.step()

            client_states.append(local_model.state_dict())

        avg_state = OrderedDict()
        for key in global_state.keys():
            avg_state[key] = sum(
                client_states[i][key].float() * (client_sizes[i] / total_samples)
                for i in range(n_clients)
            )
        global_model.load_state_dict(avg_state)

        if rnd % 5 == 0 or rnd == num_rounds:
            probs, labels = _aggregate_by_participant(val_loader, global_model, device)
            thresh, f1 = find_optimal_threshold(probs, labels)
            print(f"  Round {rnd:2d}/{num_rounds} | Val F1={f1:.4f} (thresh={thresh:.2f})")
            if f1 > best_f1:
                best_f1 = f1
                best_state = copy.deepcopy(global_model.state_dict())
                best_round = rnd

        if rnd - best_round >= 10 and rnd > 15:
            print(f"  Early stopping at round {rnd}")
            break

    global_model.load_state_dict(best_state)
    probs, labels = _aggregate_by_participant(val_loader, global_model, device)
    opt_thresh, _ = find_optimal_threshold(probs, labels)
    metrics = evaluate_participant_level(global_model, test_loader, device, threshold=opt_thresh)
    return metrics, opt_thresh


def main():
    config = load_config("configs/dvlog_config.yaml")
    set_seed(config["seed"])
    device = get_device(config["device"])
    print(f"Device: {device}\n")

    results = {}

    for n in [3, 4, 5, 6]:
        set_seed(config["seed"])
        print(f"\n{'='*60}")
        print(f"FL with {n} clients (D-vlog)")
        print(f"{'='*60}")

        metrics, thresh = run_fl_dvlog(n, config, device)
        results[n] = metrics

        print(f"\n--- {n} clients: F1={metrics['f1']:.4f} Recall={metrics['recall']:.4f} "
              f"Precision={metrics['precision']:.4f} Acc={metrics['accuracy']:.4f} (thresh={thresh:.2f}) ---")
        print_evaluation_report(metrics)

    print(f"\n{'='*60}")
    print("SUMMARY: FL on D-vlog with varying clients")
    print(f"{'='*60}")
    print(f"{'Clients':>8} {'F1':>8} {'Recall':>8} {'Precision':>10} {'Accuracy':>10}")
    print("-" * 50)
    for n in [3, 4, 5, 6]:
        m = results[n]
        print(f"{n:>8} {m['f1']:>8.4f} {m['recall']:>8.4f} {m['precision']:>10.4f} {m['accuracy']:>10.4f}")

    import pandas as pd
    from sklearn.metrics import confusion_matrix
    excel_path = "results/experiment_results.xlsx"
    try:
        existing = pd.read_excel(excel_path)
        existing = existing[~existing["Version"].str.contains("FL-.*client", na=False, regex=True)]
    except FileNotFoundError:
        existing = pd.DataFrame()

    rows = []
    for n in [3, 4, 5, 6]:
        m = results[n]
        cm = confusion_matrix(m["labels"], m["predictions"])
        tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (0, 0, 0, 0)
        rows.append({
            "Version": f"FL-{n}client (D-vlog)",
            "Modalities": "Audio + Visual",
            "Train Samples": f"D-vlog split into {n} clients",
            "Params": "257,668",
            "Test F1": m["f1"], "Test Recall": m["recall"],
            "Test Precision": m["precision"], "Test Accuracy": m["accuracy"],
            "Test AUC": "", "TP": int(tp), "FP": int(fp), "FN": int(fn), "TN": int(tn),
            "Val F1 Peak": "", "Technique": f"Federated Learning ({n} clients)",
            "Notes": f"D-vlog only, {n} clients, FedAvg",
        })

    updated = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
    updated.to_excel(excel_path, index=False, sheet_name="Experiments")
    print(f"\nResults saved to {excel_path}")


if __name__ == "__main__":
    main()
