"""Listen to Telegram signal channels and copy their trades to an MT5 demo account.

    python main.py --list-chats   # show your chats and their IDs, to fill TG_CHANNELS
    python main.py --test "XAUUSD BUY @ 2350 SL 2340 TP 2360"   # parse only
    python main.py --check        # test MT5, Telegram and the channel step by step
    python main.py                # run the copier
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from config import HERE, load_config
from signal_parser import parse_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "copier.log", encoding="utf-8")],
)
log = logging.getLogger("copier")

SEEN_FILE = HERE / "seen_messages.json"


def load_seen() -> set[str]:
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except (OSError, ValueError):
        return set()


def save_seen(seen: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:]))


async def list_chats(cfg) -> None:
    from telethon import TelegramClient

    async with TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash) as client:
        await client.start(phone=cfg.tg_phone)
        async for dialog in client.iter_dialogs():
            username = getattr(dialog.entity, "username", None)
            print(f"{dialog.id:>16}  {'@' + username if username else '':<28} {dialog.name}")


async def resolve_channels(client, wanted: list) -> list:
    """Turn channel names like "topg vip" into chat IDs; @usernames and IDs pass through."""
    names = [w for w in wanted if isinstance(w, str) and not w.startswith("@")]
    resolved = [w for w in wanted if w not in names]
    if not names:
        return resolved
    dialogs = [d async for d in client.iter_dialogs()]
    for name in names:
        key = name.casefold().strip()
        exact = [d for d in dialogs if (d.name or "").casefold().strip() == key]
        matches = exact or [d for d in dialogs if key in (d.name or "").casefold()]
        if not matches:
            raise SystemExit(f"No Telegram chat named {name!r}. Run `python main.py --list-chats` "
                             "and put the exact name or ID in TG_CHANNELS.")
        if len(matches) > 1:
            listing = "\n".join(f"  {d.id}  {d.name}" for d in matches)
            raise SystemExit(f"Several chats match {name!r}; put one ID in TG_CHANNELS:\n{listing}")
        log.info("Channel %r -> %s (id %s)", name, matches[0].name, matches[0].id)
        resolved.append(matches[0].id)
    return resolved


async def check(cfg) -> None:
    """Test each part on its own and say plainly which one fails."""
    print(f"[OK]   Settings loaded from {HERE / '.env'}")
    print(f"       Telegram channel(s): {cfg.tg_channels}   MT5 login: {cfg.mt5_login} on {cfg.mt5_server}")

    try:
        from trader import Trader
        trader = Trader(cfg)
    except ImportError:
        print("[FAIL] The MetaTrader5 package is missing. Run: python -m pip install -r requirements.txt")
        print("       (It only installs on 64-bit Windows with Python 3.8-3.13.)")
        return
    from pathlib import Path
    if not Path(cfg.mt5_path).exists():
        print(f"[FAIL] MT5_PATH {cfg.mt5_path} does not exist. Run setup_mt5_copy.bat (Part 3) or fix MT5_PATH.")
        return
    try:
        trader.connect()
    except Exception as e:
        print(f"[FAIL] MT5: {e}")
        print("       Check MT5_LOGIN / MT5_PASSWORD / MT5_SERVER match the new demo account exactly,")
        print("       and that you can log in to it by hand in the C:\\MT5-Copier window.")
        return
    mt5 = trader.mt5
    acc, term = mt5.account_info(), mt5.terminal_info()
    print(f"[OK]   MT5 connected: demo {acc.login} on {acc.server}, balance {acc.balance} {acc.currency}")
    print(f"       Terminal: {term.path}")
    if not term.trade_allowed:
        print("[FAIL] Algo Trading is OFF in that MT5 window. Click the 'Algo Trading' button so it is green.")
    for base in ("EURUSD", "XAUUSD"):
        name = trader._resolve_symbol(base)
        print(f"[OK]   Symbol {base} -> {name}" if name else
              f"[WARN] Symbol {base} not found. Check Market Watch for its name and set SYMBOL_SUFFIX.")

    from telethon import TelegramClient
    async with TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash) as client:
        await client.start(phone=cfg.tg_phone)
        me = await client.get_me()
        print(f"[OK]   Telegram logged in as {me.first_name} ({me.phone})")
        try:
            channels = await resolve_channels(client, cfg.tg_channels)
        except SystemExit as e:
            print(f"[FAIL] {e}")
            return
        for ch in channels:
            print(f"[OK]   Channel found: {ch}. Last 5 messages and how the bot reads them:")
            async for msg in client.iter_messages(ch, limit=5):
                text = (msg.raw_text or "").strip()
                if not text:
                    continue
                print("       " + "-" * 60)
                print("       " + text.replace("\n", "\n       ")[:400])
                print(f"       => {parse_signal(text) or 'not a signal'}")
    trader.shutdown()
    print("\nAll checks done. If everything above is [OK], `python main.py` will work.")


async def run(cfg) -> None:
    from telethon import TelegramClient, events

    from trader import Trader

    if not cfg.tg_channels:
        raise SystemExit("Set TG_CHANNELS in .env (run `python main.py --list-chats` to find it)")

    # MT5's Python API is not thread-safe, so every call goes through one worker thread.
    mt5_thread = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_running_loop()
    trader = Trader(cfg)
    await loop.run_in_executor(mt5_thread, trader.connect)

    seen = load_seen()
    client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash)

    async def on_message(event):
        key = f"{event.chat_id}:{event.id}"
        text = event.raw_text or ""
        if key in seen or not text.strip():
            return
        seen.add(key)
        save_seen(seen)

        age = (datetime.now(timezone.utc) - event.date).total_seconds()
        if age > cfg.max_signal_age_sec:
            log.info("Ignoring message %s: %.0fs old", key, age)
            return

        signal = parse_signal(text)
        if signal is None:
            log.info("Not a signal (%s): %r", key, text[:80])
            return
        log.info("Signal from %s: %s", key, signal)
        try:
            await loop.run_in_executor(mt5_thread, trader.execute, signal)
        except Exception:
            log.exception("Failed to execute signal %s", key)

    await client.start(phone=cfg.tg_phone)
    channels = await resolve_channels(client, cfg.tg_channels)
    client.add_event_handler(on_message, events.NewMessage(chats=channels))
    mode = "DRY RUN (no orders sent)" if cfg.dry_run else "LIVE on demo account"
    log.info("Listening to %s - %s", cfg.tg_channels, mode)
    try:
        await client.run_until_disconnected()
    finally:
        await loop.run_in_executor(mt5_thread, trader.shutdown)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-chats", action="store_true", help="print your Telegram chats and IDs")
    ap.add_argument("--test", metavar="TEXT", help="parse a signal message and print the result")
    ap.add_argument("--check", action="store_true", help="test MT5, Telegram and the channel step by step")
    args = ap.parse_args()

    if args.test:
        print(parse_signal(args.test))
        return
    cfg = load_config(need_mt5=not args.list_chats)
    if args.check:
        asyncio.run(check(cfg))
    else:
        asyncio.run(list_chats(cfg) if args.list_chats else run(cfg))


if __name__ == "__main__":
    main()
