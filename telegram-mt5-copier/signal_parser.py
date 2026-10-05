"""Turn the text of a Telegram forex signal into a structured Signal.

Handles the common formats used by signal channels, for example:

    XAUUSD BUY @ 2350 - 2347
    SL 2340
    TP1 2355
    TP2 2360

    EUR/USD sell limit 1.0850  sl: 1.0880  tp: 1.0800

    GOLD SELL NOW
    Stop Loss : 2365
    Take Profit 1 : 2350
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM = r"(\d+(?:\.\d+)?)"

# Names channels use for instruments, mapped to the usual MT5 symbol.
ALIASES = {
    "GOLD": "XAUUSD",
    "SILVER": "XAGUSD",
    "OIL": "USOIL",
    "WTI": "USOIL",
    "BRENT": "UKOIL",
    "BITCOIN": "BTCUSD",
    "BTC": "BTCUSD",
    "ETH": "ETHUSD",
    "DOW": "US30",
    "DJ30": "US30",
    "US30": "US30",
    "NASDAQ": "NAS100",
    "NAS100": "NAS100",
    "US100": "NAS100",
    "SPX": "US500",
    "SP500": "US500",
    "US500": "US500",
    "DAX": "GER40",
    "GER40": "GER40",
    "GER30": "GER40",
}

CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD",
    "XAU", "XAG", "SGD", "HKD", "NOK", "SEK", "ZAR", "MXN", "TRY", "PLN", "BTC", "ETH",
}

SIDE_RE = re.compile(r"\b(BUY|SELL)\b(?:\s+(LIMIT|STOP)\b(?!\s*LOSS))?")
SL_RE = re.compile(r"\b(?:SL|STOP\s*LOSS)\b\s*[:=@\-]?\s*" + NUM)
TP_RE = re.compile(r"\b(?:TP|TAKE\s*PROFIT)\s*\d?\b\s*[:=@\-]?\s*" + NUM)
# Entry: "@ 2350", "entry 2350", "at 2350", or a number right after BUY/SELL (LIMIT/STOP/NOW).
ENTRY_RE = re.compile(
    r"(?:@|\bENTRY(?:\s*PRICE)?\b|\bAT\b|\b(?:BUY|SELL)(?:\s+(?:LIMIT|STOP|NOW))?)"
    r"\s*[:=]?\s*" + NUM + r"(?:\s*(?:-|/|TO)\s*" + NUM + r")?"
)


@dataclass
class Signal:
    symbol: str
    side: str  # "BUY" or "SELL"
    order_type: str  # "MARKET", "LIMIT" or "STOP"
    entry: float | None
    sl: float | None
    tps: list[float] = field(default_factory=list)


def _find_symbol(text: str) -> str | None:
    compact = re.sub(r"([A-Z]{3})\s*/\s*([A-Z]{3})", r"\1\2", text)
    for word in re.findall(r"[A-Z0-9]+", compact):
        if word in ALIASES:
            return ALIASES[word]
        if len(word) == 6 and word[:3] in CURRENCIES and word[3:] in CURRENCIES:
            return word
    return None


def parse_signal(text: str) -> Signal | None:
    """Return a Signal, or None when the message is not a usable trade signal."""
    upper = text.upper()

    side_match = SIDE_RE.search(upper)
    symbol = _find_symbol(upper)
    if not side_match or not symbol:
        return None

    side = side_match.group(1)
    order_type = side_match.group(2) or "MARKET"

    sl_match = SL_RE.search(upper)
    sl = float(sl_match.group(1)) if sl_match else None
    tps = [float(v) for v in TP_RE.findall(upper)]

    entry = None
    entry_match = ENTRY_RE.search(upper)
    if entry_match:
        # For a range like "2350 - 2347" use the first price the channel quotes.
        entry = float(entry_match.group(1))
        if entry in (sl, *tps):
            entry = None

    if order_type != "MARKET" and entry is None:
        return None
    if sl is None and not tps:
        # A bare "BUY GOLD" with no levels is usually chatter, not a signal.
        return None

    signal = Signal(symbol, side, order_type, entry, sl, tps)
    return signal if _levels_consistent(signal) else None


def _levels_consistent(s: Signal) -> bool:
    """Reject signals whose SL/TP sit on the wrong side (usually a parse error)."""
    ref = s.entry
    if s.side == "BUY":
        if s.sl is not None and any(tp <= s.sl for tp in s.tps):
            return False
        if ref is not None and s.sl is not None and s.sl >= ref:
            return False
        if ref is not None and any(tp <= ref for tp in s.tps):
            return False
    else:
        if s.sl is not None and any(tp >= s.sl for tp in s.tps):
            return False
        if ref is not None and s.sl is not None and s.sl <= ref:
            return False
        if ref is not None and any(tp >= ref for tp in s.tps):
            return False
    return True
