import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signal_parser import Signal, parse_signal  # noqa: E402


def test_gold_market_with_range_and_multiple_tps():
    msg = """XAUUSD BUY @ 2350 - 2347
SL 2340
TP1 2355
TP2 2360
TP3 2370"""
    assert parse_signal(msg) == Signal("XAUUSD", "BUY", "MARKET", 2350.0, 2340.0, [2355.0, 2360.0, 2370.0])


def test_slash_pair_sell_limit_inline():
    msg = "EUR/USD sell limit 1.0850  sl: 1.0880  tp: 1.0800"
    assert parse_signal(msg) == Signal("EURUSD", "SELL", "LIMIT", 1.085, 1.088, [1.08])


def test_alias_and_long_labels_without_entry():
    msg = """🔥 GOLD SELL NOW 🔥
Stop Loss : 2365
Take Profit 1 : 2350
Take Profit 2 : 2340"""
    assert parse_signal(msg) == Signal("XAUUSD", "SELL", "MARKET", None, 2365.0, [2350.0, 2340.0])


def test_sell_stop_is_not_stop_loss():
    msg = "GBPJPY SELL STOP 190.50 SL 191.20 TP 189.00"
    assert parse_signal(msg) == Signal("GBPJPY", "SELL", "STOP", 190.5, 191.2, [189.0])


def test_buy_now_with_price():
    msg = "US30 buy now 39000 sl 38900 tp 39200"
    assert parse_signal(msg) == Signal("US30", "BUY", "MARKET", 39000.0, 38900.0, [39200.0])


def test_tp_open_is_ignored():
    msg = "BTCUSD BUY\nSL 60000\nTP1 62000\nTP2 open"
    assert parse_signal(msg) == Signal("BTCUSD", "BUY", "MARKET", None, 60000.0, [62000.0])


def test_chatter_is_not_a_signal():
    assert parse_signal("Good morning traders! Gold looks bullish today") is None
    assert parse_signal("TP1 hit on XAUUSD +50 pips 🎉") is None
    assert parse_signal("Buy gold") is None  # no levels


def test_inconsistent_levels_rejected():
    # SL above TP on a buy means something was misread.
    assert parse_signal("XAUUSD BUY 2350 SL 2360 TP 2340") is None


def test_pending_order_needs_entry():
    assert parse_signal("EURUSD BUY LIMIT SL 1.0800 TP 1.0900") is None
