from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Bar, Position

TICK_VALUES = {"MES": 5.0, "MNQ": 2.0, "MGC": 10.0}
TRADERSPOST_TICKERS = {"MES": "MES1!", "MNQ": "MNQ1!", "MGC": "MGC1!"}
SUPPORTED = set(TICK_VALUES.keys())


@dataclass
class Decision:
    action: str  # HOLD, BUY, SELL, EXIT_LONG, EXIT_SHORT
    score: float
    reason: str
    stop_price: float | None = None
    target_price: float | None = None


class ModelRegistry:
    def __init__(self) -> None:
        self.cache: dict[str, object] = {}

    def get_model(self, symbol: str):
        if symbol in self.cache:
            return self.cache[symbol]
        path = settings.data_path / "models" / f"{symbol.lower()}_model.joblib"
        if path.exists():
            self.cache[symbol] = joblib.load(path)
            return self.cache[symbol]
        return None


model_registry = ModelRegistry()


def normalize_symbol(symbol: str) -> str:
    symbol = symbol.upper().replace("1!", "")
    aliases = {"MESH6": "MES", "MNQH6": "MNQ", "MGCM6": "MGC"}
    return aliases.get(symbol, symbol)


FEATURE_COLUMNS = [
    "ret_1",
    "ret_3",
    "ema_fast_gap",
    "ema_slow_gap",
    "atr_pct",
    "range_pct",
    "vol_ratio",
    "breakout_20",
    "breakdown_20",
    "mom_10",
]


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    df = pd.DataFrame(
        [{
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        } for b in bars]
    )
    if df.empty:
        return df
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_3"] = out["close"].pct_change(3)
    out["ema_fast"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=21, adjust=False).mean()
    out["ema_fast_gap"] = (out["close"] - out["ema_fast"]) / out["close"]
    out["ema_slow_gap"] = (out["close"] - out["ema_slow"]) / out["close"]

    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    out["atr_pct"] = atr / out["close"]
    out["range_pct"] = (out["high"] - out["low"]) / out["close"]
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(20).mean()

    out["hh20"] = out["high"].rolling(20).max()
    out["ll20"] = out["low"].rolling(20).min()
    out["breakout_20"] = (out["close"] >= out["hh20"].shift(1)).astype(float)
    out["breakdown_20"] = (out["close"] <= out["ll20"].shift(1)).astype(float)
    out["mom_10"] = out["close"] / out["close"].shift(10) - 1
    out[FEATURE_COLUMNS] = out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    return out


def get_open_position(db: Session, symbol: str) -> Position | None:
    return db.scalar(select(Position).where(Position.symbol == symbol, Position.status == "open").order_by(Position.entry_time.desc()))


def get_recent_bars(db: Session, symbol: str, timeframe: str, limit: int = 250) -> list[Bar]:
    stmt = select(Bar).where(Bar.symbol == symbol, Bar.timeframe == timeframe).order_by(Bar.timestamp.desc()).limit(limit)
    rows = list(db.scalars(stmt))
    return list(reversed(rows))


def prune_old_bars(db: Session, symbol: str, timeframe: str) -> None:
    stmt = select(Bar.id).where(Bar.symbol == symbol, Bar.timeframe == timeframe).order_by(Bar.timestamp.desc()).offset(settings.max_bars_per_symbol)
    ids = [row for row in db.scalars(stmt)]
    if ids:
        db.execute(delete(Bar).where(Bar.id.in_(ids)))


def score_symbol(df: pd.DataFrame, symbol: str) -> float:
    model = model_registry.get_model(symbol)
    x = df[FEATURE_COLUMNS].iloc[[-1]].fillna(0.0)
    if model is not None:
        if hasattr(model, "predict_proba"):
            return float(model.predict_proba(x)[0][1])
        pred = model.predict(x)
        return float(pred[0])

    # fallback if no trained model: transformed trend score
    last = x.iloc[0]
    raw = (
        1.8 * last["ema_fast_gap"]
        + 2.4 * last["ema_slow_gap"]
        + 0.8 * last["mom_10"]
        + 0.5 * last["breakout_20"]
        - 0.5 * last["breakdown_20"]
        + 0.25 * (last["vol_ratio"] - 1.0)
    )
    return float(1 / (1 + np.exp(-raw * 35)))


def decide(df: pd.DataFrame, symbol: str, current_position: Position | None) -> Decision:
    if len(df) < 40:
        return Decision(action="HOLD", score=0.5, reason="not_enough_bars")

    feat_df = compute_features(df)
    score = score_symbol(feat_df, symbol)
    last = feat_df.iloc[-1]
    close = float(last["close"])
    atr_value = max(float(last["atr_pct"] * close), close * 0.0015)
    stop_dist = atr_value * settings.risk_atr_multiplier
    target_dist = stop_dist * settings.take_profit_r_multiplier

    if current_position:
        if current_position.side == "long":
            if close <= current_position.stop_price:
                return Decision("EXIT_LONG", score, "stop_hit")
            if close >= current_position.target_price:
                return Decision("EXIT_LONG", score, "target_hit")
            if score <= 0.48:
                return Decision("EXIT_LONG", score, "model_reversal")
            return Decision("HOLD", score, "long_open")
        if current_position.side == "short":
            if close >= current_position.stop_price:
                return Decision("EXIT_SHORT", score, "stop_hit")
            if close <= current_position.target_price:
                return Decision("EXIT_SHORT", score, "target_hit")
            if score >= 0.52:
                return Decision("EXIT_SHORT", score, "model_reversal")
            return Decision("HOLD", score, "short_open")

    vol_ratio = float(last.get("vol_ratio", 0) or 0)
    atr_pct = float(last.get("atr_pct", 0) or 0)
    if vol_ratio < 0.8 or atr_pct < 0.0008:
        return Decision("HOLD", score, "market_too_quiet")

    if score >= settings.model_threshold_long:
        return Decision(
            action="BUY",
            score=score,
            reason="long_entry",
            stop_price=close - stop_dist,
            target_price=close + target_dist,
        )
    if score <= settings.model_threshold_short:
        return Decision(
            action="SELL",
            score=score,
            reason="short_entry",
            stop_price=close + stop_dist,
            target_price=close - target_dist,
        )
    return Decision("HOLD", score, "threshold_not_met")


def position_size_for(symbol: str) -> int:
    return {
        "MES": settings.position_size_mes,
        "MNQ": settings.position_size_mnq,
        "MGC": settings.position_size_mgc,
    }[symbol]


def pnl_dollars(symbol: str, side: str, entry: float, exit_: float, qty: int) -> float:
    points = (exit_ - entry) if side == "long" else (entry - exit_)
    return round(points * TICK_VALUES[symbol] * qty, 2)


def now_utc() -> datetime:
    return datetime.utcnow()
