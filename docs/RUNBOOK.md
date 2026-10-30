# Runbook (paper)

| Situation | What ATA does | Owner action |
|---|---|---|
| Daily loss ≥ ₹150 | Freezes new entries for the rest of the trading day; stops/exits continue | None; resets next trading day |
| Drawdown ≥ ₹500 from peak | Persistent lockout across restarts; CRITICAL alert | Review, then `ata owner-clear drawdown_lockout ...` |
| Kill switch | `ata kill --author ... --reason ...` blocks new entries | `ata owner-clear kill_switch ...` |
| Broker timeout | Order → UNKNOWN; entries halted; broker queried; never resubmitted | None unless it stays UNKNOWN |
| Reconciliation mismatch / broker unreachable | Entries halted, CRITICAL alert | Investigate broker vs ledger |
| Session expired | Entries paused, CRITICAL alert; ATA never bypasses 2FA | Log in again manually |
| Stale/invalid data | No entries on affected symbols | Check feed |
| AI unavailable/malformed | No AI-originated entries; exits and stops continue | None |
| Stop can't be placed | CRITICAL alert, exit scheduled next session | Review |

Commands: `uv run ata simulate [--days 30] [--seed N] [--chaos] [-v]`, `uv run ata limits`, `uv run ata status`.
