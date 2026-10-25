"""Upstox REST client (API v2).

Uses the same endpoints as nifty-bb-ema-ml/src/upstox_feed.py, which already
runs against a live token, plus the option-contract list:

    GET /v2/historical-candle/intraday/{key}/1minute
    GET /v2/historical-candle/{key}/1minute/{to}/{from}
    GET /v2/option/contract?instrument_key=...[&expiry_date=YYYY-MM-DD]

Token: UPSTOX_ACCESS_TOKEN in .env (it expires daily; regenerate each morning).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests

BASE_URL = "https://api.upstox.com/v2"
NIFTY = "NSE_INDEX|Nifty 50"


class UpstoxAuthError(RuntimeError):
    pass


def parse_candles(rows: list) -> pd.DataFrame:
    """Upstox rows [timestamp, open, high, low, close, volume, oi] -> 1-min frame.

    Index is the candle start in IST without tz; newest-first input is sorted.
    """
    cols = ["open", "high", "low", "close"]
    if not rows:
        return pd.DataFrame(columns=cols, dtype=float, index=pd.DatetimeIndex([]))
    df = pd.DataFrame([r[:5] for r in rows], columns=["timestamp", *cols])
    ts = pd.to_datetime(df.pop("timestamp"), utc=True)
    df.index = pd.DatetimeIndex(ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    df = df.astype(float).sort_index()
    return df[~df.index.duplicated(keep="last")]


class UpstoxClient:
    def __init__(self, token: str | None = None, session=None, timeout: float = 10):
        self.token = token or os.environ.get("UPSTOX_ACCESS_TOKEN")
        if not self.token:
            raise UpstoxAuthError("UPSTOX_ACCESS_TOKEN is not set (put it in .env)")
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        r = self.session.get(
            BASE_URL + path,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            params=params,
            timeout=self.timeout,
        )
        if r.status_code == 401:
            raise UpstoxAuthError("Upstox rejected the token (401): generate today's access token")
        if r.status_code != 200:
            raise RuntimeError(f"Upstox {path} -> HTTP {r.status_code}: {r.text[:200]}")
        return r.json().get("data")

    def intraday(self, key: str) -> pd.DataFrame:
        data = self._get(f"/historical-candle/intraday/{quote(key, safe='')}/1minute")
        return parse_candles((data or {}).get("candles", []))

    def history(self, key: str, day: date) -> pd.DataFrame:
        d = day.isoformat()
        data = self._get(f"/historical-candle/{quote(key, safe='')}/1minute/{d}/{d}")
        return parse_candles((data or {}).get("candles", []))

    def option_contracts(self, underlying: str = NIFTY) -> pd.DataFrame:
        """All listed option contracts: expiry, strike, type (CE/PE), key, symbol."""
        rows = self._get("/option/contract", {"instrument_key": underlying}) or []
        df = pd.DataFrame(rows)
        if df.empty:
            raise RuntimeError(f"no option contracts returned for {underlying}")
        return pd.DataFrame(
            {
                "expiry": pd.to_datetime(df["expiry"]).dt.tz_localize(None).dt.normalize(),
                "strike": df["strike_price"].astype(float),
                "type": df["instrument_type"],
                "key": df["instrument_key"],
                "symbol": df["trading_symbol"],
            }
        )

    def previous_trading_day(self, today: date, key: str = NIFTY, lookback: int = 10) -> date:
        """Most recent day before `today` with candles (skips weekends and holidays)."""
        d = today
        for _ in range(lookback):
            d -= timedelta(days=1)
            if d.weekday() < 5 and not self.history(key, d).empty:
                return d
        raise RuntimeError(f"no trading day found in the {lookback} days before {today}")
