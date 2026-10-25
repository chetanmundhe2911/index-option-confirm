<!-- Purpose: Standing instructions for Claude Code in this repo -->
# index-option-confirm

NIFTY 5-min index A_clean -> ATM option confirmation bot. Telegram alerts only, no orders.
Full rules: docs/SPEC.md (source of truth; read the relevant section before coding).

## Structure rules
- core/ is pure logic: imports only pandas/numpy. No API, files, Telegram or env vars.
- feeds/, notify/, agents/ may import core/. core/ never imports them.
- Every tunable number lives in config.yaml. Secrets live in .env.
- Closed candles only. Never use a forming candle (look-ahead bug).
- Agents (agents/) never create, change or upgrade a signal or trade level.

## How to work with me
- I am learning the repo. Work one file per step.
- Before writing a file: explain where it goes, why, and what it depends on. Wait for my OK.
- Prefer reviewing code I wrote over writing it for me.
- Never change signal rules (docs/SPEC.md sections 3-4) without asking.
- Every new module gets a test in tests/test_<module>.py.

## Commands
- Tests: python -m pytest -q
- Branch per milestone step: git checkout -b m<N>-<name>
- Commit prefixes: feat: fix: test: chore: docs:
