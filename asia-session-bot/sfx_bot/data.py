"""Loading and resampling OHLC data."""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLC = ["open", "high", "low", "close"]

_ALIASES = {
    "time": "time", "datetime": "time", "date": "time", "timestamp": "time",
    "gmt time": "time", "local time": "time",
    "open": "open", "o": "open",
    "high": "high", "h": "high",
    "low": "low", "l": "low",
    "close": "close", "c": "close",
    "volume": "volume", "vol": "volume", "tick_volume": "volume", "v": "volume",
}


def load_csv(path: str, data_tz: str = "UTC", date_format: str | None = None) -> pd.DataFrame:
    """Read 1-minute OHLC bars from a CSV file.

    The file needs a time column plus open/high/low/close (common names such as
    Datetime, Gmt time, tick_volume are recognised). ``data_tz`` is the zone the
    timestamps are written in when they carry no offset of their own.
    Separate <DATE> and <TIME> columns (MetaTrader export) are joined.
    """
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:
        df["time"] = df["date"].astype(str) + " " + df["time"].astype(str)
        df = df.drop(columns=["date"])
    df = df.rename(columns={c: _ALIASES[c] for c in df.columns if c in _ALIASES})
    missing = [c for c in ["time", *OHLC] if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}; found {list(df.columns)}")
    idx = pd.to_datetime(df["time"], format=date_format)
    df = df.drop(columns=["time"])
    df.index = idx
    return prepare(df, data_tz)


def prepare(df: pd.DataFrame, data_tz: str = "UTC") -> pd.DataFrame:
    """Sort, de-duplicate and give the index a time zone."""
    df = df.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize(data_tz)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cols = OHLC + (["volume"] if "volume" in df.columns else [])
    return df[cols].astype(float)


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate bars to ``minutes``. Bars are labelled by their open time."""
    if minutes == 1:
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    out = df.resample(f"{minutes}min", label="left", closed="left").agg(agg)
    return out.dropna(subset=["open"])


def synthetic_minutes(days: int = 60, start: str = "2024-01-01", seed: int = 7,
                      price: float = 2000.0, vol: float = 0.25) -> pd.DataFrame:
    """Random-walk 1-minute bars (UTC) for demos and smoke tests. Not market data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 1440, freq="1min", tz="UTC")
    # Volatility that clusters, so there are trends and ranges to trade.
    regime = np.repeat(rng.choice([0.6, 1.0, 1.8], size=len(idx) // 60 + 1), 60)[: len(idx)]
    steps = rng.standard_normal(len(idx)) * vol * regime
    close = price + np.cumsum(steps)
    open_ = np.concatenate([[price], close[:-1]])
    wick = np.abs(rng.standard_normal((2, len(idx)))) * vol * 0.5 * regime
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)
    # Drop the weekend like a real FX/gold feed.
    return df[df.index.dayofweek < 5]
