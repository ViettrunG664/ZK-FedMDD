"""
PyTorch Dataset for DAIC-WOZ multimodal depression detection.
Wraps preprocessed features into tensors ready for DataLoader.
"""

import numpy as np
import torch
from torch.utils.data import Dataset
from typing import Dict, List, Optional


class DepressionDataset(Dataset):
    """PyTorch Dataset that holds preprocessed audio, visual, text features and labels."""

    def __init__(
        self,
        audio_features: List[Optional[np.ndarray]],
        visual_features: List[Optional[np.ndarray]],
        text_features: List[Optional[np.ndarray]],
        labels: np.ndarray,
        max_seq_len: int = 128,
        participant_ids: Optional[List[int]] = None,
    ):
        self.labels = labels
        self.max_seq_len = max_seq_len
        self.participant_ids = participant_ids

        # Convert to tensors, replacing None with zero tensors
        self.audio = self._to_tensor_list(audio_features, dtype="float")
        self.visual = self._to_tensor_list(visual_features, dtype="float")

        # Text: token IDs as LongTensors
        self.text = self._to_text_tensors(text_features)

        # Store actual sequence lengths (before padding) for masking
        self.audio_lengths = self._get_lengths(audio_features)
        self.visual_lengths = self._get_lengths(visual_features)

    def _to_tensor_list(self, features: List[Optional[np.ndarray]], dtype="float") -> List[torch.Tensor]:
        tensors = []
        for f in features:
            if f is not None:
                t = torch.FloatTensor(np.nan_to_num(f, nan=0.0, posinf=0.0, neginf=0.0))
            else:
                t = None
            tensors.append(t)

        # Find feature dim from first non-None sample
        feat_dim = 1
        for t in tensors:
            if t is not None:
                feat_dim = t.shape[-1]
                break

        # Replace None with zero tensors
        for i, t in enumerate(tensors):
            if t is None:
                tensors[i] = torch.zeros(self.max_seq_len, feat_dim)

        return tensors

    def _to_text_tensors(self, text_features: List[Optional[np.ndarray]]) -> List[torch.Tensor]:
        """Convert text token ID arrays to LongTensors."""
        tensors = []
        max_len = 128  # default

        # Find max_len from first valid sample
        for f in text_features:
            if f is not None and isinstance(f, np.ndarray) and len(f) > 0:
                max_len = len(f)
                break

        for f in text_features:
            if f is not None and isinstance(f, np.ndarray) and len(f) > 0:
                tensors.append(torch.LongTensor(f))
            else:
                tensors.append(torch.zeros(max_len, dtype=torch.long))

        return tensors

    def _get_lengths(self, features: List[Optional[np.ndarray]]) -> List[int]:
        lengths = []
        for f in features:
            if f is not None and isinstance(f, np.ndarray) and f.ndim == 2:
                nonzero = np.any(f != 0, axis=1)
                lengths.append(int(nonzero.sum()))
            else:
                lengths.append(0)
        return lengths

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        item = {
            "audio": self.audio[idx],
            "visual": self.visual[idx],
            "text": self.text[idx],
            "audio_len": self.audio_lengths[idx],
            "visual_len": self.visual_lengths[idx],
            "label": torch.FloatTensor([self.labels[idx]]),
        }
        if self.participant_ids is not None:
            item["participant_id"] = self.participant_ids[idx]
        return item
