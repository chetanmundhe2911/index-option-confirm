"""Live NIFTY -> ATM option A_clean confirmation bot (Upstox, alerts only).

    python main.py                  # run for the day; stops after 15:30 IST
    python main.py --check          # test token, contracts and candles, then exit
    python main.py --test-telegram  # send one test message and exit

Needs UPSTOX_ACCESS_TOKEN in .env (regenerate daily). Telegram is optional:
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. Alerts are appended to
logs/alerts.csv.

Every 5-min close (+20 s) it pulls 1-min candles from Upstox for the index and
the relevant options, builds closed 5-min bars, runs the A_clean detector and
grades alerts as CONFIRMED / INDEX ONLY / OPTION ONLY (spec §4).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time as _time
from datetime import date, time
from pathlib import Path

import pandas as pd

from confirm.matcher import (
    CONFIRMED,
    INDEX_ONLY,
    OPTION_FOR,
    OPTION_ONLY,
    SIDE_FOR,
    Alert,
    grade_index_signal,
    is_option_only,
)
from core.aclean import BUY, SELL, Signal, scan
from core.candles import to_5min
from feeds.upstox import NIFTY, UpstoxAuthError, UpstoxClient
from notify.telegram import load_env, send_telegram
from options.atm import Contract, nearest_expiry, pick

BAR = pd.Timedelta(minutes=5)
LAG = pd.Timedelta(seconds=20)  # let Upstox finish the last 1-min candle
RETRIES = 6  # extra 15 s polls while the newest 5-min bar is missing
STOP_AT = time(15, 31)
MAX_FAILED_ROUNDS = 3  # then warn: feed lost
LOG = Path("logs/alerts.csv")


def now_ist() -> pd.Timestamp:
    return pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)


class Engine:
    """Stateless per step except for what was already alerted and pending."""

    def __init__(self, client, today: date):
        self.client = client
        self.today = today
        self.prev_day = client.previous_trading_day(today)
        contracts = client.option_contracts(NIFTY)
        self.expiry = nearest_expiry(contracts, today)
        self.contracts = contracts[contracts["expiry"] == self.expiry]
        self._history: dict[str, pd.DataFrame] = {}
        self.pending: dict[tuple, tuple[Signal, float, list[Contract]]] = {}
        self.done: set[tuple] = set()
        self.atm_at: dict[pd.Timestamp, list[Contract]] = {}  # ATM CE/PE at each close
        self.index_bars = pd.DataFrame()

    def bars(self, key: str, now: pd.Timestamp) -> pd.DataFrame:
        """Previous session + today's closed 5-min bars for one instrument."""
        if key not in self._history:
            self._history[key] = self.client.history(key, self.prev_day)
        one_min = pd.concat([self._history[key], self.client.intraday(key)])
        one_min = one_min[~one_min.index.duplicated(keep="last")].sort_index()
        return to_5min(one_min, now)

    def today_only(self, signals: list[Signal]) -> list[Signal]:
        return [s for s in signals if s.signal_time.date() == self.today]

    def step(self, now: pd.Timestamp) -> list[Alert]:
        idx = self.bars(NIFTY, now)
        self.index_bars = idx
        if idx.empty:
            return []
        close_now = idx.index[-1] + BAR
        index_signals = [s for side in (BUY, SELL) for s in self.today_only(scan(idx, side))]

        # New index A_clean -> pending, with ATM ±1 picked from spot at that close.
        for s in index_signals:
            key = ("IDX", s.side, s.signal_time)
            if s.a_clean and key not in self.done and key not in self.pending:
                spot = float(idx.loc[s.known_at - BAR, "close"])
                cands = pick(self.contracts, self.expiry, spot, OPTION_FOR[s.side])
                self.pending[key] = (s, spot, cands)

        # ATM CE/PE as of each close; option-only checks the last two closes so
        # a late candle is still caught, but only on the strike ATM at the time.
        spot_now = float(idx["close"].iloc[-1])
        self.atm_at[close_now] = pick(self.contracts, self.expiry, spot_now, "CE", 0) + pick(
            self.contracts, self.expiry, spot_now, "PE", 0
        )
        recent_atm = [(t, c) for t in (close_now - BAR, close_now) for c in self.atm_at.get(t, [])]

        # Options to load: pending candidates + recent ATM CE/PE.
        wanted = {c.key: c for _, _, cands in self.pending.values() for c in cands}
        wanted.update({c.key: c for _, c in recent_atm})
        option_signals = {k: self.today_only(scan(self.bars(k, now), BUY)) for k in wanted}

        alerts = []
        for key, (s, spot, cands) in list(self.pending.items()):
            a = grade_index_signal(s, spot, cands, option_signals, now)
            if a is not None:
                alerts.append(a)
                self.done.add(key)
                del self.pending[key]

        # OPTION ONLY: A_clean on the strike that was ATM when it became known.
        for t, c in recent_atm:
            for s in option_signals[c.key]:
                key = ("OPT", c.key, s.signal_time)
                if not s.a_clean or key in self.done or s.known_at != t:
                    continue
                self.done.add(key)
                if is_option_only(s, c.type, index_signals):
                    alerts.append(Alert(OPTION_ONLY, s.known_at, SIDE_FOR[c.type], contract=c, option_signal=s))

        return sorted(alerts, key=lambda a: a.at)


def plan(sig_time: pd.Timestamp) -> str:
    return "1:3 (afternoon)" if sig_time.time() >= time(12, 0) else "1:2"


def levels(s: Signal) -> str:
    t1, t2, t3 = s.targets()
    return f"Entry {s.entry:,.2f} · Stop {s.stop:,.2f} (R {s.risk:.1f}) · T1 {t1:,.1f} · T2 {t2:,.1f} · T3 {t3:,.1f}"


def format_alert(a: Alert) -> str:
    if a.grade == OPTION_ONLY:
        s, c = a.option_signal, a.contract
        weak = " · PE: weaker" if c.type == "PE" else ""
        return (
            f"🔵 OPTION ONLY · {c.symbol} · {a.at:%H:%M}\n"
            f"Option A_clean: signal {s.signal_time:%H:%M} candle · no index {a.side} signal within 10 min\n"
            f"{levels(s)}\n"
            f"Plan: 1:1 only{weak}"
        )
    i = a.index_signal
    head = (
        f"Index A_clean: signal {i.signal_time:%H:%M} candle, confirmed {i.known_at - BAR:%H:%M} candle"
        f" · spot {a.spot:,.2f}"
    )
    if a.grade == CONFIRMED:
        s, c = a.option_signal, a.contract
        lag = (s.signal_time - i.signal_time) // BAR
        when = "same bar" if lag == 0 else f"+{lag} bar{'s' if lag > 1 else ''}"
        strike = "ATM" if c.offset == 0 else f"ATM{c.offset:+d}"
        return (
            f"🟢 CONFIRMED {a.side} · NIFTY 5-min · {a.at:%H:%M}\n"
            f"{head}\n"
            f"Option: {c.symbol} ({strike}) · A_clean {when}\n"
            f"{levels(s)}\n"
            f"Plan: {plan(i.signal_time)}"
        )
    return (
        f"🟡 INDEX ONLY {a.side} · NIFTY 5-min · {a.at:%H:%M}\n"
        f"{head}\n"
        f"No ATM {OPTION_FOR[a.side]} confirmation in 10 min · skip"
    )


def log_alert(a: Alert, path: Path = LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    s = a.option_signal
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(
                ["alert_time", "grade", "side", "index_signal", "spot", "option", "option_signal", "entry", "stop", "t1", "t2", "t3"]
            )
        w.writerow(
            [
                a.at,
                a.grade,
                a.side,
                a.index_signal.signal_time if a.index_signal else "",
                a.spot or "",
                a.contract.symbol if a.contract else "",
                s.signal_time if s else "",
                *((s.entry, s.stop, *s.targets()) if s else ("",) * 5),
            ]
        )


def check(client) -> None:
    today = now_ist().date()
    eng = Engine(client, today)
    print(f"token ok · previous session {eng.prev_day} · nearest expiry {eng.expiry:%Y-%m-%d} · {len(eng.contracts)} contracts")
    bars = eng.bars(NIFTY, now_ist())
    if bars.empty:
        print("no 5-min index bars yet")
        return
    spot = float(bars["close"].iloc[-1])
    print(f"NIFTY 5-min bars: {len(bars)} · last {bars.index[-1]:%d %b %H:%M} close {spot:,.2f}")
    for c in pick(eng.contracts, eng.expiry, spot, "CE") + pick(eng.contracts, eng.expiry, spot, "PE"):
        ob = eng.bars(c.key, now_ist())
        last = f"last {ob.index[-1]:%d %b %H:%M} close {ob['close'].iloc[-1]:,.2f}" if len(ob) else "no bars"
        print(f"  {c.symbol:<28} {c.key:<16} {len(ob):3d} bars · {last}")


def sleep_until(ts: pd.Timestamp) -> None:
    wait = (ts - now_ist()).total_seconds()
    if wait > 0:
        _time.sleep(wait)


def run(client, notify=send_telegram) -> None:
    eng = Engine(client, now_ist().date())
    warm = eng.step(now_ist())  # restart-safe: anything already decided stays silent
    hello = (
        f"NIFTY↔option watcher started {now_ist():%d %b %H:%M} IST · expiry {eng.expiry:%d %b}"
        f" · warm-up from {eng.prev_day:%d %b} · {len(warm)} earlier alert(s) skipped"
    )
    print(hello)
    notify("✅ " + hello)
    print("waiting for the 09:15 candle to close..." if now_ist().time() < time(9, 20) else "")

    sent: list[Alert] = []
    failed = 0
    while True:
        wake = now_ist().floor("5min") + BAR + LAG
        if wake.time() > STOP_AT:
            break
        sleep_until(wake)
        expected = now_ist().floor("5min") - BAR
        for _ in range(RETRIES + 1):
            try:
                alerts = eng.step(now_ist())
                failed = 0
            except UpstoxAuthError as e:
                notify(f"⛔ {e}")
                raise SystemExit(str(e))
            except Exception as e:
                failed += 1
                print(f"  ! fetch failed: {e}")
                if failed == MAX_FAILED_ROUNDS:
                    notify(f"⚠️ Upstox feed failing ({e}); no alerts until it recovers")
                alerts = []
            for a in alerts:
                msg = format_alert(a)
                print("\n" + msg + "\n")
                notify(msg)
                log_alert(a)
                sent.append(a)
            bars = eng.index_bars
            got = len(bars) and bars.index[-1] >= expected
            in_session = time(9, 20) <= now_ist().time() < STOP_AT
            if got or not in_session:
                break
            _time.sleep(15)
        bars = eng.index_bars
        if len(bars) and bars.index[-1].date() == eng.today:
            print(f"{bars.index[-1]:%H:%M} candle  NIFTY {bars['close'].iloc[-1]:,.2f}  pending {len(eng.pending)}")

    counts = {g: sum(a.grade == g for a in sent) for g in (CONFIRMED, INDEX_ONLY, OPTION_ONLY)}
    bye = f"Market closed. 🟢 {counts[CONFIRMED]} · 🟡 {counts[INDEX_ONLY]} · 🔵 {counts[OPTION_ONLY]}"
    for a in sent:
        bye += f"\n  {a.at:%H:%M} {a.grade} {a.side}" + (f" {a.contract.symbol}" if a.contract else "")
    print("\n" + bye)
    notify("🏁 " + bye)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="test the Upstox connection and exit")
    ap.add_argument("--test-telegram", action="store_true", help="send a test message and exit")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    if args.test_telegram:
        ok = send_telegram("✅ index-option-confirm: Telegram works")
        print("sent" if ok else "not sent: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return
    try:
        client = UpstoxClient()
        check(client) if args.check else run(client)
    except UpstoxAuthError as e:
        raise SystemExit(f"Upstox: {e}")


if __name__ == "__main__":
    main()
