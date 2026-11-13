import copy
import torch
import torch.nn as nn
import numpy as np
from torch.utils.data import DataLoader
from collections import OrderedDict
from typing import Dict, List, Tuple

from src.model import MultimodalBiLSTM, BiLSTMEncoder, SelfAttentionPool, MultimodalAttentionFusion, FeatureProjector


class FederatedModel(nn.Module):

    def __init__(
        self,
        local_audio_dim: int,
        local_visual_dim: int,
        shared_dim: int = 64,
        hidden_dim: int = 64,
        num_layers: int = 1,
        dropout: float = 0.4,
        vocab_size: int = 0,
        text_embedding_dim: int = 64,
    ):
        super().__init__()

        self.audio_proj = FeatureProjector(local_audio_dim, shared_dim)
        self.visual_proj = FeatureProjector(local_visual_dim, shared_dim)

        self.audio_encoder = BiLSTMEncoder(shared_dim, hidden_dim, num_layers, dropout)
        self.visual_encoder = BiLSTMEncoder(shared_dim, hidden_dim, num_layers, dropout)

        encoder_out_dim = hidden_dim * 2

        self.use_text = vocab_size > 0
        if self.use_text:
            from src.model import TextBiLSTMEncoder
            self.text_encoder = TextBiLSTMEncoder(
                vocab_size, text_embedding_dim, hidden_dim, num_layers, dropout,
            )
            num_modalities = 3
        else:
            num_modalities = 2

        self.fusion_layer = MultimodalAttentionFusion(encoder_out_dim)
        classifier_input_dim = encoder_out_dim

        self.classifier = nn.Sequential(
            nn.Linear(classifier_input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, audio, visual, text=None):
        audio = self.audio_proj(audio)
        visual = self.visual_proj(visual)

        audio_h = self.audio_encoder(audio)
        visual_h = self.visual_encoder(visual)

        if self.use_text and text is not None:
            text_h = self.text_encoder(text)
            modalities = [audio_h, visual_h, text_h]
        else:
            modalities = [audio_h, visual_h]

        fused = self.fusion_layer(*modalities)
        return self.classifier(fused)

    def get_shared_state_dict(self) -> OrderedDict:
        shared = OrderedDict()
        for name, param in self.named_parameters():
            if not name.startswith(("audio_proj", "visual_proj")):
                shared[name] = param.data.clone()
        return shared

    def load_shared_state_dict(self, shared_state: OrderedDict):
        current = self.state_dict()
        for name, param in shared_state.items():
            if name in current:
                current[name] = param
        self.load_state_dict(current)


class FLClient:

    def __init__(
        self,
        name: str,
        model: FederatedModel,
        train_loader: DataLoader,
        val_loader: DataLoader,
        device: torch.device,
        lr: float = 0.001,
        weight_decay: float = 0.001,
        label_smoothing: float = 0.05,
    ):
        self.name = name
        self.model = model
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.device = device
        self.lr = lr
        self.weight_decay = weight_decay
        self.label_smoothing = label_smoothing
        self.num_samples = len(train_loader.dataset)

    def train_local(self, epochs: int = 3):
        self.model.train()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr, weight_decay=self.weight_decay)

        all_labels = []
        for batch in self.train_loader:
            all_labels.append(batch["label"])
        all_labels = torch.cat(all_labels).squeeze()
        num_pos = all_labels.sum().item()
        num_neg = len(all_labels) - num_pos
        pos_weight = torch.tensor([num_neg / (num_pos + 1e-8)]).to(self.device)
        criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        total_loss = 0.0
        for epoch in range(epochs):
            epoch_loss = 0.0
            for batch in self.train_loader:
                audio = batch["audio"].to(self.device)
                visual = batch["visual"].to(self.device)
                text = batch["text"].to(self.device) if "text" in batch else None
                labels = batch["label"].to(self.device)

                audio = audio + torch.randn_like(audio) * 0.05
                visual = visual + torch.randn_like(visual) * 0.05

                if self.label_smoothing > 0:
                    labels = labels * (1 - self.label_smoothing) + 0.5 * self.label_smoothing

                optimizer.zero_grad()
                logits = self.model(audio, visual, text)
                loss = criterion(logits, labels)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                optimizer.step()
                epoch_loss += loss.item() * audio.size(0)

            total_loss = epoch_loss / len(self.train_loader.dataset)

        return total_loss

    def get_shared_weights(self) -> OrderedDict:
        return self.model.get_shared_state_dict()

    def load_shared_weights(self, shared_state: OrderedDict):
        self.model.load_shared_state_dict(shared_state)


class FLServer:

    def __init__(self, clients: List[FLClient]):
        self.clients = clients

    def aggregate_fedavg(self) -> OrderedDict:
        total_samples = sum(c.num_samples for c in self.clients)

        client_states = []
        for client in self.clients:
            state = client.get_shared_weights()
            weight = client.num_samples / total_samples
            client_states.append((state, weight))

        avg_state = OrderedDict()
        for key in client_states[0][0].keys():
            avg_state[key] = sum(
                state[key].float() * w for state, w in client_states
            )

        return avg_state

    def run_round(self, local_epochs: int = 3) -> Dict[str, float]:
        global_state = self.clients[0].get_shared_weights()

        for client in self.clients:
            client.load_shared_weights(global_state)

        losses = {}
        for client in self.clients:
            loss = client.train_local(epochs=local_epochs)
            losses[client.name] = loss

        new_global = self.aggregate_fedavg()

        for client in self.clients:
            client.load_shared_weights(new_global)

        return losses
