"""Place trades for a parsed Signal on a MetaTrader 5 *demo* account."""

from __future__ import annotations

import logging
import math

from signal_parser import Signal

log = logging.getLogger("trader")

MAGIC = 260705
COMMENT = "tg-copier"


METALS = ("XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER")
OILS = ("USOIL", "UKOIL", "WTI", "BRENT", "XTI", "XBR", "OIL")
CRYPTO = ("BTC", "ETH", "LTC", "XRP", "SOL")


def sl_category(symbol: str) -> str:
    """Which SL_PIPS_* setting applies to a symbol."""
    s = symbol.upper()
    if s.startswith(METALS):
        return "metal"
    if any(o in s for o in OILS):
        return "oil"
    if s.startswith(CRYPTO):
        return "crypto"
    if len(s) >= 6 and s[:6].isalpha():
        return "forex"
    return "index"


class Trader:
    def __init__(self, cfg):
        import MetaTrader5 as mt5  # Windows-only package, imported lazily

        self.mt5 = mt5
        self.cfg = cfg

    # ---- connection -------------------------------------------------------

    def connect(self) -> None:
        mt5, cfg = self.mt5, self.cfg
        kwargs = {}
        if cfg.mt5_password:
            kwargs = {"login": cfg.mt5_login, "password": cfg.mt5_password, "server": cfg.mt5_server}
        # Always start our own terminal (MT5_PATH, portable) so an MT5 you already have running
        # keeps its own account and is never switched over.
        ok = mt5.initialize(cfg.mt5_path, portable=True, **kwargs)
        if not ok:
            code, msg = mt5.last_error()
            hint = ""
            if code == -6:
                hint = (" The demo server rejected the login. Use the main 'Password' (not the 'Investor' one),"
                        " check MT5_LOGIN and MT5_SERVER, or leave MT5_PASSWORD empty and log in by hand"
                        " in the MT5_PATH window with 'Save password' ticked.")
            raise RuntimeError(f"MT5 initialize failed: ({code}, {msg!r}).{hint}")

        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"No account is logged in to the MT5_PATH terminal ({mt5.last_error()}). "
                               "Log in there by hand with 'Save password' ticked, or fill MT5_PASSWORD.")
        if cfg.mt5_login and info.login != cfg.mt5_login:
            mt5.shutdown()
            raise RuntimeError(f"The MT5_PATH terminal is logged in to {info.login}, not MT5_LOGIN {cfg.mt5_login}. "
                               "Log in to the right account in that window, or fix MT5_LOGIN.")
        if info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
            mt5.shutdown()
            raise RuntimeError(
                f"Account {info.login} on {info.server} is NOT a demo account. "
                "This bot only trades demo accounts; refusing to continue."
            )
        if not mt5.terminal_info().trade_allowed:
            log.warning("Algo trading is disabled in the MT5 terminal. Enable the 'Algo Trading' button.")
        log.info("Connected to demo account %s on %s, balance %.2f %s (terminal: %s)",
                 info.login, info.server, info.balance, info.currency, mt5.terminal_info().path)

    def shutdown(self) -> None:
        self.mt5.shutdown()

    # ---- trading ----------------------------------------------------------

    def execute(self, sig: Signal) -> list:
        mt5, cfg = self.mt5, self.cfg

        symbol = self._resolve_symbol(sig.symbol)
        if symbol is None:
            log.error("Symbol %s not found at this broker (suffix=%r)", sig.symbol, cfg.symbol_suffix)
            return []
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            log.error("No price data for %s: %s", symbol, mt5.last_error())
            return []

        buy = sig.side == "BUY"
        market_price = tick.ask if buy else tick.bid

        if sig.order_type == "MARKET":
            price = market_price
            if not self._market_still_valid(sig, price):
                return []
        else:
            price = sig.entry

        tps = sig.tps[: cfg.max_tps] if sig.tps else [None]
        sl = self._stop_loss(sig, buy, price, info)
        spread = tick.ask - tick.bid
        if sl and abs(price - sl) <= spread:
            log.warning("%s SL %s is only %s from price, inside the %s spread; the broker will likely reject it. "
                        "Raise the SL_PIPS_* setting for this symbol.", symbol, round(sl, info.digits),
                        round(abs(price - sl), info.digits), round(spread, info.digits))

        total_volume = self._volume(info, price, sl)
        per_trade = self._round_volume(info, total_volume / len(tps))
        if per_trade == 0:
            # Not enough volume to split: put everything on the first TP.
            tps, per_trade = tps[:1], self._round_volume(info, total_volume)
        if per_trade == 0:
            log.error("Volume %.4f is below the broker minimum %.2f for %s; check LOT_SIZE / RISK_PERCENT",
                      total_volume, info.volume_min, symbol)
            return []

        action, order_type = self._order_type(sig)
        results = []
        for tp in tps:
            request = {
                "action": action,
                "symbol": symbol,
                "volume": per_trade,
                "type": order_type,
                "price": self._round_price(info, price),
                "deviation": cfg.max_slippage_points,
                "magic": MAGIC,
                "comment": COMMENT,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(info),
            }
            if sl:
                request["sl"] = self._round_price(info, sl)
            if tp:
                request["tp"] = self._round_price(info, tp)

            if cfg.dry_run:
                log.info("[DRY RUN] would send: %s", request)
                results.append(request)
                continue

            result = mt5.order_send(request)
            if result is None:
                log.error("order_send failed: %s", mt5.last_error())
            elif result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                log.error("Order rejected (%s): %s | request=%s", result.retcode, result.comment, request)
            else:
                log.info("Order placed: ticket=%s %s %s %.2f @ %s sl=%s tp=%s",
                         result.order, sig.side, symbol, per_trade, result.price or price, sl, tp)
            results.append(result)
        return results

    # ---- helpers ----------------------------------------------------------

    def _resolve_symbol(self, base: str) -> str | None:
        mt5 = self.mt5
        candidates = [base + self.cfg.symbol_suffix, base]
        # Some brokers call gold "GOLD" instead of "XAUUSD".
        if base == "XAUUSD":
            candidates += ["GOLD" + self.cfg.symbol_suffix, "GOLD"]
        for name in candidates:
            if mt5.symbol_info(name) is not None:
                mt5.symbol_select(name, True)
                return name
        return None

    def _market_still_valid(self, sig: Signal, price: float) -> bool:
        """Skip a market signal if price already ran through SL or the first TP."""
        buy = sig.side == "BUY"
        if sig.sl is not None and (price <= sig.sl if buy else price >= sig.sl):
            log.warning("Skipping %s %s: price %s is already past SL %s", sig.side, sig.symbol, price, sig.sl)
            return False
        if sig.tps and (price >= sig.tps[0] if buy else price <= sig.tps[0]):
            log.warning("Skipping %s %s: price %s is already past TP1 %s", sig.side, sig.symbol, price, sig.tps[0])
            return False
        return True

    def _stop_loss(self, sig: Signal, buy: bool, price: float, info) -> float | None:
        """SL_MODE=fixed: always our pips; missing: ours only if the signal has none; signal: theirs only."""
        cfg = self.cfg
        pips = cfg.sl_pips.get(sl_category(sig.symbol), 0)
        use_ours = pips > 0 and (cfg.sl_mode == "fixed" or (cfg.sl_mode == "missing" and sig.sl is None))
        if not use_ours:
            return sig.sl
        # 1 pip = PIP_POINTS points for every symbol (10 by default, like EURUSD: 0.00010).
        dist = pips * cfg.pip_points * info.point
        sl = price - dist if buy else price + dist
        log.info("%s SL: %g pips = %s price distance -> SL %s (channel SL was %s)",
                 sig.symbol, pips, round(dist, info.digits), round(sl, info.digits), sig.sl)
        return sl

    def _order_type(self, sig: Signal):
        mt5 = self.mt5
        if sig.order_type == "MARKET":
            return mt5.TRADE_ACTION_DEAL, mt5.ORDER_TYPE_BUY if sig.side == "BUY" else mt5.ORDER_TYPE_SELL
        table = {
            ("BUY", "LIMIT"): mt5.ORDER_TYPE_BUY_LIMIT,
            ("SELL", "LIMIT"): mt5.ORDER_TYPE_SELL_LIMIT,
            ("BUY", "STOP"): mt5.ORDER_TYPE_BUY_STOP,
            ("SELL", "STOP"): mt5.ORDER_TYPE_SELL_STOP,
        }
        return mt5.TRADE_ACTION_PENDING, table[(sig.side, sig.order_type)]

    def _volume(self, info, price: float, sl: float | None) -> float:
        cfg = self.cfg
        if cfg.risk_percent <= 0 or not sl:
            return cfg.lot_size
        balance = self.mt5.account_info().balance
        risk_money = balance * cfg.risk_percent / 100
        ticks = abs(price - sl) / info.trade_tick_size
        loss_per_lot = ticks * info.trade_tick_value
        if loss_per_lot <= 0:
            return cfg.lot_size
        return min(risk_money / loss_per_lot, cfg.max_lot)

    @staticmethod
    def _round_volume(info, volume: float) -> float:
        step = info.volume_step
        vol = math.floor(volume / step + 1e-9) * step
        vol = min(vol, info.volume_max)
        return round(vol, 8) if vol >= info.volume_min else 0.0

    @staticmethod
    def _round_price(info, price: float) -> float:
        return round(price, info.digits)

    def _filling(self, info) -> int:
        mt5 = self.mt5
        # symbol_info.filling_mode is a bitmask: 1 = FOK allowed, 2 = IOC allowed.
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN
