# fl_service/model.py
# Small CNN model for MDD detection (matching PDF architecture: TFLite_Small_CNN)
# Input: (batch, 64, 4) - sliding window of 64 timesteps x 4 sensor features
# Output: (batch, 1) - sigmoid probability of MDD

import torch
import torch.nn as nn


WINDOW_SIZE = 64
NUM_FEATURES = 4  # accel_x, temperature, pulse_rate, spo2


class SmallCNN(nn.Module):
    """
    PyTorch equivalent of TFLite_Small_CNN from PDF:
      Conv1D(32) -> Conv1D(32) -> MaxPool -> Conv1D(64) -> MaxPool
      -> GlobalAvgPool -> Dense(32) -> Dense(1, sigmoid)
    Total params: ~11,841
    """

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            # Block 1: 2x Conv1D(32)
            nn.Conv1d(NUM_FEATURES, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(32, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool1d(2),  # 64 -> 32

            # Block 2: Conv1D(64)
            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool1d(2),  # 32 -> 16

            # Global Average Pooling
            nn.AdaptiveAvgPool1d(1),  # 16 -> 1
        )
        self.classifier = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        # x shape: (batch, window_size=64, features=4)
        # Conv1d expects (batch, channels, length) so transpose
        x = x.transpose(1, 2)  # -> (batch, 4, 64)
        x = self.features(x)   # -> (batch, 64, 1)
        x = x.squeeze(-1)      # -> (batch, 64)
        x = self.classifier(x) # -> (batch, 1)
        return x


def create_model():
    """Create a new SmallCNN model."""
    return SmallCNN()


def count_parameters(model):
    """Count total trainable parameters."""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def get_model_weights(model):
    """Extract model weights as a dict of numpy arrays."""
    return {k: v.cpu().numpy().copy() for k, v in model.state_dict().items()}


def set_model_weights(model, weights_dict):
    """Load weights from a dict of numpy arrays into model."""
    state = {k: torch.tensor(v) for k, v in weights_dict.items()}
    model.load_state_dict(state)
