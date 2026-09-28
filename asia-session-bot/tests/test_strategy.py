from datetime import date

import numpy as np
import pandas as pd
import pytest

from sfx_bot.backtest import run_backtest, simulate_trade, summarize
from sfx_bot.config import StrategyConfig
from sfx_bot.data import synthetic_minutes
from sfx_bot.strategy import (CANCELLED, FILLED, INVALID, PENDING, WAITING_PURGE,
                              nearest_liquidity, scan_session)

TZ = "America/New_York"
DAY = date(2024, 8, 1)


def path(points, wick=0.05):
    """1-minute bars that follow straight lines between (HH:MM, price) points."""
    times = [pd.Timestamp(f"{DAY} {t}", tz=TZ) for t, _ in points]
    idx = pd.date_range(times[0], times[-1], freq="1min")
    minutes = (idx - times[0]).total_seconds() / 60
    knots = [(t - times[0]).total_seconds() / 60 for t in times]
    close = np.interp(minutes, knots, [p for _, p in points])
    open_ = np.concatenate([[close[0]], close[:-1]])
    df = pd.DataFrame({"open": open_, "close": close}, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1) + wick
    df["low"] = df[["open", "close"]].min(axis=1) - wick
    return df[["open", "high", "low", "close"]].iloc[:-1]


# Like the thread's gold example: range into 20:00, BSL swept after 20:00,
# structure shifts down, an FVG forms, price retraces into it, then sells off.
SELL_DAY = [
    ("14:00", 100.0), ("16:00", 100.6), ("17:00", 99.8),
    ("18:30", 102.0),   # 15M swing high -> BSL 102.05
    ("19:15", 96.0),    # 15M swing low  -> SSL 95.95
    ("19:45", 100.5), ("20:00", 100.0),
    ("20:15", 101.5), ("20:24", 101.0),   # higher low on the 3M chart
    ("20:36", 102.6),                     # purge of BSL
    ("20:42", 102.0),
    ("20:45", 100.2),                     # displacement: MSS + FVG
    ("20:54", 100.9),                     # retrace into the FVG (entry)
    ("21:30", 95.0), ("23:00", 94.8),
]


def test_marks_nearest_untouched_liquidity():
    m1 = path(SELL_DAY)
    htf = m1[m1.index < pd.Timestamp(f"{DAY} 20:00", tz=TZ)].resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    ref, bsl, ssl = nearest_liquidity(htf, 2)
    assert bsl == pytest.approx(102.05)
    assert ssl == pytest.approx(95.95)
    assert ssl < ref < bsl


def test_sell_setup_after_buyside_purge():
    cfg = StrategyConfig()
    m1 = path(SELL_DAY)
    s = scan_session(m1, DAY, cfg)
    assert s.status == FILLED, s.note
    assert s.side == "sell"
    assert s.purge_time.strftime("%H:%M") < "22:00"
    assert s.mss_time > s.purge_time
    assert s.entry == s.fvg_low < s.fvg_high  # sell enters at the bottom edge
    assert s.sl > s.entry > s.tp
    assert s.entry - s.tp == pytest.approx(2 * (s.sl - s.entry))
    trade = simulate_trade(m1.tz_convert(TZ), s, cfg)
    assert trade.reason == "tp" and trade.r == pytest.approx(2.0)


def test_buy_setup_is_the_mirror_image():
    cfg = StrategyConfig()
    mirrored = [(t, 200.0 - p) for t, p in SELL_DAY]
    sell = scan_session(path(SELL_DAY), DAY, cfg)
    buy = scan_session(path(mirrored), DAY, cfg)
    assert buy.status == FILLED and buy.side == "buy"
    for name in ("entry", "sl", "tp", "bsl"):
        other = {"bsl": "ssl"}.get(name, name)
        assert getattr(buy, name) == pytest.approx(200.0 - getattr(sell, other))
    assert buy.fill_time == sell.fill_time


def test_be_runner_rides_to_opposite_liquidity():
    cfg = StrategyConfig(management="be_runner")
    m1 = path(SELL_DAY)
    s = scan_session(m1, DAY, cfg)
    assert s.runner_target == pytest.approx(s.ssl)
    trade = simulate_trade(m1.tz_convert(TZ), s, cfg)
    assert trade.reason == "runner" and trade.r > 2


def test_no_purge_in_window_is_invalid():
    quiet = SELL_DAY[:7] + [("22:30", 100.2), ("23:00", 100.0)]
    s = scan_session(path(quiet), DAY, StrategyConfig())
    assert s.status == INVALID


def test_purge_after_22_00_is_invalid():
    late = SELL_DAY[:7] + [("21:59", 100.4)] + [
        (f"{int(t[:2]) + 2}:{t[3:]}", p) for t, p in SELL_DAY[7:13]] + [("23:30", 97.0)]
    s = scan_session(path(late), DAY, StrategyConfig())
    assert s.status == INVALID


def test_scan_never_uses_future_bars():
    cfg = StrategyConfig()
    m1 = path(SELL_DAY)
    early = scan_session(m1, DAY, cfg, now=pd.Timestamp(f"{DAY} 20:10", tz=TZ))
    assert early.status == WAITING_PURGE
    # Right after the FVG forms but before the retrace reaches it.
    full = scan_session(m1, DAY, cfg)
    mid = scan_session(m1, DAY, cfg, now=full.fvg_time + pd.Timedelta(minutes=1))
    assert mid.status == PENDING
    assert mid.entry == pytest.approx(full.entry)


def test_cancel_when_target_prints_before_mitigation():
    no_retrace = SELL_DAY[:11] + [("20:45", 100.2), ("21:10", 96.0), ("23:00", 96.0)]
    s = scan_session(path(no_retrace), DAY, StrategyConfig())
    assert s.status == CANCELLED


def test_backtest_runs_on_synthetic_data():
    trades, setups = run_backtest(synthetic_minutes(30), StrategyConfig())
    stats = summarize(trades, setups)
    assert stats["sessions"] > 15
    if len(trades):
        assert set(trades["reason"]) <= {"tp", "sl", "time"}
        assert trades["r"].max() <= 2.0 + 1e-9


class FakeBroker:
    """Stands in for MT5: records orders and fills them like a limit would."""

    def __init__(self):
        self.orders, self.cancelled, self.modified = {}, [], []

    def lots_for_risk(self, *a):
        return 0.1

    def place_limit(self, symbol, side, price, sl, tp, lots, comment):
        self.orders[1] = dict(side=side, price=price, sl=sl, tp=tp)
        return 1

    def pending(self, ticket):
        return self.orders.get(ticket)

    def cancel(self, ticket):
        self.cancelled.append(ticket)

    def position(self, ticket):
        return None


def test_live_bot_places_one_order_per_session(tmp_path):
    from sfx_bot.live import LiveBot, LiveConfig
    cfg = StrategyConfig()
    broker = FakeBroker()
    bot = LiveBot(cfg, LiveConfig(live=True, state_file=str(tmp_path / "s.json")), broker)
    m1 = path(SELL_DAY)
    start = m1.index.get_loc(pd.Timestamp(f"{DAY} 20:00", tz=TZ))
    for i in range(start, start + 120):  # replay 20:00-22:00 one closed bar at a time
        bot.step(m1.iloc[:i])
    full = scan_session(m1, DAY, cfg)
    assert broker.orders == {1: dict(side="sell", price=full.entry, sl=full.sl, tp=full.tp)}
    assert bot.state[str(DAY)]["ticket"] == 1


def test_cli_backtest_reads_csv(tmp_path, capsys):
    from sfx_bot.__main__ import main
    csv = tmp_path / "xau.csv"
    synthetic_minutes(20).rename_axis("time").to_csv(csv)
    main(["backtest", str(csv), "--out", str(tmp_path / "t.csv")])
    assert '"sessions"' in capsys.readouterr().out
    assert (tmp_path / "t_setups.csv").exists()
