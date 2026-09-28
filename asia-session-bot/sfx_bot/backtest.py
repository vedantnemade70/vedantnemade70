"""Session-by-session backtest on 1-minute data."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .strategy import FILLED, Setup, scan_session, session_times


@dataclass
class Trade:
    session: date
    side: str
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    exit_time: pd.Timestamp
    exit: float
    reason: str       # "tp", "sl", "breakeven", "runner", "time"
    r: float          # result in multiples of the initial risk


def simulate_trade(m1: pd.DataFrame, s: Setup, cfg: StrategyConfig) -> Trade:
    """Walk 1-minute bars from the fill until SL, TP or the time exit.

    When the stop and target fall inside the same minute the stop is assumed
    to have been hit first, and on the entry minute only the stop is checked,
    so results lean pessimistic.
    """
    _, _, _, close_by = session_times(s.session, cfg)
    d, risk = s.direction, s.risk
    sl, target = s.sl, s.tp
    at_breakeven = False
    bars = m1[(m1.index >= s.fill_time) & (m1.index < close_by)]

    def done(t, price, reason):
        return Trade(s.session, s.side, s.fill_time, s.entry, s.sl, s.tp, t, price,
                     reason, round((price - s.entry) * d / risk, 4))

    for n, (t, bar) in enumerate(bars.iterrows()):
        adverse = bar["low"] if d > 0 else bar["high"]
        favour = bar["high"] if d > 0 else bar["low"]
        if (adverse - sl) * d <= 0:
            return done(t, sl, "breakeven" if at_breakeven else "sl")
        if n == 0:
            continue
        if (favour - target) * d >= 0:
            if cfg.management == "be_runner" and not at_breakeven and s.runner_target != s.tp:
                # 2R reached: stop to break even, let it ride to the 15M liquidity.
                sl, target, at_breakeven = s.entry, s.runner_target, True
                continue
            return done(t, target, "runner" if at_breakeven else "tp")
    if bars.empty:
        return done(s.fill_time, s.entry, "time")
    return done(bars.index[-1], float(bars["close"].iloc[-1]), "time")


def sessions_in(m1: pd.DataFrame, cfg: StrategyConfig) -> list[date]:
    local = m1.index.tz_convert(cfg.timezone)
    return sorted(set(local.date))


def run_backtest(m1: pd.DataFrame, cfg: StrategyConfig):
    """Return (trades DataFrame, setups DataFrame) for every session in ``m1``."""
    trades, setups = [], []
    for day in sessions_in(m1, cfg):
        anchor, _, _, close_by = session_times(day, cfg)
        chunk = m1[(m1.index >= anchor - pd.Timedelta(days=4)) & (m1.index < close_by)]
        if chunk.empty or chunk.index[-1] < anchor:
            continue
        s = scan_session(chunk, day, cfg)
        row = asdict(s)
        row.pop("extra")
        setups.append(row)
        if s.status == FILLED:
            trades.append(asdict(simulate_trade(chunk.tz_convert(cfg.timezone), s, cfg)))
    return pd.DataFrame(trades), pd.DataFrame(setups)


def summarize(trades: pd.DataFrame, setups: pd.DataFrame, risk_pct: float = 1.0) -> dict:
    out = {"sessions": len(setups)}
    if len(setups):
        out.update({f"status_{k}": int(v) for k, v in setups["status"].value_counts().items()})
    out["trades"] = len(trades)
    if trades.empty:
        return out
    r = trades["r"].to_numpy(dtype=float)
    wins, losses = r[r > 0], r[r < 0]
    equity = np.cumprod(1 + r * risk_pct / 100)
    peak = np.maximum.accumulate(equity)
    out.update({
        "win_rate_%": round(100 * len(wins) / len(r), 1),
        "total_R": round(float(r.sum()), 2),
        "avg_R": round(float(r.mean()), 3),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) else float("inf"),
        "max_drawdown_%": round(float(100 * (1 - equity / peak).max()), 2),
        f"return_%_at_{risk_pct:g}%_risk": round(float(100 * (equity[-1] - 1)), 2),
    })
    out.update({f"exit_{k}": int(v) for k, v in trades["reason"].value_counts().items()})
    return out
