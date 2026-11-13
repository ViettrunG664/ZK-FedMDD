import csv
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from model import SmallCNN, create_model, get_model_weights, set_model_weights, WINDOW_SIZE, NUM_FEATURES


def load_csv_data(csv_path):
    features = []
    labels = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            features.append([
                float(row["accel_x"]),
                float(row["temperature"]),
                float(row["pulse_rate"]),
                float(row["spo2"]),
            ])
            labels.append(int(float(row["label"])))
    return np.array(features, dtype=np.float32), np.array(labels, dtype=np.float32)


def normalize_features(features):
    mins = np.array([300, 30, 40, 70], dtype=np.float32)
    maxs = np.array([950, 45, 220, 100], dtype=np.float32)
    normalized = (features - mins) / (maxs - mins + 1e-8)
    return np.clip(normalized, 0, 1)


def create_sliding_windows(features, labels, window_size=WINDOW_SIZE):
    X, y = [], []
    for i in range(len(features) - window_size + 1):
        window = features[i:i + window_size]
        label = labels[i + window_size - 1]
        X.append(window)
        y.append(label)
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def train_local(csv_path, global_weights=None, epochs=5, batch_size=32, lr=0.001):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    features, labels = load_csv_data(csv_path)
    features = normalize_features(features)
    X, y = create_sliding_windows(features, labels)

    data_size = len(X)
    if data_size == 0:
        raise ValueError(f"No training data from {csv_path}")

    split = int(0.8 * data_size)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    train_ds = TensorDataset(torch.tensor(X_train), torch.tensor(y_train))
    val_ds = TensorDataset(torch.tensor(X_val), torch.tensor(y_val))
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=batch_size)

    model = create_model().to(device)
    if global_weights is not None:
        set_model_weights(model, global_weights)

    criterion = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            output = model(X_batch).squeeze(-1)
            loss = criterion(output, y_batch)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

    model.eval()
    correct = 0
    total = 0
    val_loss = 0
    with torch.no_grad():
        for X_batch, y_batch in val_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            output = model(X_batch).squeeze(-1)
            val_loss += criterion(output, y_batch).item()
            predicted = (output >= 0.5).float()
            correct += (predicted == y_batch).sum().item()
            total += y_batch.size(0)

    accuracy = correct / total if total > 0 else 0

    return {
        "weights": get_model_weights(model),
        "accuracy": accuracy,
        "data_size": data_size,
        "loss": val_loss / max(len(val_loader), 1),
    }
