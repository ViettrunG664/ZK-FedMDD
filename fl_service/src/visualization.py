import os
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import confusion_matrix, roc_curve, auc
from typing import Dict, List


def plot_training_history(history: Dict[str, List[float]], save_dir: str = "results"):
    os.makedirs(save_dir, exist_ok=True)
    epochs = range(1, len(history["train_loss"]) + 1)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    axes[0].plot(epochs, history["train_loss"], "b-o", label="Train Loss", markersize=3)
    axes[0].plot(epochs, history["val_loss"], "r-o", label="Val Loss", markersize=3)
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].set_title("Training & Validation Loss")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(epochs, history["val_f1"], "g-o", label="Val F1", markersize=3)
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("F1 Score")
    axes[1].set_title("Validation F1 Score")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, "training_curves.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved training curves to {save_dir}/training_curves.png")


def plot_confusion_matrix(labels: np.ndarray, predictions: np.ndarray, save_dir: str = "results"):
    os.makedirs(save_dir, exist_ok=True)
    cm = confusion_matrix(labels, predictions)

    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(
        cm, annot=True, fmt="d", cmap="Blues",
        xticklabels=["Not Depressed", "Depressed"],
        yticklabels=["Not Depressed", "Depressed"],
        ax=ax,
    )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Confusion Matrix")

    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, "confusion_matrix.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved confusion matrix to {save_dir}/confusion_matrix.png")


def plot_roc_curve(labels: np.ndarray, probabilities: np.ndarray, save_dir: str = "results"):
    os.makedirs(save_dir, exist_ok=True)
    fpr, tpr, _ = roc_curve(labels, probabilities)
    roc_auc = auc(fpr, tpr)

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(fpr, tpr, "b-", linewidth=2, label=f"ROC (AUC = {roc_auc:.3f})")
    ax.plot([0, 1], [0, 1], "r--", alpha=0.5, label="Random")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("ROC Curve")
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, "roc_curve.png"), dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved ROC curve to {save_dir}/roc_curve.png (AUC={roc_auc:.3f})")
    return roc_auc


def save_all_plots(history: Dict, test_metrics: Dict, save_dir: str = "results"):
    plot_training_history(history, save_dir)
    plot_confusion_matrix(test_metrics["labels"], test_metrics["predictions"], save_dir)
    roc_auc = plot_roc_curve(test_metrics["labels"], test_metrics["probabilities"], save_dir)
    return roc_auc
