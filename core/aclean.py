# Purpose: Shared BUY/SELL A_clean detector on closed 5-min candles (pure functions, no I/O)
import pandas as pd

BB_LEN = 20       # Bollinger period
BB_K = 2.0        # Bollinger width in std devs
EMA_SPAN = 9      # EMA9


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
