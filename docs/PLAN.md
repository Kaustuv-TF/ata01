# ATA-01 delivery plan

## Milestone 1 (this change): paper-trading core, offline

Done:
- Python 3.12 / uv project, ruff, mypy (strict), pytest + hypothesis, Docker/Compose (Postgres 16).
- Immutable ₹10k risk policy, persistent risk state (daily freeze, drawdown lockout, kill switch, owner-only clear).
- Independent risk gateway: spec §6 sizing and every limit, fail-closed gates, full rule trace.
- Paper broker: bid/ask, limit and stop-limit fills on daily bars, partial fills, rejections, timeouts (accepted or lost), price bands, holidays, session expiry, outages, fees.
- OMS state machine with deterministic client IDs, UNKNOWN handling and no blind retries; reconciliation; protective stops sized to filled qty.
- Market-data validators and IST market calendar.
- Four deterministic, versioned sample strategies (momentum, mean reversion, breakout, ETF trend). They are illustrative and not validated.
- AI contract: sanitized snapshot, strict output schema, semantic policy, offline deterministic ranker.
- Hash-chained audit log; demo CLI; 30-day review report.
- Paytm Money adapter stub (no endpoints).

## Spec §16 acceptance criteria

| # | Criterion | Where |
|---|---|---|
| 1 | Tests for ₹10k cap, ₹2,500 symbol, ₹7,500 gross, ₹50 trade risk, ₹100 aggregate, 3 positions, ₹150 daily freeze, ₹500 lockout | `tests/unit/test_risk_gateway.py`, `test_risk_state.py`, `test_limits.py`, `tests/property/` |
| 2 | Reject entry whose single share/min lot exceeds caps | `test_single_share_above_symbol_cap_is_skipped`, `test_minimum_lot_above_cap_is_skipped` |
| 3 | Reject when round-trip costs + spread remove after-cost edge | `test_after_cost_edge_rejects_thin_target` (spread enters via entry at ask + slippage) |
| 4 | Crash/restart keeps lockout; AI cannot change limits | `test_lockout_survives_restart`, `test_restart_keeps_lockout_and_blocks_entries`, `test_hostile_ai_cannot_change_limits_or_trade`, `test_ai_cannot_smuggle_controls` |
| 5 | Broker onboarding proof required; no fabricated Paytm endpoints | `docs/API_BROKER.md`, `test_paytm_adapter_does_not_fabricate_endpoints`, `test_paytm_adapter_fails_closed` |
| 6 | Paper engine: bid/ask, partial fills, rejection, timeout/reconciliation, price band, holidays | `tests/unit/test_paper_broker.py`, `test_oms.py`, `tests/chaos/` |
| 7 | Log every AI proposal, quantity, rule result, execution state, after-fee P&L | `test_every_ai_proposal_and_risk_decision_is_logged`, `audit_log` |
| 8 | Starts in paper with mock data and no credentials; non-trading without data; sample 30-day report | `Settings` defaults, `test_no_data_means_no_trading`, `uv run ata simulate` |

## Findings from the simulation the owner should see

1. **Costs dominate at ₹10,000.** With the placeholder schedule (₹20/order brokerage cap, STT, stamp, GST, DP ≈ ₹16 per sell) a round trip on a ₹1,000 to ₹2,500 position costs about ₹65. The ₹50 per-trade budget follows the spec formula (stop distance × qty) and excludes costs, so a stopped-out trade loses about ₹110. Five consecutive stop-outs reach the ₹500 lockout (seed 11 shows exactly this). Options: (a) keep as is, (b) count estimated costs inside the ₹50 budget, which will skip most trades at this capital, (c) restrict to instruments/brokerage plans where round-trip costs are far smaller. Needs the verified fee schedule first.
2. **Price bands constrain stops.** A 5% band instrument cannot hold a stop more than about 4% below the previous close; the gateway now rejects such entries (`stop_placeable`).
3. **Most intents are skipped**, mainly by `min_quantity` (risk or symbol cap too small for one share) and `stop_placeable`. That is expected at this capital and is the correct behaviour.

## Next milestones (spec §14)

- M2: Postgres schema for spec §11 entities with Alembic migrations; persist ledger/OMS; FastAPI health + read-only dashboard; durable scheduler (APScheduler) on the IST calendar; Telegram/email alerts.
- M3: Real historical data ingestion and instrument master; event-driven backtester with verified costs, walk-forward, out-of-sample, cost sensitivity (Gate 2).
- M4: Read-only Paytm Money adapter after onboarding (docs/API_BROKER.md); hosted LLM client behind the same `propose()` boundary with a separate API key.
- M5: 20+ sessions of automated paper-live (Gate 3). Live (Gate 4) only after explicit owner activation.

## Blocked on the owner / external

- Paytm Money API onboarding answers (docs/API_BROKER.md).
- Verified fee schedule and official holiday list.
- Real historical data source and licence.
- Decision on finding 1 above.
