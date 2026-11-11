"""
Utility functions for the depression detection project.
"""

import os
import random
import yaml
import numpy as np
import torch


def load_config(config_path: str = "configs/config.yaml") -> dict:
    """Load YAML configuration file."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def set_seed(seed: int = 42):
    """Set random seed for reproducibility across all libraries."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def get_device(device_config: str = "auto") -> torch.device:
    """Get torch device based on config string."""
    if device_config == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device_config)


def compute_class_weights(labels: np.ndarray) -> torch.Tensor:
    """Compute inverse-frequency class weights for imbalanced binary classification.

    Args:
        labels: Array of 0/1 labels

    Returns:
        Tensor of shape (2,) with weights for class 0 and class 1
    """
    counts = np.bincount(labels.astype(int), minlength=2)
    total = len(labels)
    weights = total / (2.0 * counts + 1e-8)
    return torch.FloatTensor(weights)
