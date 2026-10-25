# Purpose: Unit tests for core/aclean.py
import numpy as np
import pandas as pd
import pytest

from core.aclean import A_CLEAN, OTHER, PENDING, add_indicators, scan_buy


# ---------- helpers ----------
def frame(rows, start="2024-10-21 09:30"):
    """Hand-made 5-min bars. rows: (high, low, close, bb_lo, ema9)."""
    idx = pd.date_range(start, periods=len(rows), freq="5min")
    d = pd.DataFrame(rows, index=idx, columns=["high", "low", "close", "bb_lo", "ema9"])
    d.insert(0, "open", d["close"])
    return d


N   = (101, 99, 99.5, 90, 100)    # neutral: no touch, close below EMA
ARM = (100, 89, 95, 90, 100)      # low 89 <= band 90, close below EMA
SIG = (102, 94, 101, 90, 100)     # close 101 > EMA 100, low 94
OK  = (104, 95, 103, 90, 100)     # close > EMA, low 95 > 94


# ---------- indicators ----------
def test_indicator_definitions():
    rng = np.random.default_rng(0)
    c = 100 + rng.normal(0, 1, 60).cumsum()
    idx = pd.date_range("2024-10-21 09:15", periods=60, freq="5min")
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c}, index=idx)

    d = add_indicators(df)

    w = c[-20:]                                   # last 20 closes, computed by hand
    assert d["bb_mid"].iloc[-1] == pytest.approx(w.mean())
    assert d["bb_lo"].iloc[-1] == pytest.approx(w.mean() - 2 * w.std(ddof=0))
    assert d["bb_up"].iloc[-1] == pytest.approx(w.mean() + 2 * w.std(ddof=0))

    e, a = c[0], 2 / (9 + 1)                      # recursive EMA by hand
    for x in c[1:]:
        e = a * x + (1 - a) * e
    assert d["ema9"].iloc[-1] == pytest.approx(e)

    assert d["bb_lo"].iloc[:19].isna().all()      # no band before 20 bars
    assert "bb_lo" not in df.columns              # input not mutated


# ---------- BUY scan ----------
def test_buy_a_clean():
    d = frame([N, ARM, SIG, OK])
    s = scan_buy(d)
    assert len(s) == 1
    assert s[0].grade == A_CLEAN
    assert s[0].signal_time == d.index[2]
    assert s[0].confirm_time == d.index[3]


@pytest.mark.parametrize("nxt", [
    (104, 94, 103, 90, 100),      # low equal to signal low -> break (strict >)
    (104, 93, 103, 90, 100),      # low below
    (104, 95, 99.9, 90, 100),     # close back below EMA
])
def test_buy_other(nxt):
    assert scan_buy(frame([N, ARM, SIG, nxt]))[0].grade == OTHER


def test_pending_until_next_bar_closes():
    s = scan_buy(frame([N, ARM, SIG]))
    assert s[0].grade == PENDING and s[0].confirm_time is None


def test_arm_and_signal_on_same_bar():
    s = scan_buy(frame([N, (102, 89, 101, 90, 100), OK]))
    assert len(s) == 1 and s[0].grade == A_CLEAN


def test_disarm_after_signal_then_rearm():
    d = frame([ARM, SIG, SIG, SIG, ARM, SIG])
    assert [x.signal_time for x in scan_buy(d)] == [d.index[1], d.index[5]]


def test_arm_before_0930_does_not_count():
    assert scan_buy(frame([ARM, SIG], start="2024-10-21 09:25")) == []


def test_new_day_resets_arm():
    d1 = frame([ARM], start="2024-10-21 15:00")
    d2 = frame([SIG], start="2024-10-22 09:30")
    assert scan_buy(pd.concat([d1, d2])) == []
