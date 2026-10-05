import sys
import types
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from signal_parser import Signal  # noqa: E402


def fake_mt5(trade_mode=0):
    m = types.ModuleType("MetaTrader5")
    consts = dict(
        ACCOUNT_TRADE_MODE_DEMO=0, TRADE_ACTION_DEAL=1, TRADE_ACTION_PENDING=5,
        ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, ORDER_TYPE_BUY_LIMIT=2, ORDER_TYPE_SELL_LIMIT=3,
        ORDER_TYPE_BUY_STOP=4, ORDER_TYPE_SELL_STOP=5, ORDER_TIME_GTC=0,
        ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2,
        TRADE_RETCODE_DONE=10009, TRADE_RETCODE_PLACED=10008,
    )
    vars(m).update(consts)
    m.sent = []
    m.initialize = lambda *a, **k: True
    m.shutdown = lambda: None
    m.last_error = lambda: (0, "")
    m.account_info = lambda: NS(login=1, server="Demo", balance=10000.0, currency="USD", trade_mode=trade_mode)
    m.terminal_info = lambda: NS(trade_allowed=True)
    m.symbol_info = lambda name: NS(point=0.01, digits=2, volume_min=0.01, volume_step=0.01, volume_max=100,
                                    trade_tick_size=0.01, trade_tick_value=1.0, filling_mode=2) if name == "XAUUSD" else None
    m.symbol_select = lambda name, on: True
    m.symbol_info_tick = lambda name: NS(ask=2350.2, bid=2350.0)

    def order_send(req):
        m.sent.append(req)
        return NS(retcode=10009, comment="done", order=len(m.sent), price=req["price"])

    m.order_send = order_send
    sys.modules["MetaTrader5"] = m
    return m


def cfg(**over):
    base = dict(mt5_login=1, mt5_password="x", mt5_server="Demo", mt5_path="", lot_size=0.03, risk_percent=0,
                max_lot=1.0, max_tps=3, default_sl_points=0, max_slippage_points=30, symbol_suffix="", dry_run=False)
    base.update(over)
    return NS(**base)


def test_refuses_real_account():
    fake_mt5(trade_mode=2)
    from trader import Trader
    try:
        Trader(cfg()).connect()
    except RuntimeError as e:
        assert "NOT a demo" in str(e)
    else:
        raise AssertionError("should refuse a real account")


def test_splits_lot_across_tps():
    m = fake_mt5()
    from trader import Trader
    t = Trader(cfg())
    t.connect()
    t.execute(Signal("XAUUSD", "BUY", "MARKET", None, 2340.0, [2355.0, 2360.0, 2370.0]))
    assert [r["volume"] for r in m.sent] == [0.01, 0.01, 0.01]
    assert [r["tp"] for r in m.sent] == [2355.0, 2360.0, 2370.0]
    assert all(r["sl"] == 2340.0 and r["type_filling"] == 1 for r in m.sent)


def test_risk_percent_sizing():
    m = fake_mt5()
    from trader import Trader
    # 1% of 10000 = 100 USD; SL 10.2 away = 1020 ticks * 1.0 = 1020 per lot -> 0.098 lots -> 0.09
    Trader(cfg(risk_percent=1)).execute(Signal("XAUUSD", "BUY", "MARKET", None, 2340.0, [2360.0]))
    assert m.sent[0]["volume"] == 0.09


def test_skips_market_signal_past_tp1():
    m = fake_mt5()
    from trader import Trader
    Trader(cfg()).execute(Signal("XAUUSD", "BUY", "MARKET", None, 2330.0, [2345.0]))
    assert m.sent == []


def test_pending_order():
    m = fake_mt5()
    from trader import Trader
    Trader(cfg()).execute(Signal("XAUUSD", "SELL", "LIMIT", 2360.0, 2370.0, [2340.0]))
    assert m.sent[0]["action"] == 5 and m.sent[0]["type"] == 3 and m.sent[0]["price"] == 2360.0
