import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from sklearn.preprocessing import StandardScaler

from src.dvlog_data_loader import DVlogDataset


def create_windows(
    segments: Optional[np.ndarray],
    window_size: int,
    stride: int,
    pad_short: bool = True,
) -> List[np.ndarray]:
    if segments is None:
        return []

    num_segments, feat_dim = segments.shape

    if num_segments < window_size:
        if pad_short:
            padded = np.zeros((window_size, feat_dim))
            padded[:num_segments] = segments
            return [padded]
        return []

    windows = []
    start = 0
    while start + window_size <= num_segments:
        windows.append(segments[start : start + window_size])
        start += stride

    if start < num_segments and (num_segments - start) >= stride:
        last_window = np.zeros((window_size, feat_dim))
        remaining = segments[start:]
        last_window[: len(remaining)] = remaining
        windows.append(last_window)

    return windows


class DVlogFeatureExtractor:

    def __init__(self, config: dict):
        self.segment_duration = config["preprocessing"]["segment_duration"]
        self.norm_method = config["preprocessing"].get("norm_method", "zscore")
        self.audio_scaler = None
        self.visual_scaler = None

    def aggregate_to_segments(self, features: np.ndarray, fps: float = 1.0) -> np.ndarray:
        frames_per_segment = max(1, int(fps * self.segment_duration))
        num_frames = len(features)
        num_segments = max(1, num_frames // frames_per_segment)

        segments = []
        for i in range(num_segments):
            start = i * frames_per_segment
            end = min(start + frames_per_segment, num_frames)
            chunk = features[start:end]
            seg_mean = np.mean(chunk, axis=0)
            seg_std = np.std(chunk, axis=0)
            segments.append(np.concatenate([seg_mean, seg_std]))

        return np.array(segments)

    def process_participant_segments(
        self, raw: Dict[str, Optional[np.ndarray]], normalize: bool = True
    ) -> Dict[str, Optional[np.ndarray]]:
        audio = raw.get("acoustic")
        visual = raw.get("visual")

        if audio is not None:
            audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
            audio = self.aggregate_to_segments(audio, fps=1.0)
            if normalize and self.audio_scaler is not None:
                audio = self.audio_scaler.transform(audio)

        if visual is not None:
            visual = np.nan_to_num(visual, nan=0.0, posinf=0.0, neginf=0.0)
            visual = self.aggregate_to_segments(visual, fps=1.0)
            if normalize and self.visual_scaler is not None:
                visual = self.visual_scaler.transform(visual)

        return {"audio": audio, "visual": visual, "text": None}

    def fit_scalers(self, all_audio: List[np.ndarray], all_visual: List[np.ndarray]):
        if all_audio:
            self.audio_scaler = StandardScaler()
            stacked = np.vstack(all_audio)
            stacked = np.nan_to_num(stacked, nan=0.0, posinf=0.0, neginf=0.0)
            self.audio_scaler.fit(stacked)

        if all_visual:
            self.visual_scaler = StandardScaler()
            stacked = np.vstack(all_visual)
            stacked = np.nan_to_num(stacked, nan=0.0, posinf=0.0, neginf=0.0)
            self.visual_scaler.fit(stacked)


def build_dvlog_windowed_dataset(
    dataset: DVlogDataset,
    pids: List[int],
    labels_df: pd.DataFrame,
    extractor: DVlogFeatureExtractor,
    window_size: int,
    stride: int,
    normalize: bool = True,
    pad_short: bool = True,
) -> Tuple[Dict[str, List[np.ndarray]], np.ndarray, List[int]]:
    audio_list, visual_list, text_list = [], [], []
    labels = []
    window_pids = []

    for pid in pids:
        raw = dataset.load_participant(pid)
        processed = extractor.process_participant_segments(raw, normalize=normalize)
        label = int(labels_df[labels_df["Participant_ID"] == pid]["PHQ8_Binary"].values[0])

        audio_windows = create_windows(processed["audio"], window_size, stride, pad_short)
        visual_windows = create_windows(processed["visual"], window_size, stride, pad_short)

        n_windows = min(len(audio_windows), len(visual_windows)) if audio_windows and visual_windows else 0

        if not audio_windows and visual_windows:
            n_windows = len(visual_windows)
            adim = visual_windows[0].shape[1]
            audio_windows = [np.zeros((window_size, adim))] * n_windows
        elif audio_windows and not visual_windows:
            n_windows = len(audio_windows)
            vdim = audio_windows[0].shape[1]
            visual_windows = [np.zeros((window_size, vdim))] * n_windows

        for i in range(n_windows):
            audio_list.append(audio_windows[i])
            visual_list.append(visual_windows[i])
            text_list.append(np.zeros(128, dtype=np.int64))
            labels.append(label)
            window_pids.append(pid)

    return (
        {"audio": audio_list, "visual": visual_list, "text": text_list},
        np.array(labels) if labels else np.array([]),
        window_pids,
    )
