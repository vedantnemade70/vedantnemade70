# SFX Asia Session Bot

A trading bot for the **PO3 Asia Session strategy** by ShaylinFX (@ShaylinFX), for
**XAU/USD** and **XXX/JPY** pairs. It includes a backtester for CSV data and a
live runner for MetaTrader 5.

## The strategy (from the PDF)

| Step | Rule | How the bot does it |
|---|---|---|
| 1 | 15M chart, New York time (UTC-4), mark **20:00** | 1-minute bars are resampled to 15M in `America/New_York` |
| 2 | Mark the nearest **buyside (BSL)** and **sellside (SSL)** liquidity | Nearest untouched 15M swing high above / swing low below the 20:00 price (swing = 2 bars each side, last 24h) |
| 3 | Wait for BSL or SSL to be **purged between 20:00 and 22:00**. No purge = session invalid | First 1M bar to trade through a level. BSL purged means sells, SSL purged means buys |
| 3 | After the purge, on **1M/3M** wait for an **MSS** and an **FVG** | MSS = a 3M candle closes beyond the last swing point before the purge extreme. FVG = 3-candle gap whose middle candle is the MSS candle or later |
| 4 | Enter on **mitigation** of the FVG, **SL at the FVG formation**, target **1:2 RR** | Limit order at the FVG edge, SL at the high/low of the 3 FVG candles, TP = 2R |
| e.g. | After 2R, SL to break even, ride to the 15M opposite liquidity | Optional: `--management be_runner` |

A few things the thread doesn't spell out. Each is a setting, and these are the defaults:

- A setup can still enter until 00:00 (`--entry-cutoff 4` hours after 20:00). Any open trade is closed at 07:00.
- If price reaches the target before coming back to the FVG, the order is cancelled.
- If price makes a new high/low beyond the purge before an FVG forms, the setup is cancelled.
- One trade per session.

## Install

```bash
cd asia-session-bot
pip install -r requirements.txt          # pandas, numpy (+ pytest for tests)
pip install MetaTrader5                  # only for live trading, Windows only
```

## Backtest

You need **1-minute** OHLC data in a CSV file. Exports from MT5, Dukascopy and
HistData work; the columns are detected automatically.

```bash
python -m sfx_bot backtest XAUUSD_M1.csv --data-tz UTC
python -m sfx_bot backtest XAUUSD_M1.csv --data-tz Europe/Athens --management be_runner --out trades.csv
python -m sfx_bot backtest USDJPY_M1.csv --ltf 1 --sl-buffer 0.02
```

`--data-tz` is the time zone the timestamps in your file are in. MT5 exports use
the broker's server time, which is `Europe/Athens` (GMT+2/+3) for most brokers.
If this is wrong, the 20:00 mark lands on the wrong candle.

The output shows win rate, total R, profit factor, drawdown and the latest trades.
`--out` also saves every session's setup (levels, purge, MSS, FVG, why it was
skipped) to `trades_setups.csv`, so you can check sessions against your charts.

`python -m sfx_bot demo` runs the same pipeline on random synthetic prices. It's
a smoke test only; the numbers mean nothing.

## Live on MetaTrader 5

```bash
# Dry run: logs levels, purges and the orders it would place
python -m sfx_bot live --symbol XAUUSD --server-tz Europe/Athens

# Real orders, 1% risk per trade (test on a demo account first!)
python -m sfx_bot live --symbol XAUUSD --server-tz Europe/Athens --risk 1 --live
```

Leave the MT5 terminal open and logged in (or pass `--login/--password/--server`).
Every minute the bot re-checks the session. When an FVG appears it places a
**limit order** with SL and TP. It cancels the order if the setup breaks or the
cutoff passes. With `be_runner` it moves the stop to break even at 2R. State is
kept in `sfx_state.json`, so a restart won't place the same trade twice.

## All options

`--ltf {1,3}` · `--rr 2` · `--management fixed|be_runner` · `--entry-mode edge|mid`
(`mid` = 50% of the FVG) · `--sl-mode fvg|swing` (`swing` = beyond the purge wick) ·
`--sl-buffer` · `--min-fvg` · `--htf-swing` · `--ltf-swing` · `--entry-cutoff` ·
`--timezone`. Run `python -m sfx_bot backtest -h` for details.

## Code

```
sfx_bot/
  config.py     all strategy settings and their defaults
  strategy.py   the rules: liquidity -> purge -> MSS -> FVG -> entry (scan_session)
  backtest.py   trade simulation and statistics
  live.py       MetaTrader 5 runner
  data.py       CSV loading, resampling, synthetic data
tests/          scenario tests (sell, mirrored buy, invalid sessions, no look-ahead, live bot)
```

The backtester and the live bot share `scan_session`. It only reads candles that
have already closed, so a backtest follows the same logic the bot uses live.
If the stop and target are both hit inside the same minute, the backtest counts
the stop.

## Disclaimer

This is a tool, not financial advice. The strategy comes from a social-media
thread and hasn't been shown to be profitable. Backtest it on real data and run
it on a demo account before risking money.
