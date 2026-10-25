"""Expiry, ATM strike and option instrument keys (spec §4)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

STRIKE_STEP = 50  # NIFTY


@dataclass(frozen=True)
class Contract:
    strike: float
    type: str  # "CE" | "PE"
    key: str
    symbol: str
    offset: int  # strikes away from ATM: 0, -1, +1


def atm_strike(spot: float, step: int = STRIKE_STEP) -> float:
    """round(spot / step) * step, halves rounded up."""
    return math.floor(spot / step + 0.5) * step


def nearest_expiry(contracts: pd.DataFrame, today) -> pd.Timestamp:
    future = contracts.loc[contracts["expiry"] >= pd.Timestamp(today).normalize(), "expiry"]
    if future.empty:
        raise RuntimeError(f"no option expiry on or after {today}")
    return future.min()


def pick(
    contracts: pd.DataFrame,
    expiry: pd.Timestamp,
    spot: float,
    opt_type: str,
    width: int = 1,
    step: int = STRIKE_STEP,
) -> list[Contract]:
    """ATM first, then -1, +1, ... strikes for one expiry and type."""
    atm = atm_strike(spot, step)
    rows = contracts[(contracts["expiry"] == expiry) & (contracts["type"] == opt_type)]
    by_strike = {float(r.strike): r for r in rows.itertuples()}
    out = []
    for off in [0] + [o for k in range(1, width + 1) for o in (-k, k)]:
        r = by_strike.get(atm + off * step)
        if r is not None:
            out.append(Contract(float(r.strike), opt_type, r.key, r.symbol, off))
    return out
