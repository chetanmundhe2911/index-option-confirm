# Build Spec: Index → ATM Option A_clean Confirmation Bot (alerts only)

Paste this into a new chat in the trading Project and say: **"Build this, step by step, starting with Milestone 1."**

---

## 1. Goal
1. Monitor the **NIFTY 50 index** live on **5-minute** candles.
2. When the index forms an **A_clean** signal, check whether the **ATM option** (CE on a BUY, PE on a SELL) also forms A_clean in the **same 5-min candle or the next 2 candles (≤10 min)**.
3. Send graded **Telegram alerts**.
4. Alerts only, no order placement. Runs on the local Windows laptop (Git Bash + Anaconda).
5. An **agentic layer (LangGraph)** sits beside the signal engine: it explains, risk-checks, journals and answers questions about alerts. It never creates, changes or upgrades a signal (§10).
6. Later: add SENSEX (`BSE_INDEX|SENSEX`, strike step 100) for Wed–Thu.

---

## 2. Why: backtest evidence (314 days, Jan–Jun 2023 + Jan–Oct 2024, 5-min, ATM CE/PE)

| Option A_clean trade | Trades | 1:1 win | 1:2 win | 1:3 win | Pts/trade @1:1 |
|---|---|---|---|---|---|
| Index A_clean in SAME 5-min bar | 255 | 67.5% | 47.8% | 40.0% | +4.4 |
| Index A_clean 5–10 min earlier | 54 | 72.2% | 50.0% | 46.3% | +2.2 |
| Index signal 15–30 min away | 21 | 52% | 29% | 14% | −1.0 |
| No index signal (option-only) | 213 | 64.3% | 46.5% | 35.7% | +2.0 |

- **Confirmed vs option-only:** 68% vs 64% at 1:1, 41% vs 36% at 1:3, about 2× points per trade, about 1 confirmed trade per day.
- **Option-only PE** lost at 1:3, so take **1:1 only** for option-only signals.

**Timing:** when both index and option signal, 78% fall in the same 5-min bar and ~20% have the option 1 bar later. The option is almost never earlier.

**Index A_clean on NIFTY 5-min, 2015–2021:**
- BUY: 65% win at 1:1, 47% at 1:2
- SELL: 67% at 1:1, 50% at 1:2
- Afternoon is best for 1:3.
- "Other" signals win about 7%, so never trade them.

---

## 3. Signal rules (identical for index and options)
- **Indicators** on 5-min closes:
  - Bollinger Bands **(20, 2, population std / ddof=0)**
  - **EMA9** (ewm span=9, adjust=False)
  - Warm-up using the previous day's candles.

### BUY (index BUY, or any option premium since we only buy options)
1. **Arm:** candle low ≤ lower BB.
2. **Signal candle (i):** while armed, the first candle that closes > EMA9. Disarm after the signal.
3. **A_clean:** bar i+1 closes > EMA9 AND low[i+1] > low[i]. Known only when bar i+1 closes.

### SELL (index only; option side = ATM PE)
- **Arm:** high ≥ upper BB.
- **Signal:** first close < EMA9.
- **A_clean:** bar i+1 closes < EMA9 AND high[i+1] < high[i].

### Other rules
- **Window:** signals between 09:30 and 15:00 IST. Reset `armed` at day change.
- **Trade levels (option):**
  - entry = option signal-candle close
  - stop = option signal-candle low
  - targets 1R / 2R / 3R where R = entry − stop
- **Closed candles only.** Never use a forming candle (avoids the repainting / look-ahead bug seen before).

### Edge-case decisions (aligned with `nifty-bb-ema-ml/src/pipeline.py`)
- Arm and signal can happen on the **same candle** (arm is checked first).
- Candles outside 09:30–15:00 **disarm** and cannot arm; a touch before 09:30 does not carry into the window.
- Signal with zero risk (BUY close = low, SELL close = high) is skipped and disarms.
- A_clean break test is strict: BUY needs `low[i+1] > low[i]`, SELL needs `high[i+1] < high[i]`.
- Bar i+1 must be on the same day; otherwise the signal is graded OTHER.
- Indicators run continuously across the previous day + today (warm-up); only `armed` resets daily.

---

## 4. Matching logic
1. **On index A_clean** (confirmed at the close of index bar i+1):
   - **ATM strike** = round(spot / 50) × 50, nearest weekly expiry.
   - **Options:** BUY → CE, SELL → PE. Check ATM plus ±1 strike (ATM is primary).
   - **Load** each option's 5-min candles (previous day + today) and run the same A_clean BUY detector.
2. **Grading:**
   - option signal candle in the **same bar** as the index signal candle, or up to **2 bars later** → 🟢 **CONFIRMED** (target 1:2, or 1:3 in the afternoon)
   - no option A_clean within 2 bars → 🟡 **INDEX ONLY**, skip (alert for information)
3. **Independently**, any ATM CE/PE A_clean with no index A_clean of the matching side within ±10 min → 🔵 **OPTION ONLY** (1:1 only; flag PE as weaker).
4. **De-duplicate:** one alert per index signal; one per option signal candle.

---

## 5. Data / Upstox API (token in `.env` as `UPSTOX_ACCESS_TOKEN`; Analytics token is read-only, which is fine)

| Need | Endpoint / key |
|---|---|
| Index key | `NSE_INDEX\|Nifty 50` |
| Live ticks | Upstox Market Data Feed **WebSocket** (v3), mode `ltpc` or `full` |
| History for warm-up | `GET /v3/historical-candle/{key}/minutes/1/{to}/{from}`, plus `/v3/historical-candle/intraday/{key}/minutes/1` for today |
| Expiries / ATM keys | `GET /v2/option/contract?instrument_key=NSE_INDEX|Nifty 50` and `GET /v2/option/chain?instrument_key=...&expiry_date=YYYY-MM-DD` |

- **Candles:** build 1-min from ticks, then resample to 5-min (label=left, closed=left).
- **Option candles:** on a trigger, fetch via the intraday endpoint, or subscribe ATM ±1 at startup and keep updating ATM as spot moves (preferred, no API lag).
- Confirm exact endpoints against the current Upstox docs before coding.

---

## 6. Alert format (Telegram)
```
🟢 CONFIRMED BUY · NIFTY 5-min · 10:35
Index A_clean: signal 10:30, confirmed 10:35 · spot 22,415
Option: NIFTY 24OCT 22400 CE · A_clean same bar
Entry 132.50 · Stop 121.80 (R 10.7) · T1 143.2 · T2 153.9 · T3 164.6
Plan: 1:2 (afternoon → 1:3)
```
- 🟡 INDEX ONLY: "no CE/PE confirmation in 10 min, skip".
- 🔵 OPTION ONLY: "1:1 only".
- **15:30 daily summary:** signals by grade, which hit T1/T2/T3 or the stop, and running stats.

---

## 7. Repo layout (`index-option-confirm/`)
```
config.yaml            # index, strike_step, window_bars=2, session times, targets, telegram ids
main.py                # orchestrator / event loop
feeds/ws_feed.py       # Upstox websocket + reconnect
feeds/history.py       # warm-up + intraday fetch
core/candles.py        # tick -> 1m -> 5m (closed bars only)
core/aclean.py         # shared BUY/SELL A_clean detector (pure functions)
options/atm.py         # expiry + ATM strike + instrument keys
confirm/matcher.py     # index↔option matching + grading
track/outcomes.py      # follow each alert to T1/T2/T3/stop, write logs/alerts.csv
notify/telegram.py
agents/                # LangGraph agentic layer (§10), outside the signal path
  state.py             # AlertState / ChatState TypedDicts
  graph.py             # Supervisor StateGraph + routing
  supervisor.py        # routes events: SIGNAL / OUTCOME / EOD / CHAT
  explainer.py         # plain-language "why" line for each alert
  risk_guard.py        # deterministic risk rules + optional LLM note
  journal.py           # per-alert and end-of-day journal entries
  chat.py              # Telegram Q&A over logs (read-only tools)
  tools.py             # read-only tools: query alerts.csv, backtest stats, today's candles
  prompts/             # versioned prompt files
tests/test_aclean.py   # unit tests on hand-made candles
tests/test_risk_guard.py  # rule table tests, no LLM
tests/test_agents_fallback.py  # LLM timeout/error -> deterministic alert still sent
tests/replay_test.py   # replay 2023–24 nifty_options_*.csv days; must reproduce ~1 confirmed/day and stats in §2
```

---

## 8. Milestones
1. **`core/aclean.py` + unit tests:** must match the backtest definitions exactly.
2. **Replay engine** on saved files (`nifty_options_DD_MM_YYYY.csv`; index rebuilt as strike + CE − PE, median of ATM±2): reproduce the §2 table.
3. **Live index feed:** 5-min candles + index A_clean alerts to Telegram.
4. **ATM option check + grading** (CONFIRMED / INDEX ONLY / OPTION ONLY).
5. **Outcome tracking + daily summary.**
6. **Agentic layer (§10):** Risk Guard (rules only) → Supervisor graph → Explainer → Journal → Chat. Each agent ships behind a config flag; alerts must still go out with all agents off.
7. **Two weeks of paper alerts:** compare live stats with the backtest; Journal agent writes the weekly review.
8. **(Later)** SENSEX support, semi-auto order buttons (needs static IP + X-Algo-Name header per Upstox/SEBI rules).

---

## 9. Acceptance criteria
- Replay reproduces confirmed-trade stats within a few % of §2.
- No look-ahead: every decision uses only closed candles available at that time.
- Alert within 10 s of the confirming 5-min candle close.
- Safe restart mid-day (re-warm from history, no duplicate alerts).
- Agentic layer: with the LLM down or slow, the deterministic alert still arrives within 10 s; no agent output ever changes side, grade, entry, stop or targets.
- Laptop checklist: plugged in, sleep disabled 09:00–15:45, auto-reconnect, kill switch on feed loss.

---

## 10. Agentic layer (LangGraph)

### 10.1 Principle
The signal engine (§3–§4) is deterministic and is the only source of signals, grades and trade levels. Agents read its output and add explanation, risk control, journaling and chat. **Agents may suppress or downgrade an alert (Risk Guard only), never create or upgrade one.**

### 10.2 Graph
```
 events from main.py ──► Supervisor ──┬─► SIGNAL ─► Risk Guard ─► Explainer ─► Telegram ─► Journal
 (signal, outcome,                    ├─► OUTCOME ─────────────────────────────────────► Journal
  15:30 EOD, user msg)                ├─► EOD ─────► Journal (daily summary) ─► Telegram
                                      └─► CHAT ────► Chat agent (read-only tools) ─► Telegram
```
- **State (`AlertState`):** `event_type`, `signal` (frozen dict from `core/aclean.py` + matcher), `grade`, `levels`, `risk_verdict`, `risk_reasons`, `explanation`, `message`, `journal_id`.
- **Checkpointer:** SQLite (`logs/agents.sqlite`) so a mid-day restart resumes without duplicate alerts; thread id = signal id.

### 10.3 Agents
| Agent | Input | Output | LLM? |
|---|---|---|---|
| **Supervisor** | raw event | route: SIGNAL / OUTCOME / EOD / CHAT | No. Plain routing on `event_type`. |
| **Risk Guard** | signal + today's alerts/outcomes | `ALLOW` / `DOWNGRADE_TO_INFO` / `BLOCK` + reasons | Rules decide. LLM may only phrase the reason. |
| **Explainer** | signal, grade, levels, time of day, §2 stats | one or two lines: why this grade, which plan (1:1 / 1:2 / 1:3) | Yes. Gets numbers in, may not output new numbers. |
| **Journal** | every alert, outcome, EOD | rows in `logs/journal.csv` + 15:30 summary + weekly review | Summary text only. Stats computed in pandas. |
| **Chat** | Telegram message from the owner's chat id | answer from logs | Yes, with read-only tools only. |

### 10.4 Risk Guard rules (`config.yaml → risk:`)
- Daily stop count ≥ `max_stops_per_day` (default 3) → BLOCK further CONFIRMED/OPTION ONLY alerts for the day.
- First two A_clean trades of the day both hit stop → DOWNGRADE_TO_INFO for the rest of the day (weak-day rule).
- Feed stale > 30 s or missing candles in the signal window → BLOCK.
- Alerts in the last `cutoff_minutes` before 15:00 on expiry day → DOWNGRADE_TO_INFO (configurable).
- Option R < `min_r_points` or R > `max_r_points` (tiny stops slip, huge stops did worst) → DOWNGRADE_TO_INFO.
- Option-only PE → keep 🔵 grade, force plan 1:1 (already in §4), add "weaker" tag.

### 10.5 Guardrails
- **Latency budget:** Explainer has 4 s (`agents.llm_timeout_s`). On timeout or error, send the deterministic §6 message unchanged; Journal logs `explainer=fallback`.
- **Number check:** after the Explainer, a validator rejects any text containing a price/number not present in the input state; fall back to the template.
- **Chat scope:** tools are read-only (`query_alerts`, `get_backtest_stats`, `get_today_candles`); answers only to the configured chat id; no order tools exist at this stage.
- **Prompts** live in `agents/prompts/*.md` with a version tag written into every journal row.
- **Observability:** LangSmith tracing optional (`LANGSMITH_API_KEY` in `.env`); otherwise log prompts, latency and token counts to `logs/agents.jsonl`.
- **Cost:** model and max tokens set in `config.yaml → agents:`; expected ~3–6 LLM calls per day plus chat.

### 10.6 Config additions
```yaml
agents:
  enabled: true
  explainer: true
  journal: true
  chat: true
  llm_model: claude-sonnet-4-6      # any LangChain chat model
  llm_timeout_s: 4
risk:
  max_stops_per_day: 3
  weak_day_rule: true
  min_r_points: 3
  max_r_points: 40
  expiry_cutoff_minutes: 30
  stale_feed_s: 30
```
