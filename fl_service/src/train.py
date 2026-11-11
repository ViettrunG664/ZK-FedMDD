"""
Training loop for the Multimodal BiLSTM depression detection model.
"""

import os
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from tqdm import tqdm
from typing import Dict, Optional

from src.model import MultimodalBiLSTM
from src.evaluate import evaluate


class EarlyStopping:
    """Stop training when validation loss stops improving."""

    def __init__(self, patience: int = 10, min_delta: float = 1e-4):
        self.patience = patience
        self.min_delta = min_delta
        self.counter = 0
        self.best_loss = None
        self.should_stop = False

    def step(self, val_loss: float) -> bool:
        if self.best_loss is None:
            self.best_loss = val_loss
            return False

        if val_loss < self.best_loss - self.min_delta:
            self.best_loss = val_loss
            self.counter = 0
        else:
            self.counter += 1
            if self.counter >= self.patience:
                self.should_stop = True

        return self.should_stop


def train_one_epoch(
    model: MultimodalBiLSTM,
    dataloader: DataLoader,
    criterion: nn.Module,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    label_smoothing: float = 0.0,
) -> float:
    """Train for one epoch. Returns average loss."""
    model.train()
    total_loss = 0.0

    for batch in dataloader:
        audio = batch["audio"].to(device)
        visual = batch["visual"].to(device)
        text = batch["text"].to(device) if "text" in batch else None
        labels = batch["label"].to(device)

        # Data augmentation: add Gaussian noise to audio/visual features
        if model.training:
            audio = audio + torch.randn_like(audio) * 0.05
            visual = visual + torch.randn_like(visual) * 0.05

        # Label smoothing: 0 → smoothing, 1 → 1-smoothing
        if label_smoothing > 0:
            labels = labels * (1 - label_smoothing) + 0.5 * label_smoothing

        optimizer.zero_grad()
        logits = model(audio, visual, text)
        loss = criterion(logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        total_loss += loss.item() * audio.size(0)

    return total_loss / len(dataloader.dataset)


def train(
    model: MultimodalBiLSTM,
    train_loader: DataLoader,
    val_loader: DataLoader,
    config: dict,
    device: torch.device,
    save_dir: str = "checkpoints",
) -> Dict[str, list]:
    """Full training loop with validation and early stopping.

    Returns:
        history: Dict with train_loss, val_loss, val_f1 per epoch.
    """
    os.makedirs(save_dir, exist_ok=True)

    # Loss function with class weights
    weight = None
    if config["training"].get("class_weight"):
        # pos_weight = num_negative / num_positive
        all_labels = []
        for batch in train_loader:
            all_labels.append(batch["label"])
        all_labels = torch.cat(all_labels).squeeze()
        num_pos = all_labels.sum().item()
        num_neg = len(all_labels) - num_pos
        pos_weight = torch.tensor([num_neg / (num_pos + 1e-8)]).to(device)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    else:
        criterion = nn.BCEWithLogitsLoss()

    # Optimizer
    lr = config["training"]["learning_rate"]
    wd = config["training"]["weight_decay"]
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=wd)

    # Scheduler
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=5
    )

    # Early stopping
    early_stopping = EarlyStopping(patience=config["training"]["patience"])

    history = {"train_loss": [], "val_loss": [], "val_f1": []}
    best_f1 = 0.0

    label_smoothing = config["training"].get("label_smoothing", 0.0)
    num_epochs = config["training"]["num_epochs"]
    for epoch in range(1, num_epochs + 1):
        # Train
        train_loss = train_one_epoch(model, train_loader, criterion, optimizer, device, label_smoothing)

        # Validate
        val_metrics = evaluate(model, val_loader, criterion, device)
        val_loss = val_metrics["loss"]
        val_f1 = val_metrics["f1"]

        scheduler.step(val_loss)
        current_lr = optimizer.param_groups[0]["lr"]

        # Log
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_f1"].append(val_f1)

        print(
            f"Epoch {epoch:3d}/{num_epochs} | "
            f"Train Loss: {train_loss:.4f} | "
            f"Val Loss: {val_loss:.4f} | "
            f"Val F1: {val_f1:.4f} | "
            f"LR: {current_lr:.6f}"
        )

        # Save best model
        if val_f1 > best_f1:
            best_f1 = val_f1
            torch.save(model.state_dict(), os.path.join(save_dir, "best_model.pt"))
            print(f"  -> Saved best model (F1: {best_f1:.4f})")

        # Early stopping
        if early_stopping.step(val_loss):
            print(f"Early stopping at epoch {epoch}")
            break

    # Save final model
    torch.save(model.state_dict(), os.path.join(save_dir, "final_model.pt"))
    return history
