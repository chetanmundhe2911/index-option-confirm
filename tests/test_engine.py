"""Engine end to end against a fake Upstox serving 1-min candles as time passes."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from confirm.matcher import CONFIRMED, INDEX_ONLY, OPTION_ONLY
from core.aclean import BUY, SELL, a_clean_signals
from core.candles import to_5min
from feeds.upstox import NIFTY
from main import Engine, format_alert, log_alert

PREV, TODAY = date(2024, 3, 4), date(2024, 3, 5)
EXPIRY = pd.Timestamp("2024-03-07")
BAR = pd.Timedelta(minutes=5)
STRIKES = range(21000, 23001, 50)


def walk(seed, start, sd):
    rng = np.random.default_rng(seed)
    frames, price = [], start
    for d in (PREV, TODAY):
        idx = pd.date_range(pd.Timestamp(d) + pd.Timedelta("09:15:00"), periods=375, freq="1min")
        rows = []
        for _ in idx:
            o = price
            price += rng.normal(0, sd)
            rows.append((o, max(o, price) + abs(rng.normal(0, sd / 3)), min(o, price) - abs(rng.normal(0, sd / 3)), price))
        frames.append(pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx))
    return pd.concat(frames)


def linear_option(index, strike, opt_type):
    """Premium moving 0.5 point per index point: same BB/EMA9 signals as the index."""
    if opt_type == "CE":
        f = lambda x: 300 + 0.5 * (x - strike)  # noqa: E731
        return pd.DataFrame({c: f(index[c]) for c in ("open", "high", "low", "close")})
    f = lambda x: 300 - 0.5 * (x - strike)  # noqa: E731
    return pd.DataFrame({"open": f(index["open"]), "high": f(index["low"]), "low": f(index["high"]), "close": f(index["close"])})


class FakeUpstox:
    def __init__(self, index, option_source):
        self.index = index
        self.option_source = option_source  # (strike, type) -> 1-min frame
        self.now = pd.Timestamp(TODAY) + pd.Timedelta("09:00:00")
        self.calls = 0
        rows = [
            {"expiry": EXPIRY, "strike": float(k), "type": t, "key": f"NSE_FO|{k}{t}", "symbol": f"NIFTY {k} {t}"}
            for k in STRIKES
            for t in ("CE", "PE")
        ]
        self.contracts = pd.DataFrame(rows)
        self._frames = {}

    def frame(self, key):
        if key == NIFTY:
            return self.index
        if key not in self._frames:
            k, t = int(key[7:-2]), key[-2:]
            self._frames[key] = self.option_source(k, t)
        return self._frames[key]

    def previous_trading_day(self, today):
        return PREV

    def option_contracts(self, underlying):
        return self.contracts

    def history(self, key, day):
        df = self.frame(key)
        return df[df.index.normalize() == pd.Timestamp(day)]

    def intraday(self, key):
        self.calls += 1
        df = self.frame(key)
        today = df[df.index.normalize() == pd.Timestamp(TODAY)]
        return today[today.index <= self.now]  # includes the forming minute, like the live API


def run_day(fake, start="09:00"):
    fake.now = pd.Timestamp(TODAY) + pd.Timedelta(f"{start}:00")
    eng = Engine(fake, TODAY)
    warm = eng.step(fake.now)
    alerts = []
    t = fake.now.floor("5min") + BAR + pd.Timedelta(seconds=20)
    while t <= pd.Timestamp(TODAY) + pd.Timedelta("15:31:00"):
        fake.now = t
        for a in eng.step(t):
            assert a.at <= t, "alert decided from data not yet available"
            assert a.at > t - BAR - pd.Timedelta(seconds=20), "alert sent late"
            alerts.append(a)
        t += BAR
    return eng, warm, alerts


def index_5m(index):
    return to_5min(index, pd.Timestamp(TODAY) + pd.Timedelta("16:00:00"))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_linear_options_confirm_every_index_signal_same_bar(seed):
    index = walk(seed, 22000, 4)
    fake = FakeUpstox(index, lambda k, t: linear_option(index, k, t))
    eng, warm, alerts = run_day(fake)

    bars = index_5m(index)
    expected = [s for side in (BUY, SELL) for s in a_clean_signals(bars, side) if s.signal_time.date() == TODAY]
    assert expected, "fixture should produce index signals"
    assert warm == []
    assert all(a.grade == CONFIRMED for a in alerts)
    assert sorted((a.side, a.index_signal.signal_time) for a in alerts) == sorted((s.side, s.signal_time) for s in expected)
    for a in alerts:
        assert a.option_signal.signal_time == a.index_signal.signal_time
        assert a.contract.offset == 0 and a.contract.type == ("CE" if a.side == BUY else "PE")
        assert a.contract.strike == round(a.spot / 50) * 50
        assert "🟢 CONFIRMED" in format_alert(a)
    assert not eng.pending


def test_unrelated_options_give_index_only_and_option_only():
    index = walk(4, 22000, 4)
    fake = FakeUpstox(index, lambda k, t: walk(1000 + k + (t == "PE"), 150, 1.5))
    eng, _, alerts = run_day(fake)
    grades = {a.grade for a in alerts}
    assert INDEX_ONLY in grades and OPTION_ONLY in grades
    for a in alerts:
        text = format_alert(a)
        assert text.startswith({CONFIRMED: "🟢", INDEX_ONLY: "🟡", OPTION_ONLY: "🔵"}[a.grade])
    assert not eng.pending
    # one alert per index signal and per option signal candle
    keys = [(a.grade, a.side, a.index_signal.signal_time if a.index_signal else a.option_signal.signal_time,
             a.contract.key if a.grade == OPTION_ONLY else None) for a in alerts]
    assert len(keys) == len(set(keys))


def test_restart_mid_day_repeats_nothing():
    index = walk(2, 22000, 4)
    source = lambda k, t: linear_option(index, k, t)  # noqa: E731
    _, _, full = run_day(FakeUpstox(index, source))
    _, warm, after = run_day(FakeUpstox(index, source), start="12:02")
    restart = pd.Timestamp(TODAY) + pd.Timedelta("12:02:00")
    assert [a for a in full if a.at > restart] == after
    assert len(warm) == sum(a.at <= restart for a in full)


def test_log_alert_writes_csv(tmp_path):
    index = walk(4, 22000, 4)
    fake = FakeUpstox(index, lambda k, t: walk(1000 + k + (t == "PE"), 150, 1.5))
    _, _, alerts = run_day(fake)
    path = tmp_path / "alerts.csv"
    for a in alerts:
        log_alert(a, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("alert_time,grade") and len(lines) == len(alerts) + 1
