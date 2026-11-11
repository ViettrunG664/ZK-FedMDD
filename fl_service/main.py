# fl_service/main.py
# FastAPI service for Federated Learning on D-vlog (BiLSTM + Attention)
# Port 7001
#
# Usage:
#   python -m uvicorn fl_service.main:app --host localhost --port 7001 --reload
#
# First /model/init or /train call triggers heavy dataset loading (~2-5 min).
# Subsequent calls are fast (dataset cached in memory + pickle on disk).

import os
import sys
import json
import base64
import hashlib
import io
import time
import copy
import pickle
import logging
from pathlib import Path
from typing import Optional, Dict, List

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from collections import OrderedDict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Paths ───
FL_SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FL_SERVICE_DIR))

from src.utils import load_config, set_seed, get_device
from src.dvlog_data_loader import DVlogDataset
from src.dvlog_feature_extraction import DVlogFeatureExtractor, build_dvlog_windowed_dataset
from src.dataset import DepressionDataset
from src.model import MultimodalBiLSTM

# ─── Logging ───
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("fl_service")

# ─── FastAPI ───
app = FastAPI(title="FL-Service", description="Federated Learning service for MDD detection (D-vlog + BiLSTM)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://localhost:3002"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Global state (lazy-initialized) ───
CONFIG_PATH = FL_SERVICE_DIR / "configs" / "dvlog_config.yaml"
WEIGHTS_DIR = FL_SERVICE_DIR / "weights"
CACHE_DIR = FL_SERVICE_DIR / "cache"
WEIGHTS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

HOSPITAL_IDS = ["hospital_a", "hospital_b", "hospital_c"]
NUM_HOSPITALS = len(HOSPITAL_IDS)

weights_store: Dict[str, Dict[str, np.ndarray]] = {}   # key -> numpy state dict

# Heavy objects built on first /model/init or /train call
_FL: Dict = {
    "ready": False,
    "device": None,
    "config": None,
    "hospital_loaders": {},   # hospital_id -> DataLoader
    "hospital_sizes": {},     # hospital_id -> num participants (for FedAvg weighting)
    "val_loader": None,
    "test_loader": None,
    "audio_dim": None,
    "visual_dim": None,
}


# ─── Request Models ───
class TrainRequest(BaseModel):
    hospital_id: str
    global_weights_key: Optional[str] = None
    epochs: int = 3
    learning_rate: float = 0.0005


class AggregateRequest(BaseModel):
    weight_keys: List[str]
    data_sizes: List[int]
    test_hospital: Optional[str] = None  # ignored in D-vlog mode; test_loader is shared


# ─── Helpers: weight serialization ───
def state_dict_to_numpy(state_dict) -> Dict[str, np.ndarray]:
    return {k: v.detach().cpu().numpy() for k, v in state_dict.items()}


def numpy_to_state_dict(np_dict: Dict[str, np.ndarray]) -> OrderedDict:
    return OrderedDict((k, torch.from_numpy(np.asarray(v))) for k, v in np_dict.items())


def save_weights(numpy_dict: Dict[str, np.ndarray], prefix: str = "weights"):
    buffer = io.BytesIO()
    np.savez(buffer, **numpy_dict)
    raw = buffer.getvalue()

    sha = hashlib.sha256(raw).hexdigest()
    key = f"{prefix}_{sha[:16]}_{int(time.time())}"

    weights_store[key] = numpy_dict

    # Persist to disk too
    np.savez(str(WEIGHTS_DIR / f"{key}.npz"), **numpy_dict)

    return key, sha


def load_weights(key: str) -> Optional[Dict[str, np.ndarray]]:
    if key in weights_store:
        return weights_store[key]

    disk_path = WEIGHTS_DIR / f"{key}.npz"
    if disk_path.exists():
        data = np.load(str(disk_path))
        weights = {k: data[k] for k in data.files}
        weights_store[key] = weights
        return weights

    return None


# ─── Model factory ───
def build_model() -> MultimodalBiLSTM:
    mc = _FL["config"]["model"]
    return MultimodalBiLSTM(
        audio_input_dim=_FL["audio_dim"],
        visual_input_dim=_FL["visual_dim"],
        hidden_dim=mc["hidden_dim"],
        num_layers=mc["num_layers"],
        dropout=mc["dropout"],
        fusion=mc["fusion"],
    ).to(_FL["device"])


# ─── Split 961 participants into 3 hospitals ───
def split_pids_into_hospitals(pids: List[int], seed: int = 42) -> Dict[str, List[int]]:
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(pids)
    chunks = np.array_split(shuffled, NUM_HOSPITALS)
    return {hid: c.tolist() for hid, c in zip(HOSPITAL_IDS, chunks)}


# ─── Lazy initialization ───
def _try_load_cache():
    """Try to load cached datasets + scaler + dims from disk."""
    meta_path = CACHE_DIR / "fl_meta.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Cache meta unreadable: {e}")
        return None


def ensure_initialized():
    """Build dvlog + hospital loaders. Called lazily."""
    if _FL["ready"]:
        return

    t0 = time.time()
    logger.info("🔄 Initializing FL state (first call — may take 2-5 min)...")

    config = load_config(str(CONFIG_PATH))
    set_seed(config["seed"])
    device = get_device(config["device"])
    logger.info(f"  device = {device}")

    dataset_dir = FL_SERVICE_DIR / config["data"]["dataset_dir"]
    dataset = DVlogDataset(str(dataset_dir))
    extractor = DVlogFeatureExtractor(config)

    train_pids = dataset.get_participant_ids("train")
    valid_pids = dataset.get_participant_ids("valid")
    test_pids = dataset.get_participant_ids("test")
    train_labels = dataset.load_labels("train")
    valid_labels = dataset.load_labels("valid")
    test_labels = dataset.load_labels("test")
    logger.info(f"  dvlog: {len(train_pids)} train, {len(valid_pids)} valid, {len(test_pids)} test")

    # Fit scalers on ALL train data
    logger.info("  fitting scalers on train set...")
    raw_segs = []
    for pid in train_pids:
        raw = dataset.load_participant(pid)
        processed = extractor.process_participant_segments(raw, normalize=False)
        raw_segs.append(processed)
    extractor.fit_scalers(
        [s["audio"] for s in raw_segs if s["audio"] is not None],
        [s["visual"] for s in raw_segs if s["visual"] is not None],
    )

    wc = config["preprocessing"]["windowing"]
    ws, stride = wc["window_size"], wc["stride"]

    # Shared val / test loaders
    logger.info("  building val/test loaders...")
    val_feat, val_y, val_wp = build_dvlog_windowed_dataset(
        dataset, valid_pids, valid_labels, extractor, ws, stride)
    test_feat, test_y, test_wp = build_dvlog_windowed_dataset(
        dataset, test_pids, test_labels, extractor, ws, stride)

    val_ds = DepressionDataset(val_feat["audio"], val_feat["visual"], val_feat["text"], val_y, ws, val_wp)
    test_ds = DepressionDataset(test_feat["audio"], test_feat["visual"], test_feat["text"], test_y, ws, test_wp)
    val_loader = DataLoader(val_ds, batch_size=16, shuffle=False)
    test_loader = DataLoader(test_ds, batch_size=16, shuffle=False)

    # Split train PIDs into NUM_HOSPITALS groups
    hospital_pids = split_pids_into_hospitals(train_pids)
    hospital_loaders = {}
    hospital_sizes = {}
    for hid, pids in hospital_pids.items():
        h_labels = train_labels[train_labels["Participant_ID"].isin(pids)]
        feat, y, wp = build_dvlog_windowed_dataset(
            dataset, pids, h_labels, extractor, ws, stride)
        ds = DepressionDataset(feat["audio"], feat["visual"], feat["text"], y, ws, wp)
        loader = DataLoader(ds, batch_size=16, shuffle=True)
        hospital_loaders[hid] = loader
        hospital_sizes[hid] = len(pids)
        logger.info(f"  {hid}: {len(pids)} participants, {len(ds)} windows")

    # Detect dims from val_ds sample
    sample = val_ds[0]
    audio_dim = sample["audio"].shape[-1]
    visual_dim = sample["visual"].shape[-1]

    _FL["config"] = config
    _FL["device"] = device
    _FL["hospital_loaders"] = hospital_loaders
    _FL["hospital_sizes"] = hospital_sizes
    _FL["val_loader"] = val_loader
    _FL["test_loader"] = test_loader
    _FL["audio_dim"] = audio_dim
    _FL["visual_dim"] = visual_dim
    _FL["ready"] = True

    elapsed = time.time() - t0
    logger.info(f"✅ FL state ready in {elapsed:.1f}s (audio_dim={audio_dim}, visual_dim={visual_dim})")


# ─── Evaluation ───
@torch.no_grad()
def evaluate_accuracy(model: nn.Module, loader: DataLoader) -> float:
    """Simple threshold-0.5 accuracy over val loader."""
    model.eval()
    device = _FL["device"]
    correct = 0
    total = 0
    for batch in loader:
        audio = batch["audio"].to(device)
        visual = batch["visual"].to(device)
        labels = batch["label"].to(device).squeeze()
        logits = model(audio, visual).squeeze()
        preds = (torch.sigmoid(logits) >= 0.5).float()
        correct += (preds == labels).sum().item()
        total += labels.numel()
    return correct / max(total, 1)


# ─── Local training ───
def train_one_hospital(hospital_id: str, global_weights: Optional[Dict[str, np.ndarray]],
                       epochs: int, lr: float) -> Dict:
    """Train the MultimodalBiLSTM locally on one hospital's DataLoader."""
    loader = _FL["hospital_loaders"][hospital_id]
    device = _FL["device"]

    model = build_model()

    if global_weights is not None:
        sd = numpy_to_state_dict(global_weights)
        model.load_state_dict(sd)

    # Loss + pos_weight
    all_labels = torch.cat([b["label"] for b in loader]).squeeze()
    num_pos = all_labels.sum().item()
    num_neg = len(all_labels) - num_pos
    pos_weight = torch.tensor([num_neg / (num_pos + 1e-8)]).to(device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=0.001)

    model.train()
    avg_loss = 0.0
    for ep in range(epochs):
        ep_loss = 0.0
        ep_count = 0
        for batch in loader:
            audio = batch["audio"].to(device) + torch.randn_like(batch["audio"]).to(device) * 0.05
            visual = batch["visual"].to(device) + torch.randn_like(batch["visual"]).to(device) * 0.05
            labels = batch["label"].to(device)
            labels = labels * 0.95 + 0.025  # label smoothing

            optimizer.zero_grad()
            logits = model(audio, visual)
            loss = criterion(logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            ep_loss += loss.item() * audio.size(0)
            ep_count += audio.size(0)
        avg_loss = ep_loss / max(ep_count, 1)

    # Evaluate on shared val set
    acc = evaluate_accuracy(model, _FL["val_loader"])

    # data_size: number of training WINDOWS (used for FedAvg weighting)
    data_size = len(loader.dataset)

    # Return numpy weights
    np_weights = state_dict_to_numpy(model.state_dict())
    return {
        "weights": np_weights,
        "accuracy": float(acc),
        "loss": float(avg_loss),
        "data_size": int(data_size),
    }


# ─── FedAvg ───
def federated_average(updates: List[Dict]) -> Dict[str, np.ndarray]:
    """Weighted average of client weights."""
    total = sum(u["data_size"] for u in updates)
    if total == 0:
        raise ValueError("Total data_size is zero")

    keys = updates[0]["weights"].keys()
    avg: Dict[str, np.ndarray] = {}
    for k in keys:
        acc = None
        for u in updates:
            w = u["data_size"] / total
            arr = u["weights"][k].astype(np.float32) * w
            acc = arr if acc is None else acc + arr
        avg[k] = acc
    return avg


# ─── Endpoints ───
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "FL-Service",
        "ready": _FL["ready"],
        "hospitals": HOSPITAL_IDS if _FL["ready"] else [],
    }


@app.get("/model/info")
def model_info():
    ensure_initialized()
    model = build_model()
    params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    mc = _FL["config"]["model"]
    return {
        "name": "MultimodalBiLSTM (D-vlog MDD Detection)",
        "parameters": params,
        "audio_dim": _FL["audio_dim"],
        "visual_dim": _FL["visual_dim"],
        "hidden_dim": mc["hidden_dim"],
        "num_layers": mc["num_layers"],
        "fusion": mc["fusion"],
        "output": "sigmoid (0=Normal, 1=MDD)",
        "modalities": ["audio", "visual"],
    }


@app.get("/model/init")
def init_model():
    """Create fresh global model and return its initial weights key."""
    ensure_initialized()
    model = build_model()
    np_weights = state_dict_to_numpy(model.state_dict())
    key, sha = save_weights(np_weights, prefix="init")
    params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    logger.info(f"Initialized global model, key={key}")
    return {"weights_key": key, "sha256": sha, "parameters": params}


@app.post("/train")
def train(req: TrainRequest):
    """Train BiLSTM locally on one hospital's share of D-vlog."""
    ensure_initialized()

    if req.hospital_id not in HOSPITAL_IDS:
        raise HTTPException(404, f"Unknown hospital_id '{req.hospital_id}'. Available: {HOSPITAL_IDS}")

    global_weights = None
    if req.global_weights_key:
        global_weights = load_weights(req.global_weights_key)
        if global_weights is None:
            raise HTTPException(404, f"Weights not found: {req.global_weights_key}")

    start = time.time()
    logger.info(f"▶ Training on {req.hospital_id}, epochs={req.epochs}...")
    result = train_one_hospital(req.hospital_id, global_weights, req.epochs, req.learning_rate)
    elapsed = time.time() - start

    key, sha = save_weights(result["weights"], prefix=f"local_{req.hospital_id}")
    logger.info(f"✓ Done {req.hospital_id}: acc={result['accuracy']:.4f} loss={result['loss']:.4f} "
                f"samples={result['data_size']} ({elapsed:.1f}s)")

    return {
        "weights_key": key,
        "weights_sha256": sha,
        "accuracy": round(result["accuracy"], 4),
        "data_size": result["data_size"],
        "loss": round(result["loss"], 4),
        "elapsed_ms": round(elapsed * 1000),
    }


@app.post("/aggregate")
def aggregate(req: AggregateRequest):
    """FedAvg over a list of weight keys; optionally evaluate on test set."""
    ensure_initialized()

    if len(req.weight_keys) != len(req.data_sizes):
        raise HTTPException(400, "weight_keys and data_sizes length mismatch")
    if len(req.weight_keys) < 2:
        raise HTTPException(400, "Need at least 2 updates to aggregate")

    updates = []
    for key, size in zip(req.weight_keys, req.data_sizes):
        w = load_weights(key)
        if w is None:
            raise HTTPException(404, f"Weights not found: {key}")
        updates.append({"weights": w, "data_size": int(size)})

    start = time.time()
    logger.info(f"▶ Aggregating {len(updates)} updates...")

    avg_np = federated_average(updates)

    # Evaluate on shared test_loader
    test_acc = None
    try:
        model = build_model()
        model.load_state_dict(numpy_to_state_dict(avg_np))
        test_acc = evaluate_accuracy(model, _FL["test_loader"])
    except Exception as e:
        logger.warning(f"Eval failed: {e}")

    key, sha = save_weights(avg_np, prefix="global")
    elapsed = time.time() - start
    total = sum(u["data_size"] for u in updates)

    logger.info(f"✓ Aggregated: test_acc={test_acc} total_windows={total} ({elapsed:.1f}s)")

    resp = {
        "global_weights_key": key,
        "global_weights_sha256": sha,
        "participant_count": len(updates),
        "total_samples": total,
        "elapsed_ms": round(elapsed * 1000),
    }
    if test_acc is not None:
        resp["global_accuracy"] = round(test_acc, 4)
    return resp


@app.get("/weights/{key}/hash")
def get_weights_hash(key: str):
    w = load_weights(key)
    if w is None:
        raise HTTPException(404, f"Weights not found: {key}")
    buffer = io.BytesIO()
    np.savez(buffer, **w)
    sha = hashlib.sha256(buffer.getvalue()).hexdigest()
    return {"key": key, "sha256": sha}


@app.get("/weights/{key}/blob")
def get_weights_blob(key: str):
    """Return the raw npz-serialized weights for a key, base64-encoded.

    The Node bridge fetches this so it can AES-256-encrypt the weight
    bytes before pushing them to Filecoin (content-level confidentiality
    at rest). The integrity hash is over the plaintext npz bytes and
    matches the value committed on-chain.
    """
    w = load_weights(key)
    if w is None:
        raise HTTPException(404, f"Weights not found: {key}")
    buffer = io.BytesIO()
    np.savez(buffer, **w)
    raw = buffer.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    return {
        "key": key,
        "sha256": sha,
        "bytes": len(raw),
        "b64": base64.b64encode(raw).decode("ascii"),
    }


@app.get("/hospitals")
def list_hospitals():
    """List available hospital partitions (triggers init if not ready)."""
    ensure_initialized()
    return {
        "hospitals": [
            {"id": hid,
             "participants": _FL["hospital_sizes"][hid],
             "windows": len(_FL["hospital_loaders"][hid].dataset)}
            for hid in HOSPITAL_IDS
        ]
    }
