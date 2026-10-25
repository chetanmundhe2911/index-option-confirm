"""Upstox client parsing, 1m->5m candles, ATM selection and grading."""

from datetime import date

import pandas as pd
import pytest

from confirm.matcher import CONFIRMED, INDEX_ONLY, grade_index_signal, is_option_only
from core.aclean import BUY, SELL, Signal
from core.candles import to_5min
from feeds.upstox import NIFTY, UpstoxAuthError, UpstoxClient, parse_candles
from options.atm import Contract, atm_strike, nearest_expiry, pick

T = pd.Timestamp


# --- feeds/upstox.py ----------------------------------------------------------


def test_parse_candles_sorts_and_converts_to_ist():
    rows = [
        ["2024-01-02T09:16:00+05:30", 101, 102, 100, 101.5, 1000, 0],
        ["2024-01-02T09:15:00+05:30", 100, 101, 99, 100.5, 900, 0],
    ]
    df = parse_candles(rows)
    assert list(df.index) == [T("2024-01-02 09:15"), T("2024-01-02 09:16")]
    assert df.loc[T("2024-01-02 09:16")].tolist() == [101, 102, 100, 101.5]
    assert parse_candles([]).empty


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, status=200, body=None):
        self.calls = []
        self.status, self.body = status, body

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        return FakeResponse(self.status, self.body)


def test_client_urls_and_auth_header():
    s = FakeSession(body={"status": "success", "data": {"candles": []}})
    c = UpstoxClient("tok", session=s)
    c.intraday(NIFTY)
    c.history("NSE_FO|12345", date(2024, 1, 2))
    assert s.calls[0][0] == "https://api.upstox.com/v2/historical-candle/intraday/NSE_INDEX%7CNifty%2050/1minute"
    assert s.calls[1][0] == "https://api.upstox.com/v2/historical-candle/NSE_FO%7C12345/1minute/2024-01-02/2024-01-02"
    assert s.calls[0][1]["Authorization"] == "Bearer tok"


def test_client_raises_clear_error_on_expired_token():
    c = UpstoxClient("tok", session=FakeSession(status=401, body={}))
    with pytest.raises(UpstoxAuthError):
        c.intraday(NIFTY)


def test_client_requires_token(monkeypatch):
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    with pytest.raises(UpstoxAuthError):
        UpstoxClient()


def test_option_contracts_parsing():
    data = [
        {"expiry": "2024-01-04", "strike_price": 21700.0, "instrument_type": "CE",
         "instrument_key": "NSE_FO|1", "trading_symbol": "NIFTY 21700 CE 04 JAN 24", "weekly": True},
        {"expiry": "2024-01-04", "strike_price": 21700.0, "instrument_type": "PE",
         "instrument_key": "NSE_FO|2", "trading_symbol": "NIFTY 21700 PE 04 JAN 24", "weekly": True},
    ]
    s = FakeSession(body={"status": "success", "data": data})
    df = UpstoxClient("tok", session=s).option_contracts()
    assert s.calls[0][2] == {"instrument_key": NIFTY}
    assert df["expiry"].iloc[0] == T("2024-01-04")
    assert df[["strike", "type", "key"]].values.tolist() == [[21700.0, "CE", "NSE_FO|1"], [21700.0, "PE", "NSE_FO|2"]]


# --- core/candles.py ----------------------------------------------------------


def minutes(start, n, base=100.0):
    idx = pd.date_range(start, periods=n, freq="1min")
    c = [base + k for k in range(n)]
    return pd.DataFrame({"open": c, "high": [x + 0.5 for x in c], "low": [x - 0.5 for x in c], "close": c}, index=idx)


def test_to_5min_aggregates_left_labelled_bars():
    bars = to_5min(minutes("2024-01-02 09:15", 10), T("2024-01-02 09:25:20"))
    assert list(bars.index) == [T("2024-01-02 09:15"), T("2024-01-02 09:20")]
    assert bars.iloc[0].tolist() == [100, 104.5, 99.5, 104]


def test_to_5min_never_returns_forming_bar():
    m = minutes("2024-01-02 09:15", 8)  # 09:15..09:22, includes the forming minute
    bars = to_5min(m, T("2024-01-02 09:22:30"))
    assert list(bars.index) == [T("2024-01-02 09:15")]


def test_to_5min_waits_for_last_minute_then_grace():
    m = minutes("2024-01-02 09:15", 4)  # 09:19 candle not published yet
    assert to_5min(m, T("2024-01-02 09:20:20")).empty
    assert len(to_5min(m, T("2024-01-02 09:22:00"))) == 1


def test_to_5min_drops_out_of_session_bars():
    m = minutes("2024-01-02 15:25", 10)
    assert list(to_5min(m, T("2024-01-02 16:00")).index) == [T("2024-01-02 15:25")]


# --- options/atm.py -----------------------------------------------------------


def contracts():
    rows = []
    for exp in ("2024-01-04", "2024-01-11"):
        for k in range(21500, 22001, 50):
            for t in ("CE", "PE"):
                rows.append({"expiry": T(exp), "strike": float(k), "type": t,
                             "key": f"{exp}-{k}-{t}", "symbol": f"NIFTY {k} {t} {exp}"})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("spot, atm", [(21712.0, 21700), (21725.0, 21750), (21724.9, 21700), (21776, 21800)])
def test_atm_strike(spot, atm):
    assert atm_strike(spot) == atm


def test_nearest_expiry_includes_expiry_day():
    assert nearest_expiry(contracts(), date(2024, 1, 4)) == T("2024-01-04")
    assert nearest_expiry(contracts(), date(2024, 1, 5)) == T("2024-01-11")


def test_pick_atm_first_then_neighbours():
    got = pick(contracts(), T("2024-01-04"), 21712.0, "CE")
    assert [(c.strike, c.offset) for c in got] == [(21700, 0), (21650, -1), (21750, 1)]
    assert all(c.type == "CE" and c.key.startswith("2024-01-04") for c in got)
    assert [c.strike for c in pick(contracts(), T("2024-01-04"), 21712.0, "PE", width=0)] == [21700]


# --- confirm/matcher.py -------------------------------------------------------


def sig(side, t, a_clean=True):
    st = T(f"2024-01-02 {t}")
    return Signal(side, st, 100.0, 95.0, a_clean, st + pd.Timedelta(minutes=10))


ATM = Contract(21700, "CE", "atm", "NIFTY 21700 CE", 0)
UP1 = Contract(21750, "CE", "up1", "NIFTY 21750 CE", 1)


@pytest.mark.parametrize("opt_time, expected", [("10:30", CONFIRMED), ("10:35", CONFIRMED), ("10:40", CONFIRMED), ("10:45", INDEX_ONLY)])
def test_grade_window_same_bar_to_two_bars_later(opt_time, expected):
    idx = sig(BUY, "10:30")
    a = grade_index_signal(idx, 21712, [ATM], {"atm": [sig(BUY, opt_time)]}, T("2024-01-02 10:50"))
    assert (a.grade if a else None) == expected


def test_grade_option_earlier_does_not_confirm():
    idx = sig(BUY, "10:30")
    assert grade_index_signal(idx, 21712, [ATM], {"atm": [sig(BUY, "10:25")]}, T("2024-01-02 10:45")) is None


def test_grade_index_only_after_window():
    idx = sig(BUY, "10:30")
    # last allowed option signal candle 10:40 is decided at the close of 10:45, i.e. 10:50
    assert grade_index_signal(idx, 21712, [ATM], {}, T("2024-01-02 10:49:59")) is None
    a = grade_index_signal(idx, 21712, [ATM], {}, T("2024-01-02 10:50:20"))
    assert a.grade == INDEX_ONLY and a.at == T("2024-01-02 10:50")


def test_grade_prefers_atm_and_ignores_non_clean():
    idx = sig(BUY, "10:30")
    opts = {"atm": [sig(BUY, "10:35"), sig(BUY, "10:30", a_clean=False)], "up1": [sig(BUY, "10:30")]}
    a = grade_index_signal(idx, 21712, [ATM, UP1], opts, T("2024-01-02 10:50"))
    assert a.grade == CONFIRMED and a.contract == ATM and a.option_signal.signal_time == T("2024-01-02 10:35")
    a = grade_index_signal(idx, 21712, [ATM, UP1], {"up1": [sig(BUY, "10:30")]}, T("2024-01-02 10:50"))
    assert a.contract == UP1


def test_option_only_checks_matching_side_within_ten_minutes():
    opt = sig(BUY, "11:00")
    assert is_option_only(opt, "CE", [])
    assert not is_option_only(opt, "CE", [sig(BUY, "10:50")])
    assert is_option_only(opt, "CE", [sig(BUY, "10:45")])
    assert is_option_only(opt, "CE", [sig(SELL, "11:00")])  # wrong side
    assert not is_option_only(opt, "PE", [sig(SELL, "11:00")])
    assert is_option_only(opt, "CE", [sig(BUY, "11:00", a_clean=False)])
