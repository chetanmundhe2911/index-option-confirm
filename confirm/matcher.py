"""Index <-> option matching and grading (spec §4). Pure functions.

CONFIRMED  : index A_clean, and an option (ATM first, then ±1) has an A_clean
             whose signal candle is the index signal candle or up to 2 later.
INDEX_ONLY : no such option A_clean once that window can no longer confirm.
OPTION_ONLY: an ATM CE/PE A_clean with no index A_clean of the matching side
             (CE <-> BUY, PE <-> SELL) whose signal candle is within 2 bars.
             Decided when the option A_clean is known, using index signals
             known by then (the option is almost never earlier than the index).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.aclean import BUY, SELL, Signal
from options.atm import Contract

BAR = pd.Timedelta(minutes=5)
WINDOW_BARS = 2

CONFIRMED = "CONFIRMED"
INDEX_ONLY = "INDEX_ONLY"
OPTION_ONLY = "OPTION_ONLY"

OPTION_FOR = {BUY: "CE", SELL: "PE"}
SIDE_FOR = {"CE": BUY, "PE": SELL}


@dataclass(frozen=True)
class Alert:
    grade: str
    at: pd.Timestamp  # when the grade became known
    side: str  # index side the alert is about (OPTION_ONLY: the matching side)
    index_signal: Signal | None = None
    spot: float | None = None
    contract: Contract | None = None
    option_signal: Signal | None = None


def grade_index_signal(
    idx: Signal,
    spot: float,
    candidates: list[Contract],
    option_signals: dict[str, list[Signal]],
    now: pd.Timestamp,
    bar: pd.Timedelta = BAR,
) -> Alert | None:
    """Grade one index A_clean signal, or None while it can still confirm.

    `candidates` are the option contracts picked at the index signal (ATM
    first); `option_signals` maps instrument key -> that option's signals.
    """
    lo = idx.signal_time
    hi = lo + WINDOW_BARS * bar
    for c in candidates:
        for s in option_signals.get(c.key, []):
            if s.a_clean and lo <= s.signal_time <= hi:
                return Alert(CONFIRMED, max(idx.known_at, s.known_at), idx.side, idx, spot, c, s)
    deadline = hi + 2 * bar  # close of the bar after the last allowed option signal candle
    if now >= deadline:
        return Alert(INDEX_ONLY, deadline, idx.side, idx, spot)
    return None


def is_option_only(opt: Signal, opt_type: str, index_signals: list[Signal], bar: pd.Timedelta = BAR) -> bool:
    side = SIDE_FOR[opt_type]
    near = WINDOW_BARS * bar
    return not any(
        s.a_clean and s.side == side and abs(s.signal_time - opt.signal_time) <= near
        for s in index_signals
    )
