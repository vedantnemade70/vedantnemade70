# Telegram → MT5 signal copier (demo only)

Reads forex signals from a Telegram channel you're a member of, and places the trades
on a **MetaTrader 5 demo account**. It checks the account type when it connects and won't run
on a real account.

## How it works
1. Logs in to **your own Telegram account** with [Telethon](https://docs.telethon.dev) and listens to the channels you list.
2. Each new message goes through `signal_parser.py`, which picks out the symbol, BUY/SELL, market/limit/stop, entry, SL and TPs.
   Messages that aren't signals ("TP1 hit 🎉", "good morning") are skipped.
3. `trader.py` sends the orders through the `MetaTrader5` Python package. It opens one position per TP (up to `MAX_TPS`)
   and splits the lot size between them.

Safety checks:
- Won't trade unless the MT5 account is a demo account.
- Skips a market signal if the price has already passed the SL or TP1.
- Skips signals whose SL and TPs are on the wrong side of the entry (usually a misread message).
- Ignores messages older than `MAX_SIGNAL_AGE_SEC` and never trades the same message twice.
- `DRY_RUN=true` (the default) only logs what it would do.

## Requirements
- **Windows** (or a Windows VPS) with the **MT5 terminal installed** and logged in to your demo account.
  The `MetaTrader5` Python package only works on Windows, next to a running terminal.
- Python 3.10+
- In MT5, turn on **Algo Trading** (toolbar button) and check *Tools → Options → Expert Advisors → Allow algorithmic trading*.

## Setup
```bat
cd telegram-mt5-copier
python -m pip install -r requirements.txt
copy .env.example .env
```
Fill in `.env`:
- `TG_API_ID` / `TG_API_HASH`: from https://my.telegram.org → *API development tools*.
- `TG_PHONE`: your Telegram phone number, with country code.
- `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER`: your demo account details.

Find the channel:
```bat
python main.py --list-chats
```
The first time, Telegram sends you a login code. Enter it in the terminal (and your 2FA password if you have one).
This creates `copier.session`. **Keep it private**: it gives access to your Telegram account.
Put the channel's `@username` or numeric ID in `TG_CHANNELS`.

Check that the channel's message format is understood:
```bat
python main.py --test "XAUUSD BUY @ 2350-2347 SL 2340 TP1 2355 TP2 2360"
```

## Run
```bat
python main.py
```
Start with `DRY_RUN=true` and watch `copier.log` for a few signals. When the orders look right, set `DRY_RUN=false`
and restart. Keep the window (and MT5) open; the bot only copies signals while it's running.

## Tuning
- **Lot size:** `LOT_SIZE` for fixed lots, or `RISK_PERCENT` to size from the SL distance and your balance.
- **Symbol names:** if your broker uses `XAUUSD.m`, set `SYMBOL_SUFFIX=.m`. Add channel nicknames
  (e.g. `"YEN": "USDJPY"`) to `ALIASES` in `signal_parser.py`.
- **Unusual formats:** add an example to `tests/test_parser.py`, adjust `signal_parser.py`, and run `python -m pytest tests`.

## Not handled yet
Follow-up messages such as "move SL to entry", "close half" or "cancel" are logged and ignored, not acted on.
