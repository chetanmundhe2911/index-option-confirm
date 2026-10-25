"""Replay NIFTY 5-min candles bar by bar, printing index A_clean alerts as they
would have fired live, plus how each trade played out.

    python replay.py                      # last ~60 days from yfinance (^NSEI)
    python replay.py --days 5             # only the last 5 sessions
    python replay.py --csv data/nifty.csv # your own candles
    python replay.py --save data/nifty_5m.csv

CSV columns: a timestamp column (datetime / date / timestamp, bar start, IST)
and open, high, low, close.

Outcomes follow the spec's levels (entry = signal close, stop = signal low or
high, targets 1R/2R/3R) and are walked from the bar after the alert to the
end of that session. If a bar touches both the stop and a target, the stop
counts first.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import time

import pandas as pd

from core.aclean import BUY, Signal
from core.watcher import SignalWatcher

SESSION = (time(9, 15), time(15, 25))


def load_yfinance(symbol: str = "^NSEI") -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(symbol, period="60d", interval="5m", progress=False, auto_adjust=False)
    if raw.empty:
        raise SystemExit(f"yfinance returned no data for {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close"]]
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert("Asia/Kolkata").tz_localize(None)
    return clean(df)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = next(c for c in ("datetime", "timestamp", "date", "time") if c in df.columns)
    df.index = pd.to_datetime(df.pop(ts_col))
    if df.index.tz is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    return clean(df[["open", "high", "low", "close"]])


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna().astype(float).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    t = df.index.time
    return df[(t >= SESSION[0]) & (t <= SESSION[1])]


def outcome(sig: Signal, after: pd.DataFrame) -> str:
    """Highest target hit before the stop: 'T3', 'T2', 'T1', 'STOP' or 'OPEN'."""
    t1, t2, t3 = sig.targets()
    best = "OPEN"
    for _, bar in after.iterrows():
        if sig.side == BUY:
            stopped = bar["low"] <= sig.stop
            reached = [t for t, lvl in (("T1", t1), ("T2", t2), ("T3", t3)) if bar["high"] >= lvl]
        else:
            stopped = bar["high"] >= sig.stop
            reached = [t for t, lvl in (("T1", t1), ("T2", t2), ("T3", t3)) if bar["low"] <= lvl]
        if stopped:
            return best if best != "OPEN" else "STOP"
        if reached:
            best = reached[-1]
            if best == "T3":
                return best
    return best


def alert_text(sig: Signal) -> str:
    t1, t2, t3 = sig.targets()
    plan = "1:3" if sig.signal_time.time() >= time(12, 0) else "1:2"
    return (
        f"{sig.side:<4} {sig.known_at:%Y-%m-%d %H:%M}  "
        f"signal {sig.signal_time:%H:%M}  entry {sig.entry:,.2f}  stop {sig.stop:,.2f} "
        f"(R {sig.risk:.1f})  T1 {t1:,.1f}  T2 {t2:,.1f}  T3 {t3:,.1f}  plan {plan}"
    )


def replay(candles: pd.DataFrame, days: int | None = None) -> list[tuple[Signal, str]]:
    sessions = candles.index.normalize().unique()
    if len(sessions) < 2:
        raise SystemExit("need at least two sessions (one for warm-up)")
    first = sessions[1] if days is None else sessions[max(1, len(sessions) - days)]
    warm = candles[(candles.index < first) & (candles.index >= sessions[sessions.get_loc(first) - 1])]

    watcher = SignalWatcher()
    watcher.warm_up(warm)
    results = []
    live = candles[candles.index >= first]
    for ts, bar in live.iterrows():
        for sig in watcher.on_bar(ts, bar["open"], bar["high"], bar["low"], bar["close"]):
            day_end = sig.signal_time.normalize() + pd.Timedelta(days=1)
            after = live[(live.index > ts) & (live.index < day_end)]
            res = outcome(sig, after)
            results.append((sig, res))
            print(f"{alert_text(sig)}  ->  {res}")
    return results


def summary(results: list[tuple[Signal, str]], n_days: int) -> None:
    print(f"\n{len(results)} A_clean alerts over {n_days} sessions")
    rank = {"STOP": 0, "OPEN": 0, "T1": 1, "T2": 2, "T3": 3}
    for side in ("BUY", "SELL"):
        rs = [r for s, r in results if s.side == side]
        if not rs:
            continue
        decided = [r for r in rs if r != "OPEN"]
        line = f"  {side:<4} {len(rs):3d} trades"
        for k in (1, 2, 3):
            hit = sum(rank[r] >= k for r in decided)
            line += f" | 1:{k} {hit / len(decided):5.1%}" if decided else ""
        line += f" | open at close {rs.count('OPEN')}"
        print(line)
    print("  outcomes:", dict(Counter(r for _, r in results)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="read candles from this CSV instead of yfinance")
    ap.add_argument("--symbol", default="^NSEI", help="yfinance symbol (default ^NSEI)")
    ap.add_argument("--days", type=int, help="replay only the last N sessions")
    ap.add_argument("--save", help="also write the loaded candles to this CSV")
    args = ap.parse_args()

    candles = load_csv(args.csv) if args.csv else load_yfinance(args.symbol)
    if args.save:
        candles.rename_axis("datetime").to_csv(args.save)
    sessions = candles.index.normalize().unique()
    n_days = len(sessions) - 1 if args.days is None else min(args.days, len(sessions) - 1)
    print(f"{len(candles)} candles, {sessions[0]:%Y-%m-%d} .. {sessions[-1]:%Y-%m-%d}; replaying {n_days} sessions\n")
    summary(replay(candles, args.days), n_days)


if __name__ == "__main__":
    main()
