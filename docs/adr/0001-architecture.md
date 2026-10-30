# ADR-0001: Milestone 1 architecture for the ATA-01 ₹10,000 paper pilot

- Status: accepted for milestone 1 (paper only)
- Date: 2026-10-30
- Context: `ATA01_Claude_Code_10K_Pilot_Spec.md` v1.1 and `CLAUDE.md`

## Context

ATA-01 must run paper-first, cap the pilot at ₹10,000, enforce hard risk limits that the AI layer cannot change, survive restarts with persistent lockouts, and never place a live order until broker onboarding, compliance and owner activation are done. Paytm Money's API behaviour for this account is unverified.

## Decisions

1. **Modular monolith for paper; split later.** One Python 3.12 package (`src/ata`) with strict module boundaries: `ai` never imports `brokers`/`execution`; `risk` depends only on models, limits, state and costs. When live work starts, `execution` + the broker adapter move into an isolated service that is the only holder of credentials (spec §3, §13).
2. **Decimal for every price and rupee.** Floats are used only inside technical features. pandas/numpy are deferred to the backtest milestone.
3. **Mandate ceilings live in code; YAML can only tighten.** `risk/limits.py::MANDATE_CEILINGS` holds the ₹10k numbers. `config/defaults.yaml` is validated against them and loading a looser value raises `LimitViolation` (fail closed). A SHA-256 policy hash is written to the audit log at startup. Raising a ceiling is a code change under owner change-control.
4. **Percent limits shrink with equity, never grow past the rupee cap.** E.g. per-trade risk = min(₹50, 0.5% × equity).
5. **Persistent risk state.** High-water mark, daily start equity, daily freeze, drawdown lockout and kill switch live in the `risk_state` table. The lockout and kill switch survive restarts and are cleared only via `RiskState.owner_clear` (named human, written reason, `control_events` row). Non-human author names are refused.
6. **AI boundary is text in, text out.** The model sees an `AISnapshot` (no balances, limits, credentials or order controls) and must return an `AIDecision` with `extra="forbid"`. A semantic policy then rejects unknown evidence, sells/shorts, widened stops, longer horizons, unapproved strategies, stale or pre-dated rationale. The AI can only rank and weight candidates produced by deterministic, versioned strategies. Milestone 1 ships an offline deterministic ranker; a hosted LLM client implements the same `propose()` signature later.
7. **Independent risk gateway with full rule trace.** Every rule (environment, permissions, instrument, sizing caps, costs) is evaluated and recorded, pass or fail. Sizing follows spec §6 exactly and never rounds up.
8. **OMS state machine with no blind retries.** Client order IDs are deterministic hashes of the intent key, so a replay is refused as a duplicate. Timeouts/connectivity errors move an order to `UNKNOWN`, halt new entries, and are resolved only by querying the broker; `UNKNOWN → SUBMITTED` does not exist. An order absent at the broker is closed only after two consecutive checks.
9. **Broker truth supersedes the ledger.** Reconciliation (cash, positions, unrecognised broker orders) runs pre-open and end-of-day; any discrepancy or unreachable broker halts entries and raises a CRITICAL alert.
10. **Protective stops are broker-side stop-limits sized to confirmed filled quantity**, re-synced after every fill or partial. The gateway rejects entries whose stop-limit would fall outside the daily price band (found in simulation: such fills would otherwise be unprotected). Stops are not treated as guaranteed exits; gaps are simulated.
11. **Append-only, hash-chained audit log** (`audit_log`), verifiable with `Store.verify_audit_chain()`. SQLite by default; Postgres 16 via `ATA_DATABASE_URL` (verified locally with Postgres 16).
12. **Paytm Money adapter is a fail-closed stub** with no endpoints, URLs, auth flow or fee assumptions. A test asserts the module contains none.
13. **Costs are configuration flagged `verified: false`.** Edge checks and sizing use them, and the review report prints that they are placeholders.

## Consequences and open items

- Per-trade "risk" follows the spec formula (stop distance × qty). With the placeholder fee schedule a round trip on a small position costs about ₹65, so a stopped-out trade loses roughly ₹110, more than double the ₹50 budget. Whether the ₹50 budget should include costs is an owner decision (see PLAN.md).
- Ledger and OMS state are in memory in milestone 1; a restart rebuilds from broker truth. The full relational schema of spec §11 with Alembic migrations is milestone 2.
- Deferred: FastAPI health/dashboard, durable scheduler, Telegram/email alerts, real data feeds and instrument master, walk-forward backtester (Gate 2).
