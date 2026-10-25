import pandas as pd

import live_yf
from core.aclean import BUY, SELL, a_clean_signals
from live_yf import LiveRunner, closed_bars, load_env
from tests.test_aclean import SESSION_BARS, random_walk

BAR = pd.Timedelta(minutes=5)


def test_closed_bars_drops_forming_and_off_grid_rows():
    idx = pd.DatetimeIndex(["2024-01-02 10:25", "2024-01-02 10:30", "2024-01-02 10:32:17"])
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    now = pd.Timestamp("2024-01-02 10:34:50")
    assert list(closed_bars(df, now).index) == [idx[0]]
    assert list(closed_bars(df, now + pd.Timedelta(seconds=10)).index) == idx[:2].tolist()


def test_runner_alerts_like_batch_and_never_uses_forming_bar():
    df = random_walk(3, days=2)
    day2 = df.index[SESSION_BARS]
    clock = {"now": day2 + pd.Timedelta(minutes=2)}  # 09:17, first candle still forming

    def fetch():
        # yfinance returns everything up to now, including the forming candle.
        return df[df.index <= clock["now"]]

    sent = []
    runner = LiveRunner(fetch, notify=sent.append, log=lambda s: None)
    assert runner.start(clock["now"]) == df.index[SESSION_BARS - 1]

    while clock["now"] < df.index[-1] + BAR:
        clock["now"] += BAR
        runner.poll(clock["now"])
        assert runner.last_bar + BAR <= clock["now"]

    expected = sorted(
        (s for side in (BUY, SELL) for s in a_clean_signals(df, side) if s.signal_time >= day2),
        key=lambda s: (s.known_at, s.side),
    )
    assert sorted(runner.alerts, key=lambda s: (s.known_at, s.side)) == expected
    assert len(sent) == len(expected) > 0


def test_restart_mid_session_sends_nothing_old():
    df = random_walk(3, days=2)
    now = df.index[SESSION_BARS + 50] + BAR + pd.Timedelta(seconds=30)
    runner = LiveRunner(lambda: df[df.index <= now], notify=lambda m: None, log=lambda s: None)
    runner.start(now)
    assert runner.poll(now) == 0 and runner.alerts == []


def test_load_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\nTELEGRAM_BOT_TOKEN="abc:123"\nTELEGRAM_CHAT_ID=42\n')
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    load_env(str(env))
    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "abc:123"
    assert os.environ["TELEGRAM_CHAT_ID"] == "42"


def test_telegram_skipped_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert live_yf.send_telegram("hi") is False


def test_alert_log(tmp_path):
    df = random_walk(3, days=2)
    sig = a_clean_signals(df, BUY)[0]
    path = tmp_path / "logs" / "a.csv"
    live_yf.log_alert(sig, path)
    live_yf.log_alert(sig, path)
    rows = path.read_text().splitlines()
    assert rows[0].startswith("alert_time,side") and len(rows) == 3
