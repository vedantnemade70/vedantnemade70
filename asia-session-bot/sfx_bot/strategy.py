"""SFX PO3 Asia Session strategy.

The rules, from the thread:

1. 15M chart, New York time (UTC-4). Mark 20:00.
2. Mark the nearest buyside liquidity (BSL, a swing high above price) and
   sellside liquidity (SSL, a swing low below price).
3. Wait for one of them to be purged (swept) between 20:00 and 22:00. If
   nothing is purged in that window the session is invalid.
   BSL purged -> look for sells. SSL purged -> look for buys.
4. On the 1M/3M chart wait for a market structure shift (MSS) and a fair
   value gap (FVG). Enter when price comes back into (mitigates) the FVG,
   SL at the formation of the FVG, target 1:2 RR.

``scan_session`` replays one session's bars in time order and reports how far
the setup has got. It only ever looks at bars that have closed, so the same
function drives both the backtester and the live bot.

Buy setups are handled by mirroring prices (a buy is a sell on the chart
turned upside down), so there is a single code path for both directions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data import resample

# Setup states, in the order a session moves through them.
NO_LEVELS = "no_levels"          # no usable 15M liquidity before 20:00
WAITING_PURGE = "waiting_purge"  # 20:00-22:00, nothing swept yet
INVALID = "invalid"              # no purge in the window (or both in one bar)
WAITING_MSS = "waiting_mss"      # purge seen, waiting for structure to shift
WAITING_FVG = "waiting_fvg"      # MSS seen, waiting for a fair value gap
PENDING = "pending"              # entry order ready, waiting for mitigation
FILLED = "filled"                # FVG mitigated, trade entered
CANCELLED = "cancelled"          # setup broke before entry
EXPIRED = "expired"              # entry cutoff passed without a fill


@dataclass
class Setup:
    session: date
    anchor: pd.Timestamp
    status: str = NO_LEVELS
    note: str = ""
    ref_price: float | None = None
    bsl: float | None = None
    ssl: float | None = None
    side: str | None = None           # "sell" after a BSL purge, "buy" after SSL
    purge_time: pd.Timestamp | None = None
    extreme: float | None = None      # highest high / lowest low after the purge
    mss_time: pd.Timestamp | None = None
    mss_level: float | None = None
    fvg_time: pd.Timestamp | None = None   # when the FVG was confirmed
    fvg_low: float | None = None
    fvg_high: float | None = None
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None
    runner_target: float | None = None
    fill_time: pd.Timestamp | None = None
    extra: dict = field(default_factory=dict)

    @property
    def direction(self) -> int:
        return {"buy": 1, "sell": -1}.get(self.side, 0)

    @property
    def risk(self) -> float | None:
        if self.entry is None or self.sl is None:
            return None
        return abs(self.entry - self.sl)


def session_times(session: date, cfg: StrategyConfig):
    """Anchor (20:00), end of purge window, entry cutoff and forced exit time."""
    def at(t: str, day: date) -> pd.Timestamp:
        hh, mm = (int(x) for x in t.split(":"))
        return pd.Timestamp(datetime.combine(day, time(hh, mm))).tz_localize(cfg.timezone)

    anchor = at(cfg.anchor_time, session)
    window_end = at(cfg.purge_window_end, session)
    if window_end <= anchor:
        window_end = at(cfg.purge_window_end, session + timedelta(days=1))
    cutoff = anchor + pd.Timedelta(hours=cfg.entry_cutoff_hours)
    close_by = anchor + pd.Timedelta(hours=cfg.max_trade_hours)
    return anchor, window_end, cutoff, close_by


# ---------------------------------------------------------------- structure

def swing_highs(high: np.ndarray, k: int) -> np.ndarray:
    """Indices of bars whose high beats the k bars on each side (confirmed only)."""
    n = len(high)
    out = []
    for i in range(k, n - k):
        if high[i] > high[i - k:i].max() and high[i] >= high[i + 1:i + k + 1].max():
            out.append(i)
    return np.array(out, dtype=int)


def swing_lows(low: np.ndarray, k: int) -> np.ndarray:
    return swing_highs(-low, k)


def nearest_liquidity(htf: pd.DataFrame, k: int):
    """Nearest untouched swing high above and swing low below the last close."""
    high, low = htf["high"].to_numpy(), htf["low"].to_numpy()
    ref = float(htf["close"].iloc[-1])
    bsl = ssl = None
    for i in swing_highs(high, k):
        untouched = i == len(high) - 1 or high[i] > high[i + 1:].max()
        if untouched and high[i] > ref and (bsl is None or high[i] < bsl):
            bsl = float(high[i])
    for i in swing_lows(low, k):
        untouched = i == len(low) - 1 or low[i] < low[i + 1:].min()
        if untouched and low[i] < ref and (ssl is None or low[i] > ssl):
            ssl = float(low[i])
    return ref, bsl, ssl


def _mirror(df: pd.DataFrame) -> pd.DataFrame:
    """Turn the chart upside down so buy setups can reuse the sell logic."""
    return pd.DataFrame({"open": -df["open"], "high": -df["low"],
                         "low": -df["high"], "close": -df["close"]}, index=df.index)


# ---------------------------------------------------------------- the scan

def scan_session(m1: pd.DataFrame, session: date, cfg: StrategyConfig,
                 now: pd.Timestamp | None = None) -> Setup:
    """Replay a session's 1-minute bars and return how far the setup got.

    ``m1`` holds 1-minute bars indexed by their open time (any time zone).
    ``now`` limits the scan to bars that closed by then; by default every bar
    in ``m1`` is used.
    """
    anchor, window_end, cutoff, _ = session_times(session, cfg)
    setup = Setup(session=session, anchor=anchor)
    m1 = m1.tz_convert(cfg.timezone)
    if now is not None:
        m1 = m1[m1.index + pd.Timedelta(minutes=1) <= now]
    now = (m1.index[-1] + pd.Timedelta(minutes=1)) if len(m1) else anchor

    # STEP 1 + 2: liquidity on the 15M chart before 20:00.
    # Counted in bars, not clock time, so Sunday sessions see Friday's chart.
    before = m1[(m1.index < anchor) & (m1.index >= anchor - pd.Timedelta(days=4))]
    htf = resample(before, cfg.htf_minutes).iloc[-cfg.htf_lookback_bars:]
    if len(htf) < 2 * cfg.htf_swing_strength + 2:
        setup.note = "not enough 15M history before 20:00"
        return setup
    setup.ref_price, setup.bsl, setup.ssl = nearest_liquidity(htf, cfg.htf_swing_strength)
    if setup.bsl is None and setup.ssl is None:
        setup.note = "no untouched 15M swing high/low near price"
        return setup

    # STEP 3: first purge between 20:00 and 22:00.
    window = m1[(m1.index >= anchor) & (m1.index < window_end)]
    hit_bsl = window["high"] > setup.bsl if setup.bsl is not None else window["high"] < -np.inf
    hit_ssl = window["low"] < setup.ssl if setup.ssl is not None else window["low"] > np.inf
    hits = window[hit_bsl | hit_ssl]
    if hits.empty:
        if now >= window_end:
            setup.status, setup.note = INVALID, "no purge between 20:00 and 22:00"
        else:
            setup.status = WAITING_PURGE
        return setup
    t = hits.index[0]
    if hit_bsl[t] and hit_ssl[t]:
        setup.status, setup.note = INVALID, "BSL and SSL purged in the same minute"
        return setup
    setup.side = "sell" if hit_bsl[t] else "buy"
    setup.purge_time = t

    # STEP 4 runs on the sell side; buys are mirrored in and out.
    view = m1 if setup.side == "sell" else _mirror(m1)
    opposite = setup.ssl if setup.side == "sell" else (-setup.bsl if setup.bsl is not None else None)
    _find_entry(view, setup, cfg, anchor, cutoff, now, opposite)

    if setup.side == "buy":
        for name in ("extreme", "mss_level", "fvg_low", "fvg_high", "entry", "sl", "tp", "runner_target"):
            v = getattr(setup, name)
            if v is not None:
                setattr(setup, name, -v)
        if setup.fvg_low is not None:
            setup.fvg_low, setup.fvg_high = setup.fvg_high, setup.fvg_low
    return setup


def _find_entry(m1: pd.DataFrame, setup: Setup, cfg: StrategyConfig,
                anchor: pd.Timestamp, cutoff: pd.Timestamp, now: pd.Timestamp,
                opposite_liquidity: float | None) -> None:
    """MSS -> FVG -> mitigation, for a sell after a buyside purge.

    Works on prices already mirrored for buys. Mutates ``setup``.
    """
    step = pd.Timedelta(minutes=cfg.ltf_minutes)
    k = cfg.ltf_swing_strength
    # LTF bars from a couple of hours before the anchor (for the swing low the
    # MSS breaks), keeping only bars that have fully closed.
    src = m1[m1.index >= anchor - pd.Timedelta(hours=3)]
    ltf = resample(src, cfg.ltf_minutes)
    ltf = ltf[ltf.index + step <= now]
    high, low, close = (ltf[c].to_numpy() for c in ("high", "low", "close"))
    times = ltf.index

    setup.status = WAITING_MSS
    start = int(np.searchsorted(times, setup.purge_time.floor(f"{cfg.ltf_minutes}min"), side="left"))
    if start >= len(ltf):
        setup.extreme = float(m1.loc[setup.purge_time, "high"])
        return

    lows_idx = swing_lows(low, k)
    ext_idx = start
    mss_idx = None
    for i in range(start, len(ltf)):
        if times[i] >= cutoff:
            setup.status, setup.note = EXPIRED, "no MSS before the entry cutoff"
            return
        if high[i] >= high[ext_idx]:
            ext_idx = i
        # Latest swing low before the high, confirmed without using bar i.
        prior = lows_idx[(lows_idx < ext_idx) & (lows_idx + k < i)]
        if len(prior) and close[i] < low[prior[-1]]:
            mss_idx = i
            setup.mss_level = float(low[prior[-1]])
            setup.mss_time = times[i]
            break
    setup.extreme = float(high[ext_idx])
    if mss_idx is None:
        return

    # FVG created after the MSS: the MSS candle or a later one is the middle
    # candle, and candle 1's low sits above candle 3's high.
    setup.status = WAITING_FVG
    fvg = None
    for i in range(mss_idx + 1, len(ltf)):
        if times[i] >= cutoff:
            setup.status, setup.note = EXPIRED, "no FVG before the entry cutoff"
            return
        if high[i] > setup.extreme:
            setup.status, setup.note = CANCELLED, "new high above the purge before an FVG formed"
            return
        gap = low[i - 2] - high[i]
        if gap > 0 and gap >= cfg.min_fvg_size:
            fvg = i
            break
    if fvg is None:
        return

    setup.fvg_time = times[fvg] + step
    setup.fvg_low, setup.fvg_high = float(high[fvg]), float(low[fvg - 2])
    setup.entry = setup.fvg_low if cfg.entry_mode == "edge" else (setup.fvg_low + setup.fvg_high) / 2
    base = high[fvg - 2:fvg + 1].max() if cfg.sl_mode == "fvg" else setup.extreme
    setup.sl = float(base) + cfg.sl_buffer
    risk = setup.sl - setup.entry
    if risk <= 0:
        setup.status, setup.note = CANCELLED, "stop is not beyond the entry"
        return
    setup.tp = setup.entry - cfg.rr * risk
    setup.runner_target = setup.tp
    if cfg.management == "be_runner" and opposite_liquidity is not None and opposite_liquidity < setup.tp:
        setup.runner_target = float(opposite_liquidity)

    # Mitigation: first 1-minute bar after the FVG closes that trades back up
    # to the entry. Cancel if the target prints first.
    setup.status = PENDING
    after = m1[m1.index >= max(setup.fvg_time, setup.mss_time + step)]
    for t, bar in after.iterrows():
        if t >= cutoff:
            setup.status, setup.note = EXPIRED, "FVG not mitigated before the entry cutoff"
            return
        if bar["high"] >= setup.entry:
            setup.status, setup.fill_time = FILLED, t
            return
        if cfg.cancel_if_target_hit_first and bar["low"] <= setup.tp:
            setup.status, setup.note = CANCELLED, "target reached before the FVG was mitigated"
            return
    if now >= cutoff:
        setup.status, setup.note = EXPIRED, "FVG not mitigated before the entry cutoff"
