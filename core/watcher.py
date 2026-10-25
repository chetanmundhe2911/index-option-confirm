"""Bar-by-bar A_clean watcher: feed closed 5-min candles, get new alerts.

Used by the replay script now and by the live feed later. It keeps only the
previous session and today, reruns the pure detector on every closed bar, and
reports each A_clean signal once, at the close of the bar that confirms it.
"""

from __future__ import annotations

import pandas as pd

from core.aclean import BUY, SELL, Signal, scan


class SignalWatcher:
    def __init__(self, sides=(BUY, SELL), **scan_kwargs):
        self.sides = tuple(sides)
        self.scan_kwargs = scan_kwargs
        self.candles = pd.DataFrame(columns=["open", "high", "low", "close"], dtype=float)
        self._seen: set[tuple[str, pd.Timestamp]] = set()

    def warm_up(self, candles: pd.DataFrame) -> None:
        """Load history without alerting; signals already decided stay silent.

        Lets a mid-day restart re-warm from history without duplicate alerts.
        """
        self.candles = candles[["open", "high", "low", "close"]].astype(float)
        self._trim()
        for side in self.sides:
            for s in scan(self.candles, side, **self.scan_kwargs):
                if s.a_clean is not None:
                    self._seen.add((s.side, s.signal_time))

    def on_bar(self, ts, open_, high, low, close) -> list[Signal]:
        """Add one closed candle (labelled by its start time); return new A_clean signals."""
        ts = pd.Timestamp(ts)
        if len(self.candles) and ts <= self.candles.index[-1]:
            raise ValueError(f"bar {ts} is not after {self.candles.index[-1]}")
        self.candles.loc[ts] = [open_, high, low, close]
        self._trim()

        alerts = []
        for side in self.sides:
            for s in scan(self.candles, side, **self.scan_kwargs):
                key = (s.side, s.signal_time)
                if s.a_clean is None or key in self._seen:
                    continue
                self._seen.add(key)
                if s.a_clean:
                    alerts.append(s)
        return alerts

    def _trim(self) -> None:
        days = self.candles.index.normalize().unique()
        if len(days) > 2:
            self.candles = self.candles[self.candles.index >= days[-2]]
