# index-option-confirm

Alerts-only bot: watches NIFTY 50 on 5-minute candles. When the index forms an
**A_clean** signal, it checks whether the ATM option (CE on BUY, PE on SELL)
confirms within the same candle or the next 2 candles, then sends a graded
Telegram alert. It never places orders. Full spec: [docs/SPEC.md](docs/SPEC.md).

## Status

| # | Milestone | State |
|---|---|---|
| 1 | `core/aclean.py` + unit tests | done |
| 2 | Replay engine on saved option CSVs, reproduce backtest table | needs sample CSVs |
| 3 | Live index feed (Upstox) + index alerts to Telegram | built, first live run pending |
| 4 | ATM option check + grading | built, first live run pending |
| 5 | Outcome tracking + daily summary | |
| 6 | Two weeks of paper alerts | |

## Setup

```bash
conda create -n ioc python=3.11 -y && conda activate ioc
pip install -r requirements.txt
python -m pytest
```

## Live run with Upstox (`main.py`)

```bash
cp .env.example .env          # once; then paste today's UPSTOX_ACCESS_TOKEN every morning
python main.py --check        # token, expiry, ATM CE/PE keys and candles look right?
python main.py                # start before 09:20 IST; stops after 15:30
```

Every 5-min close (+20 s) it pulls 1-min candles from Upstox REST v2 (same
endpoints as `nifty-bb-ema-ml/src/upstox_feed.py`) for NIFTY and the ATM ±1
CE/PE of the nearest expiry, builds closed 5-min bars and sends:

- 🟢 CONFIRMED: index A_clean + option A_clean in the same candle or up to 2 later
- 🟡 INDEX ONLY: no option confirmation within 10 min, skip
- 🔵 OPTION ONLY: ATM CE/PE A_clean with no matching index signal, 1:1 only

Alerts go to the terminal, Telegram (if set in `.env`) and `logs/alerts.csv`.
A restart mid-day re-warms from history and does not repeat alerts.

| File | Role |
|---|---|
| `feeds/upstox.py` | REST client: intraday/historical 1-min candles, option contracts |
| `core/candles.py` | 1-min -> closed 5-min bars |
| `options/atm.py` | nearest expiry, ATM strike, CE/PE keys |
| `confirm/matcher.py` | CONFIRMED / INDEX ONLY / OPTION ONLY rules |
| `main.py` | engine + live loop |

## Live paper test (index only, yfinance)

```bash
python live_yf.py                  # start any time before 09:20 IST; stops after 15:30
python live_yf.py --test-telegram  # check Telegram setup
```

Prints every closed 5-min candle with EMA9 and the bands, and an alert for each
index A_clean signal (also sent to Telegram if `.env` has the bot token and chat
ID, see `.env.example`). Alerts are logged to `logs/live_alerts.csv`.
No option check yet: that comes with the Upstox feed (milestones 3-4).

## Replay: test the live logic on recent days

`replay.py` feeds 5-min NIFTY candles one at a time, exactly as the live feed
will, and prints each index A_clean alert at the moment it would have fired,
with how the trade played out (T1/T2/T3/STOP/OPEN by session end).

```bash
python replay.py                       # last ~60 days of ^NSEI from yfinance
python replay.py --days 5              # just the last 5 sessions
python replay.py --save data/nifty_5m.csv   # keep a copy of the candles
python replay.py --csv data/nifty_5m.csv    # replay a saved / your own CSV
```

Example line:

```
BUY  2024-03-05 10:35  signal 10:25  entry 21,624.56  stop 21,607.33 (R 17.2)  T1 21,641.8  T2 21,659.0  T3 21,676.3  plan 1:2  ->  T3
```

The first time is when the alert fires (close of the confirming candle).
Check a few against a TradingView 5-min NIFTY chart with BB(20,2) and EMA9.
yfinance candles can differ slightly from NSE/Upstox, so expect the
occasional signal to differ.

`core/watcher.py` (`SignalWatcher`) is the bar-by-bar engine the live feed
will reuse: `warm_up()` with history, then `on_bar()` per closed candle.

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
