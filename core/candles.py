"""1-min candles -> closed 5-min candles (label = bar start, left-closed)."""

from __future__ import annotations

from datetime import time

import pandas as pd

BAR = pd.Timedelta(minutes=5)
MINUTE = pd.Timedelta(minutes=1)
# If a bar's last minute never arrives, accept the bar this long after its close.
GRACE = pd.Timedelta(minutes=2)
SESSION = (time(9, 15), time(15, 25))


def to_5min(one_min: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Closed 5-min bars as of `now`.

    A bar counts as closed once `now` is past its end and its last 1-min
    candle (start + 4 min) has arrived, or GRACE after its end regardless.
    A forming bar is never returned.
    """
    cols = ["open", "high", "low", "close"]
    if one_min.empty:
        return pd.DataFrame(columns=cols, dtype=float, index=pd.DatetimeIndex([]))
    grouped = one_min[cols].resample("5min", label="left", closed="left")
    bars = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    last_minute = pd.Series(one_min.index, index=one_min.index).resample(
        "5min", label="left", closed="left"
    ).max().reindex(bars.index)

    end = bars.index + BAR
    complete = (last_minute == bars.index + 4 * MINUTE).to_numpy()
    closed = (end <= now) & (complete | (end + GRACE <= now))
    t = bars.index.time
    in_session = (t >= SESSION[0]) & (t <= SESSION[1])
    return bars[closed & in_session]
