"""Live runner for MetaTrader 5.

Every minute it pulls recent 1-minute bars, re-runs ``scan_session`` on the
current session and acts on the result:

* PENDING  -> place a limit order at the FVG with SL/TP (once per session)
* CANCELLED / EXPIRED -> delete the unfilled order
* be_runner -> at 2R move the stop to break even (TP sits at the 15M liquidity)
* max_trade_hours reached -> close the position at market

Without ``--live`` it only logs what it would do (dry run).
The MetaTrader5 package runs on Windows with the MT5 terminal installed.
"""
from __future__ import annotations

import json
import logging
import time as systime
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import StrategyConfig
from .data import prepare
from .strategy import CANCELLED, EXPIRED, FILLED, PENDING, Setup, scan_session, session_times

log = logging.getLogger("sfx_bot")
MAGIC = 20002200


@dataclass
class LiveConfig:
    symbol: str = "XAUUSD"
    server_tz: str = "Europe/Athens"   # zone the broker's chart times are in
    risk_pct: float = 1.0              # % of balance risked per trade
    fixed_lots: float | None = None    # overrides risk_pct when set
    live: bool = False                 # False = dry run, no orders sent
    state_file: str = "sfx_state.json"
    history_bars: int = 7000           # ~5 days of M1 bars


class MT5Broker:
    def __init__(self, login: int | None = None, password: str | None = None,
                 server: str | None = None, path: str | None = None):
        import MetaTrader5 as mt5  # Windows-only package
        self.mt5 = mt5
        kwargs = {k: v for k, v in dict(login=login, password=password, server=server, path=path).items() if v}
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    def m1(self, symbol: str, bars: int, server_tz: str) -> pd.DataFrame:
        mt5 = self.mt5
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 1, bars)  # skip forming bar
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"no M1 data for {symbol}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        # MT5 stamps bars with the server's wall-clock time.
        df.index = pd.to_datetime(df["time"], unit="s").dt.tz_localize(server_tz, ambiguous="NaT",
                                                                         nonexistent="shift_forward")
        df = df[df.index.notna()].rename(columns={"tick_volume": "volume"})
        return prepare(df[["open", "high", "low", "close", "volume"]])

    def lots_for_risk(self, symbol: str, entry: float, sl: float, risk_pct: float) -> float:
        mt5 = self.mt5
        info, acct = mt5.symbol_info(symbol), mt5.account_info()
        loss_per_lot = abs(entry - sl) / info.trade_tick_size * info.trade_tick_value
        lots = acct.balance * risk_pct / 100 / loss_per_lot
        lots = int(lots / info.volume_step) * info.volume_step
        return round(min(max(lots, info.volume_min), info.volume_max), 8)

    def place_limit(self, symbol, side, price, sl, tp, lots, comment) -> int:
        mt5 = self.mt5
        info = mt5.symbol_info(symbol)
        rnd = lambda x: round(x, info.digits)  # noqa: E731
        req = {
            "action": mt5.TRADE_ACTION_PENDING, "symbol": symbol, "volume": lots,
            "type": mt5.ORDER_TYPE_BUY_LIMIT if side == "buy" else mt5.ORDER_TYPE_SELL_LIMIT,
            "price": rnd(price), "sl": rnd(sl), "tp": rnd(tp),
            "magic": MAGIC, "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        res = mt5.order_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"order_send failed: {res}")
        return res.order

    def pending(self, ticket: int):
        orders = self.mt5.orders_get(ticket=ticket)
        return orders[0] if orders else None

    def position(self, ticket: int):
        # A filled pending order opens a position with the same ticket id.
        pos = self.mt5.positions_get(ticket=ticket)
        return pos[0] if pos else None

    def cancel(self, ticket: int) -> None:
        self.mt5.order_send({"action": self.mt5.TRADE_ACTION_REMOVE, "order": ticket})

    def modify(self, pos, sl: float, tp: float) -> None:
        self.mt5.order_send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
                             "position": pos.ticket, "sl": sl, "tp": tp})

    def close(self, pos) -> None:
        mt5 = self.mt5
        tick = mt5.symbol_info_tick(pos.symbol)
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
            "magic": MAGIC, "comment": "sfx time exit", "type_filling": mt5.ORDER_FILLING_IOC,
        })


def current_session(now: pd.Timestamp, cfg: StrategyConfig) -> date:
    """The session whose 20:00 anchor is the most recent one at ``now``."""
    local = now.tz_convert(cfg.timezone)
    anchor, *_ = session_times(local.date(), cfg)
    return local.date() if local >= anchor else local.date() - timedelta(days=1)


class LiveBot:
    def __init__(self, cfg: StrategyConfig, lcfg: LiveConfig, broker: MT5Broker | None):
        self.cfg, self.lcfg, self.broker = cfg, lcfg, broker
        self.path = Path(lcfg.state_file)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.last_status = None

    def save(self):
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    def step(self, m1: pd.DataFrame) -> Setup:
        now = m1.index[-1] + pd.Timedelta(minutes=1)
        day = current_session(now, self.cfg)
        setup = scan_session(m1, day, self.cfg, now=now)
        key = str(day)
        st = self.state.setdefault(key, {})
        if setup.status != self.last_status:
            log.info("%s %s %s %s", key, setup.status, setup.side or "", setup.note)
            self.last_status = setup.status

        if setup.status == PENDING and "ticket" not in st and not st.get("skipped"):
            self.place(setup, st)
        elif setup.status == FILLED and "ticket" not in st and not st.get("skipped"):
            st["skipped"] = "FVG already mitigated when first seen"
            log.info("%s skipped: %s", key, st["skipped"])
        elif setup.status in (CANCELLED, EXPIRED) and st.get("ticket") and not st.get("closed"):
            self.cancel_if_unfilled(st)
        self.manage(setup, st, now)
        self.save()
        return setup

    def place(self, s: Setup, st: dict):
        target = s.runner_target if self.cfg.management == "be_runner" else s.tp
        msg = f"{s.side.upper()} LIMIT {self.lcfg.symbol} @ {s.entry:.3f} SL {s.sl:.3f} TP {target:.3f}"
        st.update(side=s.side, entry=s.entry, sl=s.sl, tp=s.tp, target=target)
        if not self.lcfg.live:
            log.info("[dry run] would place %s", msg)
            st["ticket"] = 0
            return
        lots = self.lcfg.fixed_lots or self.broker.lots_for_risk(self.lcfg.symbol, s.entry, s.sl, self.lcfg.risk_pct)
        st["ticket"] = self.broker.place_limit(self.lcfg.symbol, s.side, s.entry, s.sl, target, lots,
                                               f"SFX Asia {s.session}")
        st["lots"] = lots
        log.info("placed %s lots=%s ticket=%s", msg, lots, st["ticket"])

    def cancel_if_unfilled(self, st: dict):
        if self.lcfg.live and self.broker.pending(st["ticket"]):
            self.broker.cancel(st["ticket"])
            log.info("cancelled unfilled order %s", st["ticket"])
        st["closed"] = True

    def manage(self, s: Setup, st: dict, now: pd.Timestamp):
        if not self.lcfg.live or not st.get("ticket") or st.get("closed"):
            return
        pos = self.broker.position(st["ticket"])
        if pos is None:
            return
        _, _, _, close_by = session_times(s.session, self.cfg)
        if now >= close_by:
            self.broker.close(pos)
            st["closed"] = True
            log.info("time exit on %s", st["ticket"])
            return
        reached_2r = (pos.price_current - st["tp"]) * (1 if st["side"] == "buy" else -1) >= 0
        if self.cfg.management == "be_runner" and reached_2r and not st.get("at_breakeven"):
            self.broker.modify(pos, st["entry"], st["target"])
            st["at_breakeven"] = True
            log.info("2R reached, stop moved to break even on %s", st["ticket"])


def run(cfg: StrategyConfig, lcfg: LiveConfig, broker: MT5Broker) -> None:
    bot = LiveBot(cfg, lcfg, broker)
    log.info("SFX Asia session bot on %s (%s)", lcfg.symbol, "LIVE" if lcfg.live else "dry run")
    while True:
        try:
            bot.step(broker.m1(lcfg.symbol, lcfg.history_bars, lcfg.server_tz))
        except Exception:  # keep the loop alive through broker hiccups
            log.exception("step failed")
        # Wake a few seconds after each new minute bar closes.
        systime.sleep(60 - datetime.now().second + 3)
