"""Download free 1-minute history from Dukascopy's public datafeed.

Each UTC day is one LZMA-compressed file of 24-byte records:
seconds-from-midnight, open, close, low, high (ints scaled by a per-symbol
divisor) and volume (float), all big-endian.
"""
from __future__ import annotations

import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

URL = "https://datafeed.dukascopy.com/datafeed/{sym}/{y:04d}/{m:02d}/{d:02d}/{side}_candles_min_1.bi5"
RECORD = struct.Struct(">5if")


def divisor(symbol: str) -> int:
    s = symbol.upper()
    if s.startswith(("XAU", "XAG")) or s.endswith("JPY"):
        return 1000
    return 100000


def decode_day(raw: bytes, day: date, div: int) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    data = lzma.decompress(raw)
    rows = [RECORD.unpack_from(data, i) for i in range(0, len(data) - len(data) % RECORD.size, RECORD.size)]
    df = pd.DataFrame(rows, columns=["sec", "open", "close", "low", "high", "volume"])
    start = pd.Timestamp(day, tz="UTC")
    df.index = start + pd.to_timedelta(df.pop("sec"), unit="s")
    for c in ("open", "close", "low", "high"):
        df[c] = df[c] / div
    # Dukascopy writes flat zero-volume candles when the market is closed.
    df = df[df["volume"] > 0]
    bad = (df["low"] > df[["open", "close"]].min(axis=1)) | (df["high"] < df[["open", "close"]].max(axis=1))
    if bad.mean() > 0.01:
        raise ValueError(f"{day}: candles do not decode as OHLC; check the symbol divisor")
    return df[["open", "high", "low", "close", "volume"]]


def fetch_day(symbol: str, day: date, side: str = "BID", retries: int = 4) -> bytes:
    # The month in the URL is zero-based.
    url = URL.format(sym=symbol.upper(), y=day.year, m=day.month - 1, d=day.day, side=side)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            err = e
        except OSError as e:
            err = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"download failed for {url}: {err}")


def download(symbol: str, start: date, end: date, cache_dir: str | None = None,
             workers: int = 8) -> pd.DataFrame:
    """1-minute bid bars in UTC for [start, end). Days are cached as .bi5 files."""
    days = [start + timedelta(days=i) for i in range((end - start).days)]
    days = [d for d in days if d.weekday() != 5]  # no trading on Saturday
    cache = Path(cache_dir) if cache_dir else None
    div = divisor(symbol)

    def one(day: date) -> pd.DataFrame:
        path = cache / symbol.upper() / f"{day}.bi5" if cache else None
        if path and path.exists():
            raw = path.read_bytes()
        else:
            raw = fetch_day(symbol, day)
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        return decode_day(raw, day, div)

    frames = []
    with ThreadPoolExecutor(workers) as pool:
        for i, df in enumerate(pool.map(one, days), 1):
            frames.append(df)
            if i % 20 == 0 or i == len(days):
                print(f"\r{symbol}: {i}/{len(days)} days", end="", file=sys.stderr)
    print(file=sys.stderr)
    frames = [f for f in frames if len(f)]
    if not frames:
        raise RuntimeError(f"no data for {symbol} between {start} and {end}")
    return pd.concat(frames).sort_index()
