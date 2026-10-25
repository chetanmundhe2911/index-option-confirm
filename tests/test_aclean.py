"""Unit tests for core.aclean on hand-made 5-min candles.

The fixture is a quiet market: closes alternate 100/101 with a 0.2 wick, so
the bands sit near 99.5 / 101.5, EMA9 near 100.5, and nothing ever arms.
Each test appends a few explicit (high, low, close) bars to it.
"""

import numpy as np
import pandas as pd
import pytest

from core.aclean import (
    BUY,
    SELL,
    a_clean_signals,
    add_indicators,
    scan,
)

PREV_DAY = "2024-01-01"
TODAY = "2024-01-02"
SESSION_BARS = 75  # 09:15 .. 15:25


def quiet(date, n, start="09:15"):
    closes = [100.0 + (k % 2) for k in range(n)]
    idx = pd.date_range(f"{date} {start}", periods=n, freq="5min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.2 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
        },
        index=idx,
    )


def market(bars, quiet_today=10, date=TODAY):
    """Previous quiet day + `quiet_today` quiet bars + `bars` (high, low, close)."""
    head = pd.concat([quiet(PREV_DAY, SESSION_BARS), quiet(date, quiet_today)])
    if not bars:
        return head
    start = head.index[-1] + pd.Timedelta(minutes=5)
    idx = pd.date_range(start, periods=len(bars), freq="5min")
    tail = pd.DataFrame(
        {
            "open": [c for _, _, c in bars],
            "high": [h for h, _, _ in bars],
            "low": [lo for _, lo, _ in bars],
            "close": [c for _, _, c in bars],
        },
        index=idx,
    )
    return pd.concat([head, tail])


def at(clock, date=TODAY):
    return pd.Timestamp(f"{date} {clock}")


# Ten quiet bars put the first appended bar at 10:05.
DROP = (99.2, 98.0, 99.0)  # low pierces the lower band, close stays below EMA9
SIGNAL_UP = (101.8, 100.5, 101.5)  # close back above EMA9
CONFIRM_UP = (102.3, 101.0, 102.0)  # close above EMA9, higher low

SPIKE = (103.0, 101.8, 102.0)  # high pierces the upper band, close above EMA9
SIGNAL_DOWN = (100.5, 99.2, 99.5)  # close back below EMA9
CONFIRM_DOWN = (100.0, 98.7, 99.0)  # close below EMA9, lower high


# --- indicators -------------------------------------------------------------


def test_bollinger_uses_population_std():
    df = add_indicators(market([DROP, SIGNAL_UP, CONFIRM_UP]))
    last20 = df["close"].iloc[-20:].to_numpy()
    mid, std = last20.mean(), last20.std(ddof=0)
    assert df["bb_mid"].iloc[-1] == pytest.approx(mid)
    assert df["bb_upper"].iloc[-1] == pytest.approx(mid + 2 * std)
    assert df["bb_lower"].iloc[-1] == pytest.approx(mid - 2 * std)


def test_ema9_is_recursive_with_adjust_false():
    df = add_indicators(market([DROP, SIGNAL_UP, CONFIRM_UP]))
    alpha = 2 / (9 + 1)
    ema = df["close"].iloc[0]
    for c in df["close"].iloc[1:]:
        ema = alpha * c + (1 - alpha) * ema
    assert df["ema9"].iloc[-1] == pytest.approx(ema)


def test_bands_undefined_until_20_bars():
    df = add_indicators(quiet(PREV_DAY, 25))
    assert df["bb_lower"].iloc[:19].isna().all()
    assert df["bb_lower"].iloc[19:].notna().all()


def test_quiet_market_has_no_signals():
    df = market([])
    assert scan(df, BUY) == []
    assert scan(df, SELL) == []


# --- BUY --------------------------------------------------------------------


def test_fixture_preconditions_buy():
    df = add_indicators(market([DROP, SIGNAL_UP, CONFIRM_UP]))
    drop, sig, conf = df.loc[at("10:05")], df.loc[at("10:10")], df.loc[at("10:15")]
    assert drop["low"] <= drop["bb_lower"] and drop["close"] <= drop["ema9"]
    assert sig["close"] > sig["ema9"]
    assert conf["close"] > conf["ema9"] and conf["low"] > sig["low"]


def test_buy_a_clean():
    [s] = scan(market([DROP, SIGNAL_UP, CONFIRM_UP]), BUY)
    assert s.side == BUY
    assert s.signal_time == at("10:10")
    assert s.a_clean is True
    assert s.known_at == at("10:20")  # close of the confirming bar
    assert s.entry == 101.5
    assert s.stop == 100.5
    assert s.risk == 1.0
    assert s.targets() == (102.5, 103.5, 104.5)


def test_buy_not_clean_when_next_low_not_higher():
    weak = (102.3, 100.5, 102.0)  # equal low is not a higher low
    [s] = scan(market([DROP, SIGNAL_UP, weak]), BUY)
    assert s.a_clean is False
    assert a_clean_signals(market([DROP, SIGNAL_UP, weak]), BUY) == []


def test_buy_not_clean_when_next_close_below_ema():
    fade = (101.0, 100.6, 100.0)  # higher low but closes under EMA9
    [s] = scan(market([DROP, SIGNAL_UP, fade]), BUY)
    assert s.a_clean is False


def test_buy_pending_until_next_bar_closes():
    df = market([DROP, SIGNAL_UP])
    [s] = scan(df, BUY)
    assert s.a_clean is None and s.known_at is None
    assert a_clean_signals(df, BUY) == []


def test_buy_needs_arming_first():
    # Same close-above-EMA bar without the band touch: no signal.
    shallow = (100.2, 99.8, 99.9)
    assert scan(market([shallow, SIGNAL_UP, CONFIRM_UP]), BUY) == []


def test_disarm_after_signal():
    # A dip under EMA9 and back without re-touching the band is not a new signal.
    dip = (101.0, 100.3, 100.4)
    again = (102.5, 101.5, 102.2)
    df = market([DROP, SIGNAL_UP, CONFIRM_UP, dip, again, again])
    assert [s.signal_time for s in scan(df, BUY)] == [at("10:10")]


def test_stays_armed_until_close_above_ema():
    below = (99.6, 99.0, 99.4)  # still under EMA9, still armed
    df = market([DROP, below, below, SIGNAL_UP, CONFIRM_UP])
    [s] = scan(df, BUY)
    assert s.signal_time == at("10:20") and s.a_clean


def test_arm_bar_can_be_signal_bar():
    hammer = (101.8, 97.5, 101.6)  # pierces the band and closes above EMA9
    df = market([hammer, CONFIRM_UP])
    pre = add_indicators(df).loc[at("10:05")]
    assert pre["low"] <= pre["bb_lower"] and pre["close"] > pre["ema9"]

    [s] = scan(df, BUY)
    assert s.signal_time == at("10:05") and s.a_clean
    # Otherwise the hammer only arms, and the next close above EMA9 is the signal.
    [s] = scan(df, BUY, arm_bar_can_signal=False)
    assert s.signal_time == at("10:10")


def test_arm_resets_at_day_change():
    # Armed in the last bar of the previous day; first bar today closes above EMA9.
    prev = quiet(PREV_DAY, SESSION_BARS - 1)
    drop = pd.DataFrame(
        {"open": [99.0], "high": [99.2], "low": [98.0], "close": [99.0]},
        index=[at("15:25", PREV_DAY)],
    )
    up = pd.DataFrame(
        {
            "open": [101.5, 102.0],
            "high": [101.8, 102.3],
            "low": [100.5, 101.0],
            "close": [101.5, 102.0],
        },
        index=[at("09:15"), at("09:20")],
    )
    df = pd.concat([prev, drop, up])
    assert scan(df, BUY, window=None) == []


def test_signal_window_filters_signal_candle_time():
    early = market([DROP, SIGNAL_UP, CONFIRM_UP], quiet_today=1)  # signal 09:25
    assert scan(early, BUY, window=None)[0].signal_time == at("09:25")
    assert scan(early, BUY) == []

    edge = market([DROP, SIGNAL_UP, CONFIRM_UP], quiet_today=68)  # signal 15:00
    assert scan(edge, BUY)[0].signal_time == at("15:00")

    late = market([DROP, SIGNAL_UP, CONFIRM_UP], quiet_today=69)  # signal 15:05
    assert scan(late, BUY) == []


def test_signal_on_last_bar_of_day_cannot_confirm():
    df = market([DROP, SIGNAL_UP], quiet_today=SESSION_BARS - 2)  # signal 15:25
    nxt = quiet("2024-01-03", 3)
    [s] = scan(pd.concat([df, nxt]), BUY, window=None)
    assert s.signal_time == at("15:25") and s.a_clean is False


# --- SELL -------------------------------------------------------------------


def test_fixture_preconditions_sell():
    df = add_indicators(market([SPIKE, SIGNAL_DOWN, CONFIRM_DOWN]))
    spike, sig, conf = df.loc[at("10:05")], df.loc[at("10:10")], df.loc[at("10:15")]
    assert spike["high"] >= spike["bb_upper"] and spike["close"] >= spike["ema9"]
    assert sig["close"] < sig["ema9"]
    assert conf["close"] < conf["ema9"] and conf["high"] < sig["high"]


def test_sell_a_clean():
    [s] = scan(market([SPIKE, SIGNAL_DOWN, CONFIRM_DOWN]), SELL)
    assert s.side == SELL
    assert s.signal_time == at("10:10")
    assert s.a_clean is True
    assert s.entry == 99.5
    assert s.stop == 100.5
    assert s.targets() == (98.5, 97.5, 96.5)


def test_sell_not_clean_when_next_high_not_lower():
    weak = (100.5, 98.7, 99.0)
    [s] = scan(market([SPIKE, SIGNAL_DOWN, weak]), SELL)
    assert s.a_clean is False


def test_buy_and_sell_are_independent():
    df = market([SPIKE, SIGNAL_DOWN, CONFIRM_DOWN])
    assert scan(df, BUY) == []
    df = market([DROP, SIGNAL_UP, CONFIRM_UP])
    assert scan(df, SELL) == []


# --- no look-ahead ----------------------------------------------------------


def random_walk(seed, days=3):
    rng = np.random.default_rng(seed)
    frames = []
    price = 22000.0
    for d in range(days):
        date = pd.Timestamp("2024-03-04") + pd.Timedelta(days=d)
        idx = pd.date_range(date + pd.Timedelta("09:15:00"), periods=SESSION_BARS, freq="5min")
        o, h, lo, c = [], [], [], []
        for _ in idx:
            op = price
            price = price + rng.normal(0, 15)
            o.append(op)
            c.append(price)
            h.append(max(op, price) + abs(rng.normal(0, 6)))
            lo.append(min(op, price) - abs(rng.normal(0, 6)))
        frames.append(pd.DataFrame({"open": o, "high": h, "low": lo, "close": c}, index=idx))
    return pd.concat(frames)


@pytest.mark.parametrize("side", [BUY, SELL])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_look_ahead(side, seed):
    """Every decision made on a prefix of the data stays the same with more data."""
    df = random_walk(seed)
    full = {s.signal_time: s for s in scan(df, side)}
    assert any(s.a_clean for s in full.values()), "fixture should produce A_clean signals"

    for end in range(SESSION_BARS, len(df) + 1):
        prefix = df.iloc[:end]
        now = prefix.index[-1] + pd.Timedelta(minutes=5)  # close of the last bar
        for s in scan(prefix, side):
            assert s.signal_time in full
            if s.a_clean is None:
                known = full[s.signal_time].known_at
                assert known is None or known >= now
            else:
                assert s == full[s.signal_time]
                assert s.known_at <= now


# --- input checks -----------------------------------------------------------


def test_rejects_bad_input():
    df = market([])
    with pytest.raises(ValueError):
        scan(df, "LONG")
    with pytest.raises(ValueError):
        scan(df.drop(columns="low"), BUY)
    with pytest.raises(ValueError):
        scan(df.iloc[::-1], BUY)
    with pytest.raises(TypeError):
        scan(df.reset_index(drop=True), BUY)
