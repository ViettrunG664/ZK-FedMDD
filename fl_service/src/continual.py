"""
Continual Learning for Depression Detection.

Implements:
  - EWC (Elastic Weight Consolidation): penalizes changes to important weights
  - Replay Buffer: stores a subset of old data for rehearsal
"""

import copy
import torch
import torch.nn as nn
import numpy as np
from torch.utils.data import DataLoader, Subset
from typing import Dict, List, Optional


class EWC:
    """Elastic Weight Consolidation.

    After training on task A, computes Fisher Information Matrix to identify
    important weights. When training on task B, adds penalty to prevent
    changing those important weights.
    """

    def __init__(self, model: nn.Module, dataloader: DataLoader, device: torch.device,
                 exclude_prefixes=("audio_proj", "visual_proj")):
        self.model = model
        self.device = device
        self.exclude_prefixes = exclude_prefixes
        self.params = {
            n: p.clone().detach() for n, p in model.named_parameters()
            if p.requires_grad and not any(n.startswith(ex) for ex in exclude_prefixes)
        }
        self.fisher = self._compute_fisher(dataloader)

    def _compute_fisher(self, dataloader: DataLoader) -> Dict[str, torch.Tensor]:
        """Compute Fisher Information Matrix from task A data."""
        fisher = {
            n: torch.zeros_like(p) for n, p in self.model.named_parameters()
            if p.requires_grad and not any(n.startswith(ex) for ex in self.exclude_prefixes)
        }

        self.model.train()  # Must be in train mode for cuDNN RNN backward
        num_samples = 0

        for batch in dataloader:
            audio = batch["audio"].to(self.device)
            visual = batch["visual"].to(self.device)
            text = batch["text"].to(self.device) if "text" in batch else None
            labels = batch["label"].to(self.device)

            self.model.zero_grad()
            logits = self.model(audio, visual, text)
            loss = nn.functional.binary_cross_entropy_with_logits(logits, labels)
            loss.backward()

            for n, p in self.model.named_parameters():
                if p.requires_grad and p.grad is not None and n in fisher:
                    fisher[n] += p.grad.data.pow(2) * audio.size(0)

            num_samples += audio.size(0)

        # Average
        for n in fisher:
            fisher[n] /= num_samples

        return fisher

    def penalty(self, model: nn.Module) -> torch.Tensor:
        """Compute EWC penalty: sum of F_i * (theta_i - theta*_i)^2."""
        loss = torch.tensor(0.0, device=self.device)
        for n, p in model.named_parameters():
            if p.requires_grad and n in self.fisher:
                loss += (self.fisher[n] * (p - self.params[n]).pow(2)).sum()
        return loss


class ReplayBuffer:
    """Stores a random subset of old data for rehearsal during new task training."""

    def __init__(self, dataset, buffer_size: int = 200, seed: int = 42):
        """
        Args:
            dataset: PyTorch Dataset from old task
            buffer_size: Number of samples to keep
        """
        np.random.seed(seed)
        total = len(dataset)
        n_keep = min(buffer_size, total)
        indices = np.random.choice(total, n_keep, replace=False)
        self.buffer = Subset(dataset, indices.tolist())
        self.size = n_keep

    def get_loader(self, batch_size: int = 16) -> DataLoader:
        return DataLoader(self.buffer, batch_size=batch_size, shuffle=True)


def train_with_ewc(
    model: nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device: torch.device,
    ewc: Optional[EWC] = None,
    ewc_lambda: float = 5000.0,
    replay_loader: Optional[DataLoader] = None,
    replay_weight: float = 0.5,
    num_epochs: int = 50,
    lr: float = 0.0005,
    weight_decay: float = 0.001,
    patience: int = 15,
    label_smoothing: float = 0.05,
) -> Dict[str, list]:
    """Training loop with optional EWC penalty and replay buffer.

    Returns:
        history dict with train_loss, val_loss, val_f1
    """
    from src.evaluate import evaluate

    # Class weight
    all_labels = []
    for batch in train_loader:
        all_labels.append(batch["label"])
    all_labels = torch.cat(all_labels).squeeze()
    num_pos = all_labels.sum().item()
    num_neg = len(all_labels) - num_pos
    pos_weight = torch.tensor([num_neg / (num_pos + 1e-8)]).to(device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=5)

    history = {"train_loss": [], "val_loss": [], "val_f1": []}
    best_f1 = 0.0
    best_state = None
    no_improve = 0

    for epoch in range(1, num_epochs + 1):
        model.train()
        total_loss = 0.0

        for batch in train_loader:
            audio = batch["audio"].to(device)
            visual = batch["visual"].to(device)
            text = batch["text"].to(device) if "text" in batch else None
            labels = batch["label"].to(device)

            # Data augmentation
            audio = audio + torch.randn_like(audio) * 0.05
            visual = visual + torch.randn_like(visual) * 0.05

            if label_smoothing > 0:
                labels = labels * (1 - label_smoothing) + 0.5 * label_smoothing

            optimizer.zero_grad()
            logits = model(audio, visual, text)
            loss = criterion(logits, labels)

            # EWC penalty
            if ewc is not None:
                loss += ewc_lambda * ewc.penalty(model)

            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item() * audio.size(0)

        # Replay old data
        if replay_loader is not None:
            model.train()
            for batch in replay_loader:
                audio = batch["audio"].to(device)
                visual = batch["visual"].to(device)
                text = batch["text"].to(device) if "text" in batch else None
                labels = batch["label"].to(device)

                optimizer.zero_grad()
                logits = model(audio, visual, text)
                loss = criterion(logits, labels) * replay_weight

                if ewc is not None:
                    loss += ewc_lambda * ewc.penalty(model) * replay_weight

                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()

        train_loss = total_loss / len(train_loader.dataset)

        # Validate
        val_metrics = evaluate(model, val_loader, criterion, device)
        val_loss = val_metrics["loss"]
        val_f1 = val_metrics["f1"]

        scheduler.step(val_loss)
        cur_lr = optimizer.param_groups[0]["lr"]

        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["val_f1"].append(val_f1)

        print(f"Epoch {epoch:3d}/{num_epochs} | Train: {train_loss:.4f} | Val: {val_loss:.4f} | "
              f"F1: {val_f1:.4f} | LR: {cur_lr:.6f}")

        if val_f1 > best_f1:
            best_f1 = val_f1
            best_state = copy.deepcopy(model.state_dict())
            no_improve = 0
            print(f"  -> Best model (F1: {best_f1:.4f})")
        else:
            no_improve += 1

        if no_improve >= patience:
            print(f"Early stopping at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)

    return history
