import torch
import torch.nn as nn


WINDOW_SIZE = 64
NUM_FEATURES = 4


class SmallCNN(nn.Module):

    def __init__(self):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(NUM_FEATURES, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(32, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool1d(2),

            nn.Conv1d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool1d(2),

            nn.AdaptiveAvgPool1d(1),
        )
        self.classifier = nn.Sequential(
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        x = x.transpose(1, 2)
        x = self.features(x)
        x = x.squeeze(-1)
        x = self.classifier(x)
        return x


def create_model():
    return SmallCNN()


def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def get_model_weights(model):
    return {k: v.cpu().numpy().copy() for k, v in model.state_dict().items()}


def set_model_weights(model, weights_dict):
    state = {k: torch.tensor(v) for k, v in weights_dict.items()}
    model.load_state_dict(state)
