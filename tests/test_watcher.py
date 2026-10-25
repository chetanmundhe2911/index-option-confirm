import pandas as pd
import pytest

from core.aclean import BUY, SELL, Signal, a_clean_signals
from core.watcher import SignalWatcher
from replay import load_csv, outcome, replay
from tests.test_aclean import SESSION_BARS, random_walk

BAR = pd.Timedelta(minutes=5)


def feed(watcher, candles):
    alerts = []
    for ts, bar in candles.iterrows():
        for s in watcher.on_bar(ts, bar["open"], bar["high"], bar["low"], bar["close"]):
            alerts.append((ts, s))
    return alerts


def batch_expected(df, day):
    """A_clean signals for `day`, computed in one go on previous day + that day."""
    days = df.index.normalize().unique()
    i = days.get_loc(day)
    two = df[(df.index >= days[i - 1]) & (df.index < day + pd.Timedelta(days=1))]
    out = []
    for side in (BUY, SELL):
        out += [s for s in a_clean_signals(two, side) if s.signal_time >= day]
    return sorted(out, key=lambda s: (s.known_at, s.side))


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_watcher_matches_batch_and_alerts_on_confirming_close(seed):
    df = random_walk(seed, days=4)
    w = SignalWatcher()
    w.warm_up(df.iloc[:SESSION_BARS])
    alerts = feed(w, df.iloc[SESSION_BARS:])

    for ts, s in alerts:
        assert s.known_at == ts + BAR  # fired at the close of the confirming bar

    got = sorted((s for _, s in alerts), key=lambda s: (s.known_at, s.side))
    expected = []
    for day in df.index.normalize().unique()[1:]:
        expected += batch_expected(df, day)
    assert got == expected
    assert got, "fixture should produce alerts"


def test_restart_mid_day_does_not_repeat_alerts():
    df = random_walk(2, days=3)
    full = SignalWatcher()
    full.warm_up(df.iloc[:SESSION_BARS])
    all_alerts = feed(full, df.iloc[SESSION_BARS:])

    cut = SESSION_BARS * 2 + 40  # crash at ~12:35 on day 3
    restarted = SignalWatcher()
    restarted.warm_up(df.iloc[:cut])
    after = feed(restarted, df.iloc[cut:])

    assert [s for _, s in after] == [s for ts, s in all_alerts if ts >= df.index[cut]]


def test_watcher_rejects_out_of_order_bars():
    df = random_walk(1, days=1)
    w = SignalWatcher()
    w.warm_up(df)
    with pytest.raises(ValueError):
        w.on_bar(df.index[-1], 1, 1, 1, 1)


def test_watcher_keeps_two_sessions():
    df = random_walk(1, days=4)
    w = SignalWatcher()
    w.warm_up(df.iloc[:SESSION_BARS])
    feed(w, df.iloc[SESSION_BARS:])
    assert w.candles.index.normalize().nunique() == 2


# --- replay helpers ----------------------------------------------------------


def sig(side, entry, stop):
    return Signal(side, pd.Timestamp("2024-01-02 10:10"), entry, stop, True, pd.Timestamp("2024-01-02 10:20"))


def bars(rows):
    idx = pd.date_range("2024-01-02 10:20", periods=len(rows), freq="5min")
    return pd.DataFrame(rows, columns=["high", "low"], index=idx)


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([(101.0, 99.5)], "STOP"),  # stop 100 hit first
        ([(102.0, 100.5), (101.5, 99.0)], "T1"),  # T1 then stopped
        ([(102.0, 100.5), (103.2, 101.0)], "T2"),
        ([(104.5, 100.5)], "T3"),
        ([(101.5, 100.5)], "OPEN"),
        ([(103.5, 99.0)], "STOP"),  # same bar touches stop and target: stop first
    ],
)
def test_outcome_buy(rows, expected):
    assert outcome(sig(BUY, 101.0, 100.0), bars(rows)) == expected


def test_outcome_sell():
    s = sig(SELL, 100.0, 101.0)
    assert outcome(s, bars([(100.5, 98.9)])) == "T1"
    assert outcome(s, bars([(100.5, 96.9)])) == "T3"
    assert outcome(s, bars([(101.0, 99.5)])) == "STOP"


def test_load_csv_and_replay(tmp_path):
    df = random_walk(3, days=3)
    path = tmp_path / "nifty.csv"
    df.rename_axis("datetime").to_csv(path)
    loaded = load_csv(path)
    pd.testing.assert_frame_equal(loaded, df, check_freq=False, check_names=False)

    results = replay(loaded)
    assert results and all(r in {"STOP", "OPEN", "T1", "T2", "T3"} for _, r in results)


def test_load_yfinance_parses_multiindex_utc(monkeypatch):
    """Shape of yf.download output: MultiIndex columns, UTC index, IST session."""
    import sys
    import types

    idx = pd.DatetimeIndex(["2024-01-02 03:45", "2024-01-02 03:50", "2024-01-02 10:00"], tz="UTC")
    cols = pd.MultiIndex.from_product([["Adj Close", "Close", "High", "Low", "Open", "Volume"], ["^NSEI"]])
    raw = pd.DataFrame([[1, 10, 12, 9, 11, 0]] * 3, index=idx, columns=cols, dtype=float)
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=lambda *a, **k: raw))

    from replay import load_yfinance

    df = load_yfinance()
    # 03:45 / 03:50 UTC = 09:15 / 09:20 IST; 10:00 UTC = 15:30 IST is after the session.
    assert list(df.index) == [pd.Timestamp("2024-01-02 09:15"), pd.Timestamp("2024-01-02 09:20")]
    assert list(df.columns) == ["open", "high", "low", "close"]
    assert df.iloc[0].tolist() == [11.0, 12.0, 9.0, 10.0]
