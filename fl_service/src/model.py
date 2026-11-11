"""
Multimodal BiLSTM model for depression detection.

Architecture:
    Audio features  → BiLSTM → hidden_audio
    Visual features → BiLSTM → hidden_visual
    Text tokens     → Embedding → BiLSTM → hidden_text
                              ↓
                        Fusion Layer (concat / attention)
                              ↓
                        FC → Sigmoid → P(depressed)
"""

import torch
import torch.nn as nn
from typing import Optional


class SelfAttentionPool(nn.Module):
    """Attention pooling over all timesteps instead of just using last hidden state."""

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attn = nn.Linear(hidden_dim, 1)

    def forward(self, lstm_out: torch.Tensor) -> torch.Tensor:
        """
        Args:
            lstm_out: (batch, seq_len, hidden_dim*2)
        Returns:
            (batch, hidden_dim*2) — attention-weighted sum over timesteps
        """
        attn_weights = torch.softmax(self.attn(lstm_out), dim=1)  # (batch, seq_len, 1)
        return (lstm_out * attn_weights).sum(dim=1)                # (batch, hidden_dim*2)


class BiLSTMEncoder(nn.Module):
    """Single-modality BiLSTM encoder with attention pooling."""

    def __init__(self, input_dim: int, hidden_dim: int, num_layers: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.attention_pool = SelfAttentionPool(hidden_dim * 2)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (batch, seq_len, input_dim)
        Returns:
            output: (batch, hidden_dim * 2)
        """
        lstm_out, _ = self.lstm(x)                  # (batch, seq_len, hidden_dim*2)
        pooled = self.attention_pool(lstm_out)       # (batch, hidden_dim*2)
        return self.dropout(pooled)


class TextBiLSTMEncoder(nn.Module):
    """Text encoder: Embedding → BiLSTM → Attention Pooling."""

    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int,
        hidden_dim: int,
        num_layers: int,
        dropout: float,
        pad_idx: int = 0,
    ):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=pad_idx)
        self.lstm = nn.LSTM(
            input_size=embedding_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.attention_pool = SelfAttentionPool(hidden_dim * 2)
        self.dropout = nn.Dropout(dropout)

    def forward(self, token_ids: torch.Tensor) -> torch.Tensor:
        """
        Args:
            token_ids: (batch, max_words) LongTensor of token indices
        Returns:
            (batch, hidden_dim * 2)
        """
        embedded = self.embedding(token_ids)         # (batch, max_words, embedding_dim)
        lstm_out, _ = self.lstm(embedded)            # (batch, max_words, hidden_dim*2)
        pooled = self.attention_pool(lstm_out)       # (batch, hidden_dim*2)
        return self.dropout(pooled)


class MultimodalAttentionFusion(nn.Module):
    """Attention-based fusion of N modality representations."""

    def __init__(self, hidden_dim: int):
        super().__init__()
        self.attention = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, *modality_hiddens: torch.Tensor) -> torch.Tensor:
        """
        Args:
            modality_hiddens: N tensors of shape (batch, hidden_dim)
        Returns:
            fused: (batch, hidden_dim)
        """
        stacked = torch.stack(modality_hiddens, dim=1)  # (batch, N, hidden_dim)
        attn_weights = self.attention(stacked)           # (batch, N, 1)
        attn_weights = torch.softmax(attn_weights, dim=1)
        fused = (stacked * attn_weights).sum(dim=1)      # (batch, hidden_dim)
        return fused


class MultimodalBiLSTM(nn.Module):
    """
    Multimodal BiLSTM for depression detection.

    Supports 2 modalities (audio + visual) or 3 modalities (audio + visual + text).
    """

    def __init__(
        self,
        audio_input_dim: int,
        visual_input_dim: int,
        hidden_dim: int = 128,
        num_layers: int = 2,
        dropout: float = 0.3,
        fusion: str = "concatenate",
        # Text encoder params (set vocab_size > 0 to enable)
        vocab_size: int = 0,
        text_embedding_dim: int = 128,
    ):
        super().__init__()

        self.fusion_type = fusion
        self.use_text = vocab_size > 0

        # Per-modality BiLSTM encoders
        self.audio_encoder = BiLSTMEncoder(audio_input_dim, hidden_dim, num_layers, dropout)
        self.visual_encoder = BiLSTMEncoder(visual_input_dim, hidden_dim, num_layers, dropout)

        # Bidirectional → output dim is hidden_dim * 2
        encoder_out_dim = hidden_dim * 2

        # Text encoder (optional)
        if self.use_text:
            self.text_encoder = TextBiLSTMEncoder(
                vocab_size, text_embedding_dim, hidden_dim, num_layers, dropout,
            )
            num_modalities = 3
        else:
            num_modalities = 2

        # Fusion layer
        if fusion == "concatenate":
            classifier_input_dim = encoder_out_dim * num_modalities
        elif fusion == "attention":
            self.fusion_layer = MultimodalAttentionFusion(encoder_out_dim)
            classifier_input_dim = encoder_out_dim
        else:
            raise ValueError(f"Unknown fusion type: {fusion}")

        # Classification head
        self.classifier = nn.Sequential(
            nn.Linear(classifier_input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def forward(
        self,
        audio: torch.Tensor,
        visual: torch.Tensor,
        text: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        Args:
            audio:  (batch, seq_len, audio_dim)
            visual: (batch, seq_len, visual_dim)
            text:   (batch, max_words) LongTensor, optional

        Returns:
            logits: (batch, 1)
        """
        audio_h = self.audio_encoder(audio)
        visual_h = self.visual_encoder(visual)

        if self.use_text and text is not None:
            text_h = self.text_encoder(text)
            modalities = [audio_h, visual_h, text_h]
        else:
            modalities = [audio_h, visual_h]

        # Fusion
        if self.fusion_type == "concatenate":
            fused = torch.cat(modalities, dim=1)
        elif self.fusion_type == "attention":
            fused = self.fusion_layer(*modalities)

        logits = self.classifier(fused)
        return logits

    def freeze_encoders(self):
        """Freeze BiLSTM encoder weights (for transfer learning)."""
        for param in self.audio_encoder.parameters():
            param.requires_grad = False
        for param in self.visual_encoder.parameters():
            param.requires_grad = False
        if hasattr(self, "fusion_layer"):
            for param in self.fusion_layer.parameters():
                param.requires_grad = False

    def unfreeze_encoders(self):
        """Unfreeze BiLSTM encoder weights."""
        for param in self.audio_encoder.parameters():
            param.requires_grad = True
        for param in self.visual_encoder.parameters():
            param.requires_grad = True
        if hasattr(self, "fusion_layer"):
            for param in self.fusion_layer.parameters():
                param.requires_grad = True


class FeatureProjector(nn.Module):
    """Projects features from one dimension to another."""

    def __init__(self, input_dim: int, output_dim: int):
        super().__init__()
        self.proj = nn.Sequential(
            nn.Linear(input_dim, output_dim),
            nn.LayerNorm(output_dim),
            nn.ReLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """(batch, seq_len, input_dim) -> (batch, seq_len, output_dim)"""
        return self.proj(x)


class TransferMultimodalBiLSTM(nn.Module):
    """Transfer learning model: projects target features to pretrained encoder dimensions."""

    def __init__(
        self,
        pretrained_model: MultimodalBiLSTM,
        target_audio_dim: int,
        target_visual_dim: int,
        source_audio_dim: int,
        source_visual_dim: int,
        hidden_dim: int = 64,
        dropout: float = 0.4,
        vocab_size: int = 0,
        text_embedding_dim: int = 64,
    ):
        super().__init__()

        # Projection layers: target dims -> source dims (pretrained encoder expects)
        self.audio_proj = FeatureProjector(target_audio_dim, source_audio_dim)
        self.visual_proj = FeatureProjector(target_visual_dim, source_visual_dim)

        # Copy pretrained encoders and fusion
        self.audio_encoder = pretrained_model.audio_encoder
        self.visual_encoder = pretrained_model.visual_encoder
        self.fusion_type = pretrained_model.fusion_type
        if hasattr(pretrained_model, "fusion_layer"):
            self.fusion_layer = pretrained_model.fusion_layer

        encoder_out_dim = hidden_dim * 2

        # Optional text encoder (new, not pretrained)
        self.use_text = vocab_size > 0
        if self.use_text:
            self.text_encoder = TextBiLSTMEncoder(
                vocab_size, text_embedding_dim, hidden_dim, 1, dropout,
            )
            num_modalities = 3
        else:
            num_modalities = 2

        # New classifier head
        if self.fusion_type == "attention":
            self.fusion_layer = MultimodalAttentionFusion(encoder_out_dim)
            classifier_input_dim = encoder_out_dim
        else:
            classifier_input_dim = encoder_out_dim * num_modalities

        self.classifier = nn.Sequential(
            nn.Linear(classifier_input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1),
        )

    def freeze_encoders(self):
        for param in self.audio_encoder.parameters():
            param.requires_grad = False
        for param in self.visual_encoder.parameters():
            param.requires_grad = False

    def unfreeze_encoders(self):
        for param in self.audio_encoder.parameters():
            param.requires_grad = True
        for param in self.visual_encoder.parameters():
            param.requires_grad = True

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

        if self.fusion_type == "concatenate":
            fused = torch.cat(modalities, dim=1)
        elif self.fusion_type == "attention":
            fused = self.fusion_layer(*modalities)

        return self.classifier(fused)
