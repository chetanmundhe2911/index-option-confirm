"""Live paper test: watch NIFTY 5-min candles during market hours and alert on
index A_clean signals. Uses yfinance (no broker login), index only - no option
check yet.

    python live_yf.py                  # run for the day; stops after 15:30 IST
    python live_yf.py --test-telegram  # send one test message and exit

Telegram is optional: put TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.
Without them alerts only print in the terminal. Every alert is also appended
to logs/live_alerts.csv.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time as _time
from datetime import time
from pathlib import Path

import pandas as pd

from core.aclean import Signal, add_indicators
from core.watcher import SignalWatcher
from notify.telegram import load_env, send_telegram
from replay import load_yfinance

BAR = pd.Timedelta(minutes=5)
LAG = pd.Timedelta(seconds=30)  # yfinance publishes a closed bar a little late
RETRIES = 6  # extra 30 s polls when the new bar has not shown up yet
STOP_AT = time(15, 31)
LOG = Path("logs/live_alerts.csv")


def now_ist() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)


def closed_bars(candles: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Drop the candle still forming at `now` (labels are bar start times).

    yfinance sometimes adds a live row stamped off the 5-min grid; drop it too.
    """
    idx = candles.index
    return candles[(idx + BAR <= now) & (idx == idx.floor("5min"))]


def alert_message(sig: Signal) -> str:
    t1, t2, t3 = sig.targets()
    plan = "1:3 (afternoon)" if sig.signal_time.time() >= time(12, 0) else "1:2"
    option = "CE" if sig.side == "BUY" else "PE"
    return (
        f"🔔 NIFTY A_clean {sig.side} · 5-min · {sig.known_at:%H:%M}\n"
        f"Signal candle {sig.signal_time:%H:%M}, confirmed {sig.known_at - BAR:%H:%M} candle\n"
        f"Index entry {sig.entry:,.2f} · stop {sig.stop:,.2f} (R {sig.risk:.1f})\n"
        f"T1 {t1:,.1f} · T2 {t2:,.1f} · T3 {t3:,.1f} · plan {plan}\n"
        f"Next: check ATM {option} yourself (option check not automated yet)"
    )


def log_alert(sig: Signal, path: Path = LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["alert_time", "side", "signal_time", "entry", "stop", "t1", "t2", "t3"])
        w.writerow([sig.known_at, sig.side, sig.signal_time, sig.entry, sig.stop, *sig.targets()])


class LiveRunner:
    def __init__(self, fetch, notify=send_telegram, log=log_alert):
        self.fetch = fetch
        self.notify = notify
        self.log = log
        self.watcher = SignalWatcher()
        self.alerts: list[Signal] = []

    def start(self, now: pd.Timestamp) -> pd.Timestamp | None:
        """Warm up from history; signals already decided before now stay silent."""
        self.watcher.warm_up(closed_bars(self.fetch(), now))
        return self.last_bar

    @property
    def last_bar(self) -> pd.Timestamp | None:
        return self.watcher.candles.index[-1] if len(self.watcher.candles) else None

    def poll(self, now: pd.Timestamp) -> int:
        """Process closed bars newer than the last one seen; return how many."""
        bars = closed_bars(self.fetch(), now)
        if self.last_bar is not None:
            bars = bars[bars.index > self.last_bar]
        for ts, b in bars.iterrows():
            new = self.watcher.on_bar(ts, b["open"], b["high"], b["low"], b["close"])
            ind = add_indicators(self.watcher.candles).iloc[-1]
            print(
                f"{ts:%H:%M} candle  close {b['close']:,.2f}  EMA9 {ind['ema9']:,.2f}  "
                f"BB {ind['bb_lower']:,.2f} / {ind['bb_upper']:,.2f}"
                + ("" if new else "  · no signal")
            )
            for sig in new:
                msg = alert_message(sig)
                print("\n" + msg + "\n")
                self.notify(msg)
                self.log(sig)
                self.alerts.append(sig)
        return len(bars)


def sleep_until(ts: pd.Timestamp) -> None:
    wait = (ts - now_ist()).total_seconds()
    if wait > 0:
        _time.sleep(wait)


def run(symbol: str) -> None:
    runner = LiveRunner(lambda: load_yfinance(symbol, period="5d"))
    last = runner.start(now_ist())
    last_txt = f"{last:%d %b %H:%M}" if last is not None else "none"
    hello = f"NIFTY A_clean watcher started {now_ist():%d %b %H:%M} IST · last closed candle {last_txt}"
    print(hello)
    send_telegram("✅ " + hello)

    while now_ist().time() < STOP_AT:
        now = now_ist()
        sleep_until(now.floor("5min") + BAR + LAG)
        for attempt in range(RETRIES + 1):
            try:
                if runner.poll(now_ist()):
                    break
            except (Exception, SystemExit) as e:  # network hiccup: keep going
                print(f"  ! fetch failed: {e}")
            in_session = time(9, 20) <= now_ist().time() < STOP_AT
            if not in_session:
                break
            _time.sleep(30)
        else:
            print(f"  ! no new candle from yfinance after {RETRIES * 30}s, will catch up next round")

    bye = f"Market closed. {len(runner.alerts)} A_clean alert(s) today."
    for s in runner.alerts:
        bye += f"\n  {s.known_at:%H:%M} {s.side} entry {s.entry:,.2f} stop {s.stop:,.2f}"
    print("\n" + bye)
    send_telegram("🏁 " + bye)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="^NSEI")
    ap.add_argument("--test-telegram", action="store_true", help="send a test message and exit")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji on Windows consoles
    load_env()
    if args.test_telegram:
        ok = send_telegram("✅ index-option-confirm: Telegram works")
        print("sent" if ok else "not sent: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return
    run(args.symbol)


if __name__ == "__main__":
    main()
