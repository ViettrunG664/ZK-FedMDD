"""
Evaluation and metrics for depression detection.
"""

import torch
import torch.nn as nn
import numpy as np
from torch.utils.data import DataLoader
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    confusion_matrix,
    classification_report,
    mean_absolute_error,
)
from collections import defaultdict
from typing import Dict, Tuple

from src.model import MultimodalBiLSTM


def evaluate(
    model: MultimodalBiLSTM,
    dataloader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
) -> Dict[str, float]:
    """Evaluate model on a dataset.

    Returns:
        Dict with loss, accuracy, f1, precision, recall.
    """
    model.eval()
    total_loss = 0.0
    all_preds = []
    all_labels = []
    all_probs = []

    with torch.no_grad():
        for batch in dataloader:
            audio = batch["audio"].to(device)
            visual = batch["visual"].to(device)
            text = batch["text"].to(device) if "text" in batch else None
            labels = batch["label"].to(device)

            logits = model(audio, visual, text)
            loss = criterion(logits, labels)
            total_loss += loss.item() * audio.size(0)

            probs = torch.sigmoid(logits).cpu().numpy().flatten()
            preds = (probs >= 0.5).astype(int)

            all_probs.extend(probs)
            all_preds.extend(preds)
            all_labels.extend(labels.cpu().numpy().flatten().astype(int))

    all_preds = np.array(all_preds)
    all_labels = np.array(all_labels)

    avg_loss = total_loss / len(dataloader.dataset)

    return {
        "loss": avg_loss,
        "accuracy": accuracy_score(all_labels, all_preds),
        "f1": f1_score(all_labels, all_preds, zero_division=0),
        "precision": precision_score(all_labels, all_preds, zero_division=0),
        "recall": recall_score(all_labels, all_preds, zero_division=0),
        "predictions": all_preds,
        "labels": all_labels,
        "probabilities": np.array(all_probs),
    }


def _aggregate_by_participant(dataloader, model, device):
    """Collect per-participant averaged probabilities and labels."""
    model.eval()
    all_probs, all_labels, all_pids = [], [], []

    with torch.no_grad():
        for batch in dataloader:
            audio = batch["audio"].to(device)
            visual = batch["visual"].to(device)
            text = batch["text"].to(device) if "text" in batch else None
            labels = batch["label"]
            pids = batch["participant_id"]

            logits = model(audio, visual, text)
            probs = torch.sigmoid(logits).cpu().numpy().flatten()

            all_probs.extend(probs)
            all_labels.extend(labels.numpy().flatten().astype(int))
            all_pids.extend(pids if isinstance(pids, list) else pids.tolist())

    pid_probs = defaultdict(list)
    pid_labels = {}
    for pid, prob, label in zip(all_pids, all_probs, all_labels):
        pid = int(pid)
        pid_probs[pid].append(float(prob))
        pid_labels[pid] = int(label)

    final_probs, final_labels = [], []
    for pid in sorted(pid_probs.keys()):
        final_probs.append(np.mean(pid_probs[pid]))
        final_labels.append(pid_labels[pid])

    return np.array(final_probs), np.array(final_labels)


def find_optimal_threshold(probs: np.ndarray, labels: np.ndarray) -> Tuple[float, float]:
    """Find threshold that maximizes F1 score on given probabilities/labels."""
    best_f1 = 0.0
    best_thresh = 0.5
    for thresh in np.arange(0.20, 0.80, 0.01):
        preds = (probs >= thresh).astype(int)
        f1 = f1_score(labels, preds, zero_division=0)
        if f1 > best_f1:
            best_f1 = f1
            best_thresh = thresh
    return best_thresh, best_f1


def evaluate_participant_level(
    model: MultimodalBiLSTM,
    dataloader: DataLoader,
    device: torch.device,
    threshold: float = 0.5,
) -> Dict:
    """Evaluate with participant-level prediction aggregation."""
    final_probs, final_labels = _aggregate_by_participant(dataloader, model, device)
    final_preds = (final_probs >= threshold).astype(int)

    return {
        "accuracy": accuracy_score(final_labels, final_preds),
        "f1": f1_score(final_labels, final_preds, zero_division=0),
        "precision": precision_score(final_labels, final_preds, zero_division=0),
        "recall": recall_score(final_labels, final_preds, zero_division=0),
        "predictions": final_preds,
        "labels": final_labels,
        "probabilities": np.array(final_probs),
        "n_participants": len(final_labels),
    }


def print_evaluation_report(metrics: Dict) -> None:
    """Print a formatted evaluation report."""
    print("=" * 50)
    print("EVALUATION RESULTS")
    print("=" * 50)
    if "loss" in metrics:
        print(f"  Loss:      {metrics['loss']:.4f}")
    print(f"  Accuracy:  {metrics['accuracy']:.4f}")
    print(f"  F1 Score:  {metrics['f1']:.4f}")
    print(f"  Precision: {metrics['precision']:.4f}")
    print(f"  Recall:    {metrics['recall']:.4f}")
    print()

    # Confusion matrix
    cm = confusion_matrix(metrics["labels"], metrics["predictions"])
    print("Confusion Matrix:")
    print(f"  TN={cm[0][0]:3d}  FP={cm[0][1]:3d}")
    print(f"  FN={cm[1][0]:3d}  TP={cm[1][1]:3d}")
    print()

    # Full classification report
    print("Classification Report:")
    print(
        classification_report(
            metrics["labels"],
            metrics["predictions"],
            target_names=["Not Depressed", "Depressed"],
            zero_division=0,
        )
    )
