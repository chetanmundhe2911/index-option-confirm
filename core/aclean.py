# Purpose: Shared BUY/SELL A_clean detector on closed 5-min candles (pure functions, no I/O)
from dataclasses import dataclass
from datetime import time
from typing import Optional

import numpy as np
import pandas as pd

BB_LEN = 20       # Bollinger period
BB_K = 2.0        # Bollinger width in std devs
EMA_SPAN = 9      # EMA9
WINDOW_START = time(9, 30)   # signals allowed from ...
WINDOW_END = time(15, 0)     # ... to (inclusive)

PENDING, A_CLEAN, OTHER = "PENDING", "A_CLEAN", "OTHER"


@dataclass(frozen=True)
class Signal:
    side: str                              # "BUY" | "SELL"
    signal_time: pd.Timestamp              # candle i
    grade: str                             # PENDING | A_CLEAN | OTHER
    confirm_time: Optional[pd.Timestamp]   # candle i+1, None while PENDING
    close: float
    low: float
    high: float


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with bb_mid, bb_up, bb_lo, ema9 added.

    df: DatetimeIndex, columns open/high/low/close, closed candles only,
    previous day + today (previous day warms up the indicators).
    """
    out = df.copy()
    c = out["close"]
    mid = c.rolling(BB_LEN).mean()
    sd = c.rolling(BB_LEN).std(ddof=0)          # population std, as in the backtest
    out["bb_mid"] = mid
    out["bb_up"] = mid + BB_K * sd
    out["bb_lo"] = mid - BB_K * sd
    out["ema9"] = c.ewm(span=EMA_SPAN, adjust=False).mean()
    return out


def _in_window(t: pd.Timestamp) -> bool:
    return WINDOW_START <= t.time() <= WINDOW_END


def scan(d: pd.DataFrame, side: str) -> list[Signal]:
    """BUY or SELL signals on a frame that already has bb_lo, bb_up, ema9."""
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    buy = side == "BUY"

    ts = d.index
    h, l, c = (d[k].to_numpy(float) for k in ("high", "low", "close"))
    band = d["bb_lo" if buy else "bb_up"].to_numpy(float)
    ema = d["ema9"].to_numpy(float)
    n = len(d)

    signals: list[Signal] = []
    armed = False
    prev_day = None

    for i in range(n):
        t = ts[i]

        # 1. New day: reset the latch (indicators keep running from warm-up)
        if t.date() != prev_day:
            armed = False
            prev_day = t.date()

        # 2. Outside 09:30-15:00: cannot arm, and any arm is dropped
        if not _in_window(t):
            armed = False
            continue

        # 3. No indicator yet (first 19 bars): skip
        if np.isnan(band[i]) or np.isnan(ema[i]):
            continue

        # 4. Arm: BUY touches lower band, SELL touches upper band
        if (l[i] <= band[i]) if buy else (h[i] >= band[i]):
            armed = True

        # 5. Signal: first close beyond EMA9 while armed (can be the arming bar)
        trig = (c[i] > ema[i]) if buy else (c[i] < ema[i])
        if not (armed and trig):
            continue
        armed = False

        # 6. Zero risk cannot be traded: skip (BUY close == low, SELL close == high)
        if ((c[i] - l[i]) if buy else (h[i] - c[i])) <= 0:
            continue

        # 7. Grade with bar i+1
        j = i + 1
        if j >= n:
            grade, ctime = PENDING, None           # i+1 not closed yet (live)
        elif ts[j].date() != t.date():
            grade, ctime = OTHER, None             # i+1 is next day
        else:
            ok = (c[j] > ema[j] and l[j] > l[i]) if buy else (c[j] < ema[j] and h[j] < h[i])
            grade, ctime = (A_CLEAN if ok else OTHER), ts[j]

        signals.append(Signal(side, t, grade, ctime, c[i], l[i], h[i]))

    return signals


def scan_buy(d: pd.DataFrame) -> list[Signal]:
    """Kept so existing callers and tests keep working."""
    return scan(d, "BUY")
