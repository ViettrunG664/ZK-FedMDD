# fl_service/aggregate.py
# Federated Averaging (FedAvg) aggregation

import numpy as np
from model import create_model, get_model_weights, set_model_weights


def federated_average(updates):
    """
    Perform Federated Averaging on local model updates.

    Args:
        updates: list of dicts, each with:
            - "weights": dict of numpy arrays (model state_dict)
            - "data_size": int (number of training samples)

    Returns:
        dict of numpy arrays: aggregated global weights
    """
    if not updates:
        raise ValueError("No updates to aggregate")

    if len(updates) == 1:
        return updates[0]["weights"]

    # Total samples across all participants
    total_samples = sum(u["data_size"] for u in updates)

    if total_samples == 0:
        raise ValueError("Total data size is 0")

    # Weighted average of all model parameters
    avg_weights = {}
    first_weights = updates[0]["weights"]

    for key in first_weights:
        # Weighted sum: w_global = sum(w_i * n_i) / sum(n_i)
        weighted_sum = np.zeros_like(first_weights[key], dtype=np.float64)
        for update in updates:
            weight = update["data_size"] / total_samples
            weighted_sum += update["weights"][key].astype(np.float64) * weight
        avg_weights[key] = weighted_sum.astype(np.float32)

    return avg_weights


def aggregate_and_evaluate(updates, test_csv_path=None):
    """
    Aggregate weights and optionally evaluate the global model.

    Args:
        updates: list of local updates
        test_csv_path: optional CSV path for evaluation

    Returns:
        dict with: weights, participant_count, total_samples, accuracy (if test_csv provided)
    """
    avg_weights = federated_average(updates)

    result = {
        "weights": avg_weights,
        "participant_count": len(updates),
        "total_samples": sum(u["data_size"] for u in updates),
        "participant_accuracies": [u.get("accuracy", 0) for u in updates],
    }

    # Evaluate global model if test data provided
    if test_csv_path:
        from train import load_csv_data, normalize_features, create_sliding_windows
        import torch
        from torch.utils.data import DataLoader, TensorDataset

        model = create_model()
        set_model_weights(model, avg_weights)
        model.eval()

        features, labels = load_csv_data(test_csv_path)
        features = normalize_features(features)
        X, y = create_sliding_windows(features, labels)

        ds = TensorDataset(torch.tensor(X), torch.tensor(y))
        loader = DataLoader(ds, batch_size=64)

        correct = 0
        total = 0
        with torch.no_grad():
            for X_batch, y_batch in loader:
                output = model(X_batch).squeeze(-1)
                predicted = (output >= 0.5).float()
                correct += (predicted == y_batch).sum().item()
                total += y_batch.size(0)

        result["global_accuracy"] = correct / total if total > 0 else 0

    return result
