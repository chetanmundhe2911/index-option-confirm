# index-option-confirm

Alerts-only bot: watches NIFTY 50 on 5-minute candles. When the index forms an
**A_clean** signal, it checks whether the ATM option (CE on BUY, PE on SELL)
confirms within the same candle or the next 2 candles, then sends a graded
Telegram alert. It never places orders. Full spec: [docs/SPEC.md](docs/SPEC.md).

## Status

| # | Milestone | State |
|---|---|---|
| 1 | `core/aclean.py` + unit tests | done |
| 2 | Replay engine on saved option CSVs, reproduce backtest table | next |
| 3 | Live index feed (Upstox WS) + index alerts to Telegram | |
| 4 | ATM option check + grading | |
| 5 | Outcome tracking + daily summary | |
| 6 | Two weeks of paper alerts | |

## Setup

```bash
conda create -n ioc python=3.11 -y && conda activate ioc
pip install -r requirements.txt
python -m pytest
```

## A_clean detector (`core/aclean.py`)

```python
from core.aclean import scan, a_clean_signals, BUY, SELL

# candles: DatetimeIndex (bar start, IST), columns open/high/low/close,
# closed 5-min bars only, previous day included for indicator warm-up.
for s in a_clean_signals(candles, BUY):
    print(s.signal_time, s.entry, s.stop, s.targets())
```

- `scan()` returns every signal candle with `a_clean` = `True` / `False` /
  `None` (still waiting for candle i+1 to close) and `known_at`, the close
  time of the bar that decided it.
- Indicators: Bollinger (20, 2, ddof=0) and EMA9 (`adjust=False`) on closes.
- Arm state resets each day. Only signal candles labelled 09:30–15:00 count.
