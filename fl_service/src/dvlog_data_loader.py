import os
import numpy as np
import pandas as pd
from typing import Dict, List, Optional


class DVlogDataset:

    def __init__(self, dataset_dir: str):
        self.dataset_dir = dataset_dir
        self.labels_csv = os.path.join(dataset_dir, "labels.csv")
        self._labels_df = None

    @property
    def labels_df(self) -> pd.DataFrame:
        if self._labels_df is None:
            df = pd.read_csv(self.labels_csv)
            df = df.rename(columns={"index": "Participant_ID"})
            df["PHQ8_Binary"] = (df["label"] == "depression").astype(int)
            self._labels_df = df
        return self._labels_df

    def load_labels(self, fold: Optional[str] = None) -> pd.DataFrame:
        df = self.labels_df.copy()
        if fold:
            df = df[df["fold"] == fold].reset_index(drop=True)
        return df[["Participant_ID", "PHQ8_Binary", "gender", "fold"]]

    def get_participant_ids(self, fold: str) -> List[int]:
        df = self.labels_df[self.labels_df["fold"] == fold]
        return df["Participant_ID"].tolist()

    def load_acoustic(self, pid: int) -> Optional[np.ndarray]:
        path = os.path.join(self.dataset_dir, str(pid), f"{pid}_acoustic.npy")
        if not os.path.exists(path):
            return None
        return np.load(path)

    def load_visual(self, pid: int) -> Optional[np.ndarray]:
        path = os.path.join(self.dataset_dir, str(pid), f"{pid}_visual.npy")
        if not os.path.exists(path):
            return None
        return np.load(path)

    def load_participant(self, pid: int) -> Dict[str, Optional[np.ndarray]]:
        return {
            "acoustic": self.load_acoustic(pid),
            "visual": self.load_visual(pid),
        }
