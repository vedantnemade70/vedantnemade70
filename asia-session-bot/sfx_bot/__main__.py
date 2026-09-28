"""Command line: python -m sfx_bot {backtest,demo,live} ..."""
from __future__ import annotations

import argparse
import json
import logging

import pandas as pd

from .backtest import run_backtest, summarize
from .config import StrategyConfig
from .data import load_csv, synthetic_minutes


def add_strategy_args(p: argparse.ArgumentParser) -> None:
    d = StrategyConfig()
    g = p.add_argument_group("strategy")
    g.add_argument("--timezone", default=d.timezone, help="session time zone (default %(default)s)")
    g.add_argument("--ltf", type=int, choices=[1, 3], default=d.ltf_minutes, help="MSS/FVG timeframe in minutes")
    g.add_argument("--rr", type=float, default=d.rr, help="reward:risk target (default %(default)s)")
    g.add_argument("--management", choices=["fixed", "be_runner"], default=d.management,
                   help="fixed = exit at 2R; be_runner = BE at 2R and ride to opposite 15M liquidity")
    g.add_argument("--entry-mode", choices=["edge", "mid"], default=d.entry_mode)
    g.add_argument("--sl-mode", choices=["fvg", "swing"], default=d.sl_mode)
    g.add_argument("--sl-buffer", type=float, default=d.sl_buffer, help="extra SL distance in price")
    g.add_argument("--min-fvg", type=float, default=d.min_fvg_size, help="minimum FVG size in price")
    g.add_argument("--htf-swing", type=int, default=d.htf_swing_strength)
    g.add_argument("--ltf-swing", type=int, default=d.ltf_swing_strength)
    g.add_argument("--entry-cutoff", type=float, default=d.entry_cutoff_hours, help="hours after 20:00")


def strategy_from(a) -> StrategyConfig:
    return StrategyConfig(timezone=a.timezone, ltf_minutes=a.ltf, rr=a.rr, management=a.management,
                          entry_mode=a.entry_mode, sl_mode=a.sl_mode, sl_buffer=a.sl_buffer,
                          min_fvg_size=a.min_fvg, htf_swing_strength=a.htf_swing,
                          ltf_swing_strength=a.ltf_swing, entry_cutoff_hours=a.entry_cutoff)


def report(m1: pd.DataFrame, cfg: StrategyConfig, a) -> None:
    trades, setups = run_backtest(m1, cfg)
    print(json.dumps(summarize(trades, setups, a.risk), indent=2))
    if len(trades):
        cols = ["session", "side", "entry_time", "entry", "sl", "tp", "exit", "reason", "r"]
        print(trades[cols].tail(a.show).to_string(index=False))
    if a.out:
        trades.to_csv(a.out, index=False)
        setups.to_csv(a.out.replace(".csv", "") + "_setups.csv", index=False)
        print(f"saved {a.out}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="sfx_bot", description="SFX PO3 Asia session strategy bot")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="backtest on a CSV of 1-minute bars")
    bt.add_argument("csv")
    bt.add_argument("--data-tz", default="UTC", help="time zone of the CSV timestamps (default UTC)")
    bt.add_argument("--date-format", default=None, help="strftime format if timestamps are unusual")

    demo = sub.add_parser("demo", help="backtest on random synthetic data (smoke test, not a result)")
    demo.add_argument("--days", type=int, default=120)
    demo.add_argument("--seed", type=int, default=7)

    for p in (bt, demo):
        p.add_argument("--risk", type=float, default=1.0, help="%% risk per trade for the equity stats")
        p.add_argument("--out", help="write trades to this CSV")
        p.add_argument("--show", type=int, default=20, help="how many recent trades to print")
        add_strategy_args(p)

    lv = sub.add_parser("live", help="run on MetaTrader 5 (dry run unless --live)")
    lv.add_argument("--symbol", default="XAUUSD")
    lv.add_argument("--server-tz", required=True,
                    help="time zone of the broker's MT5 chart times, e.g. Europe/Athens for GMT+2/+3 brokers")
    lv.add_argument("--risk", type=float, default=1.0, help="%% of balance risked per trade")
    lv.add_argument("--lots", type=float, default=None, help="fixed lot size instead of %% risk")
    lv.add_argument("--live", action="store_true", help="send real orders (default: log only)")
    lv.add_argument("--state-file", default="sfx_state.json")
    lv.add_argument("--login", type=int)
    lv.add_argument("--password")
    lv.add_argument("--server")
    lv.add_argument("--terminal-path")
    add_strategy_args(lv)

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = strategy_from(a)

    if a.cmd == "backtest":
        report(load_csv(a.csv, a.data_tz, a.date_format), cfg, a)
    elif a.cmd == "demo":
        report(synthetic_minutes(a.days, seed=a.seed), cfg, a)
    else:
        from .live import LiveConfig, MT5Broker, run
        broker = MT5Broker(a.login, a.password, a.server, a.terminal_path)
        run(cfg, LiveConfig(symbol=a.symbol, server_tz=a.server_tz, risk_pct=a.risk,
                            fixed_lots=a.lots, live=a.live, state_file=a.state_file), broker)


if __name__ == "__main__":
    main()
