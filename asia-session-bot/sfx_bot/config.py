"""Strategy settings. Defaults follow the SFX "PO3 Asia Session" thread."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyConfig:
    # Time zone the session times are expressed in. The PDF uses UTC-4 (New York
    # time in summer); America/New_York also handles the winter UTC-5 switch.
    timezone: str = "America/New_York"

    # STEP ONE: 20:00 is the anchor used to mark liquidity on the 15M chart.
    anchor_time: str = "20:00"
    # STEP THREE: the purge has to happen between 20:00 and 22:00, else invalid.
    purge_window_end: str = "22:00"

    # Higher timeframe used to mark buyside / sellside liquidity (minutes).
    htf_minutes: int = 15
    # Bars on each side for a 15M swing high/low to count as liquidity.
    htf_swing_strength: int = 2
    # How many 15M bars before 20:00 to search for liquidity (96 = one day).
    htf_lookback_bars: int = 96

    # Lower timeframe for MSS + FVG after the purge: 1 or 3 minutes.
    ltf_minutes: int = 3
    # Bars on each side for a LTF swing point used as the MSS level.
    ltf_swing_strength: int = 1
    # Ignore gaps smaller than this (price units). 0 accepts any gap.
    min_fvg_size: float = 0.0

    # STEP FOUR entry: "edge" enters at the first touch of the FVG,
    # "mid" waits for its 50% (consequent encroachment).
    entry_mode: str = "edge"
    # "fvg" puts the SL at the extreme of the three candles that formed the
    # FVG (what the thread says); "swing" puts it at the purge extreme.
    sl_mode: str = "fvg"
    # Extra distance added beyond the SL level (price units).
    sl_buffer: float = 0.0
    # Reward-to-risk target.
    rr: float = 2.0
    # "fixed": exit at 2R.
    # "be_runner": at 2R move SL to break even and ride to the opposite 15M
    # liquidity, like the thread's example on page 8.
    management: str = "fixed"

    # Hours after 20:00 during which a setup may still trigger an entry.
    entry_cutoff_hours: float = 4.0
    # Hours after 20:00 at which any open trade is closed at market.
    max_trade_hours: float = 11.0
    # Cancel a pending entry if price reaches the target before filling.
    cancel_if_target_hit_first: bool = True
