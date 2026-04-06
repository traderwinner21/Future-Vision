from __future__ import annotations

import argparse
from pathlib import Path
import joblib
import pandas as pd
from xgboost import XGBClassifier

from .config import settings
from .strategy import FEATURE_COLUMNS, compute_features


def build_labels(df: pd.DataFrame, horizon: int = 6, threshold: float = 0.0015) -> pd.Series:
    future_ret = df["close"].shift(-horizon) / df["close"] - 1
    return (future_ret > threshold).astype(int)


def train(csv_path: str, symbol: str) -> Path:
    df = pd.read_csv(csv_path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    feat = compute_features(df)
    feat["target"] = build_labels(feat)
    feat = feat.dropna(subset=FEATURE_COLUMNS + ["target"]).copy()

    if len(feat) < 250:
        raise ValueError(f"Not enough rows for {symbol}. Need at least 250 after feature engineering.")

    x = feat[FEATURE_COLUMNS]
    y = feat["target"]

    model = XGBClassifier(
        n_estimators=250,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(x, y)

    model_path = settings.data_path / "models" / f"{symbol.lower()}_model.joblib"
    joblib.dump(model, model_path)
    return model_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--symbol", required=True, choices=["MES", "MNQ", "MGC"])
    args = parser.parse_args()
    path = train(args.csv, args.symbol)
    print(f"Saved model to {path}")


if __name__ == "__main__":
    main()
