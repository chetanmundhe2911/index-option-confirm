"""A_clean signal detector, shared by the index and its options (spec §3).

Indicators on 5-min closes:
    Bollinger Bands (20, 2) using population std (ddof=0)
    EMA9 = ewm(span=9, adjust=False)
Feed the previous day's candles together with today's so the indicators are
warmed up; the arm state still resets at every day change.

BUY  (index BUY, or any option premium - we only buy options)
    arm     : low <= lower BB
    signal i: while armed, first candle with close > EMA9, then disarm
    A_clean : close[i+1] > EMA9[i+1] and low[i+1] > low[i]
SELL (index only; option side is the ATM PE)
    arm     : high >= upper BB
    signal i: while armed, first candle with close < EMA9, then disarm
    A_clean : close[i+1] < EMA9[i+1] and high[i+1] < high[i]

Only signal candles labelled 09:30-15:00 IST count. A_clean is known only when
candle i+1 has closed, and every decision uses closed candles only: pass in
closed candles, never a forming one.

Everything here is a pure function of the candles passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

import pandas as pd

BB_PERIOD = 20
BB_STD = 2.0
EMA_SPAN = 9
BAR = pd.Timedelta(minutes=5)
WINDOW = (time(9, 30), time(15, 0))

BUY = "BUY"
SELL = "SELL"

REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Signal:
    side: str
    signal_time: pd.Timestamp  # label (start) of signal candle i
    entry: float  # close[i]
    stop: float  # low[i] on BUY, high[i] on SELL
    a_clean: bool | None  # None while candle i+1 has not closed yet
    known_at: pd.Timestamp | None  # close of candle i+1, when a_clean is decided

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    def targets(self, n: int = 3) -> tuple[float, ...]:
        """1R..nR target levels (spec §3: R = entry - stop)."""
        sign = 1.0 if self.side == BUY else -1.0
        return tuple(self.entry + sign * k * self.risk for k in range(1, n + 1))


def add_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of `candles` with bb_mid, bb_upper, bb_lower and ema9."""
    _validate(candles)
    out = candles.copy()
    close = out["close"].astype(float)
    mid = close.rolling(BB_PERIOD).mean()
    std = close.rolling(BB_PERIOD).std(ddof=0)
    out["bb_mid"] = mid
    out["bb_upper"] = mid + BB_STD * std
    out["bb_lower"] = mid - BB_STD * std
    out["ema9"] = close.ewm(span=EMA_SPAN, adjust=False).mean()
    return out


def scan(
    candles: pd.DataFrame,
    side: str,
    *,
    window: tuple[time, time] | None = WINDOW,
    bar: pd.Timedelta = BAR,
    arm_bar_can_signal: bool = True,
) -> list[Signal]:
    """Every signal candle for `side`, A_clean or not.

    `candles` is a DatetimeIndex-ed frame of closed bars (label = bar start,
    IST) with open/high/low/close, previous day included for warm-up.
    `arm_bar_can_signal` lets the candle that arms also be the signal candle.
    """
    if side not in (BUY, SELL):
        raise ValueError(f"side must be {BUY!r} or {SELL!r}, got {side!r}")
    df = add_indicators(candles)

    idx = df.index
    days = idx.normalize()
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    ema = df["ema9"].to_numpy(float)
    if side == BUY:
        arms = low <= df["bb_lower"].to_numpy(float)
        fires = close > ema
    else:
        arms = high >= df["bb_upper"].to_numpy(float)
        fires = close < ema

    signals = []
    armed = False
    for i in range(len(df)):
        if i == 0 or days[i] != days[i - 1]:
            armed = False
        was_armed = armed
        if arms[i]:
            armed = True
        if not (was_armed or arm_bar_can_signal):
            continue
        if not (armed and fires[i]):
            continue
        armed = False
        signals.append(_signal_at(i, side, idx, days, high, low, close, ema, bar))

    if window is not None:
        start, end = window
        signals = [s for s in signals if start <= s.signal_time.time() <= end]
    return signals


def a_clean_signals(candles: pd.DataFrame, side: str, **kwargs) -> list[Signal]:
    """Only the confirmed A_clean signals; see `scan` for arguments."""
    return [s for s in scan(candles, side, **kwargs) if s.a_clean]


def _signal_at(i, side, idx, days, high, low, close, ema, bar) -> Signal:
    j = i + 1
    if j >= len(idx):
        a_clean, known_at = None, None
    elif days[j] != days[i]:
        # Candle i+1 belongs to the next session; the signal cannot confirm.
        a_clean, known_at = False, idx[i] + bar
    elif side == BUY:
        a_clean = bool(close[j] > ema[j] and low[j] > low[i])
        known_at = idx[j] + bar
    else:
        a_clean = bool(close[j] < ema[j] and high[j] < high[i])
        known_at = idx[j] + bar
    return Signal(
        side=side,
        signal_time=idx[i],
        entry=float(close[i]),
        stop=float(low[i] if side == BUY else high[i]),
        a_clean=a_clean,
        known_at=known_at,
    )


def _validate(candles: pd.DataFrame) -> None:
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise TypeError("candles must have a DatetimeIndex")
    missing = [c for c in REQUIRED_COLUMNS if c not in candles.columns]
    if missing:
        raise ValueError(f"candles missing columns: {missing}")
    if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
        raise ValueError("candles index must be strictly increasing")
