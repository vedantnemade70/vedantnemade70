"""Settings, read from a .env file next to this script (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
load_dotenv(ENV_FILE)


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    # Drop notes pasted from the setup guide ("12345 <- your api_id") and stray quotes/spaces.
    return value.split("\u2190")[0].split("<-")[0].strip().strip("'\"").strip()


def _get(name: str) -> str:
    value = _env(name)
    if not value:
        if not ENV_FILE.exists():
            hint = (" Found .env.txt: rename it to .env (Notepad added .txt)."
                    if (HERE / ".env.txt").exists() else " Run: copy .env.example .env")
            raise SystemExit(f"No .env file in {HERE}.{hint}")
        raise SystemExit(f"Missing setting {name} in .env (see .env.example)")
    return value


def _num(name: str, default: str | None = None, kind=float):
    raw = _get(name) if default is None else (_env(name, default) or default)
    try:
        return kind(raw)
    except ValueError:
        raise SystemExit(f"{name} in .env must be a number, got {raw!r}") from None


def _bool(name: str, default: str) -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    tg_api_id: int
    tg_api_hash: str
    tg_phone: str
    tg_channels: list[str | int]
    tg_session: str

    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_path: str

    lot_size: float
    risk_percent: float
    max_lot: float
    max_tps: int
    sl_mode: str
    sl_pips: dict
    pip_points: int
    max_slippage_points: int
    symbol_suffix: str
    max_signal_age_sec: int
    dry_run: bool


def _channel(value: str) -> str | int:
    value = value.strip()
    return int(value) if value.lstrip("-").isdigit() else value


def load_config(need_mt5: bool = True) -> Config:
    return Config(
        tg_api_id=_num("TG_API_ID", kind=int),
        tg_api_hash=_get("TG_API_HASH"),
        tg_phone=_get("TG_PHONE"),
        tg_channels=[_channel(c) for c in _env("TG_CHANNELS").split(",") if c.strip()],
        tg_session=str(HERE / (_env("TG_SESSION") or "copier")),
        # Login/password/server are optional: left empty, the bot uses the account that is
        # already logged in (password saved) in the MT5_PATH terminal.
        mt5_login=_num("MT5_LOGIN", "0", int) if need_mt5 else 0,
        mt5_password=(os.getenv("MT5_PASSWORD") or "").strip() if need_mt5 else "",
        mt5_server=_env("MT5_SERVER") if need_mt5 else "",
        mt5_path=_get("MT5_PATH") if need_mt5 else "",
        lot_size=_num("LOT_SIZE", "0.01"),
        risk_percent=_num("RISK_PERCENT", "0"),
        max_lot=_num("MAX_LOT", "1.0"),
        max_tps=_num("MAX_TPS", "3", int),
        sl_mode=_env("SL_MODE", "fixed").strip().lower(),
        sl_pips={
            "forex": _num("SL_PIPS_FOREX", "20"),
            "metal": _num("SL_PIPS_METAL", "30"),
            "oil": _num("SL_PIPS_OIL", "30"),
            "crypto": _num("SL_PIPS_CRYPTO", "30"),
            "index": _num("SL_PIPS_INDEX", "0"),
        },
        pip_points=_num("PIP_POINTS", "10", int),
        max_slippage_points=_num("MAX_SLIPPAGE_POINTS", "30", int),
        symbol_suffix=_env("SYMBOL_SUFFIX", ""),
        max_signal_age_sec=_num("MAX_SIGNAL_AGE_SEC", "120", int),
        dry_run=_bool("DRY_RUN", "true"),
    )
