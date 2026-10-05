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
- **Windows** (or a Windows VPS) with **MetaTrader 5 installed**. The `MetaTrader5` Python package only works on Windows.
- Python 3.10+

## Setup

### 1. Give the bot its own MT5 (your running MT5 is left alone)
The bot runs a **separate copy of MT5** in `C:\MT5-Copier` with its own new demo account. Your normal MT5 keeps
running on its usual account and is never switched or logged out.

```bat
cd telegram-mt5-copier
setup_mt5_copy.bat
```
If your MT5 came from a broker and lives somewhere else, pass that folder:
`setup_mt5_copy.bat "C:\Program Files\XM Global MT5"`.

A second MT5 window opens. **In that new window**:
1. *File → Open an Account*, pick a demo server (e.g. *MetaQuotes-Demo*, or your broker's demo), *Next*.
2. Choose **Open a demo account**, enter a name/email, pick a deposit (e.g. 10000) and leverage, tick the agreement, *Next*.
3. Note the **Login**, **Password** and **Server** it shows.
4. Click the **Algo Trading** button in the toolbar so it turns green.

### 2. Configure
```bat
python -m pip install -r requirements.txt
copy .env.example .env
```
Fill in `.env`:
- `TG_API_ID` / `TG_API_HASH`: from https://my.telegram.org → *API development tools*.
- `TG_PHONE`: your Telegram phone number, with country code.
- `TG_CHANNELS`: already set to `topg vip`. The bot finds the channel by name (case doesn't matter).
- `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER`: the **new** demo account from step 1.
- `MT5_PATH`: leave as `C:\MT5-Copier\terminal64.exe`. The bot refuses to start without it, so it can't touch your other MT5.

The first time you start the bot, Telegram sends you a login code. Enter it in the terminal (and your 2FA password if you have one).
This creates `copier.session`. **Keep it private**: it gives access to your Telegram account.
If the bot says it can't find "topg vip", or that several chats match, run `python main.py --list-chats` and put the channel's numeric ID in `TG_CHANNELS`.

Check that the channel's message format is understood:
```bat
python main.py --test "XAUUSD BUY @ 2350-2347 SL 2340 TP1 2355 TP2 2360"
```

## Run
```bat
python main.py
```
On startup the log shows the account and terminal it connected to. Check it's the new demo login in `C:\MT5-Copier`.
Start with `DRY_RUN=true` and watch `copier.log` for a few signals. When the orders look right, set `DRY_RUN=false`
and restart. Keep this window and the `C:\MT5-Copier` MT5 open; the bot only copies signals while it's running.

## Tuning
- **Stop loss:** by default (`SL_MODE=fixed`) every trade gets your own SL, measured from the entry price: forex 20 pips,
  metals 30, oil 30, crypto 30. One pip is 10 MT5 points for every symbol, as on EURUSD, so XAUUSD 30 pips = 3.00.
  Indices use the channel's SL (`SL_PIPS_INDEX=0`). Set `SL_MODE=missing` to keep the channel's SL when it gives one.
- **Lot size:** `LOT_SIZE` for fixed lots, or `RISK_PERCENT` to size from the SL distance and your balance.
- **Symbol names:** if your broker uses `XAUUSD.m`, set `SYMBOL_SUFFIX=.m`. Add channel nicknames
  (e.g. `"YEN": "USDJPY"`) to `ALIASES` in `signal_parser.py`.
- **Unusual formats:** add an example to `tests/test_parser.py`, adjust `signal_parser.py`, and run `python -m pytest tests`.

## Not handled yet
Follow-up messages such as "move SL to entry", "close half" or "cancel" are logged and ignored, not acted on.
