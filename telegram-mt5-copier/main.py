"""Listen to Telegram signal channels and copy their trades to an MT5 demo account.

    python main.py --list-chats   # show your chats and their IDs, to fill TG_CHANNELS
    python main.py --test "XAUUSD BUY @ 2350 SL 2340 TP 2360"   # parse only
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

    @client.on(events.NewMessage(chats=cfg.tg_channels))
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
    args = ap.parse_args()

    if args.test:
        print(parse_signal(args.test))
        return
    cfg = load_config(need_mt5=not args.list_chats)
    asyncio.run(list_chats(cfg) if args.list_chats else run(cfg))


if __name__ == "__main__":
    main()
