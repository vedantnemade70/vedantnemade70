"""Settings, read from a .env file next to this script (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")


def _get(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing setting {name} in .env (see .env.example)")
    return value


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


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
    default_sl_points: int
    max_slippage_points: int
    symbol_suffix: str
    max_signal_age_sec: int
    dry_run: bool


def _channel(value: str) -> str | int:
    value = value.strip()
    return int(value) if value.lstrip("-").isdigit() else value


def load_config(need_mt5: bool = True) -> Config:
    return Config(
        tg_api_id=int(_get("TG_API_ID")),
        tg_api_hash=_get("TG_API_HASH"),
        tg_phone=_get("TG_PHONE"),
        tg_channels=[_channel(c) for c in os.getenv("TG_CHANNELS", "").split(",") if c.strip()],
        tg_session=str(HERE / os.getenv("TG_SESSION", "copier")),
        mt5_login=int(_get("MT5_LOGIN")) if need_mt5 else 0,
        mt5_password=_get("MT5_PASSWORD") if need_mt5 else "",
        mt5_server=_get("MT5_SERVER") if need_mt5 else "",
        mt5_path=_get("MT5_PATH") if need_mt5 else "",
        lot_size=float(os.getenv("LOT_SIZE", "0.01")),
        risk_percent=float(os.getenv("RISK_PERCENT", "0")),
        max_lot=float(os.getenv("MAX_LOT", "1.0")),
        max_tps=int(os.getenv("MAX_TPS", "3")),
        default_sl_points=int(os.getenv("DEFAULT_SL_POINTS", "0")),
        max_slippage_points=int(os.getenv("MAX_SLIPPAGE_POINTS", "30")),
        symbol_suffix=os.getenv("SYMBOL_SUFFIX", ""),
        max_signal_age_sec=int(os.getenv("MAX_SIGNAL_AGE_SEC", "120")),
        dry_run=_bool("DRY_RUN", "true"),
    )
