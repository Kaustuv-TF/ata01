# CLAUDE.md — ATA-01: Autonomous AI Trading Pilot (₹10,000)

## Mandatory reading
First read `ATA01_Claude_Code_10K_Pilot_Spec.md` completely. That specification takes precedence over defaults or prior ₹1-lakh estimates.

## Mission
Build an independent, auditable, Python-based AI-assisted autonomous trading application capable of operating with a 30-calendar-day owner-review cycle. Broker: Paytm Money, subject to verified live API onboarding and exchange compliance. The model may choose among independently validated and whitelisted strategies; it may not modify risk controls, credentials or executable strategies while live.

## Hard constraints
- INITIAL_CAPITAL_INR=10000; BOT_CAPITAL_CAP_INR=10000; live trading OFF by default. No orders against a real account without separately confirmed owner permission and broker compliance.
- Intended risk <=₹50/trade; combined intended risk <=₹100; single instrument <=₹2,500; gross deployment <=₹7,500; at most 3 simultaneous positions.
- Freeze new entries after ₹150 daily portfolio loss; persistent lockout after ₹500 peak-equity drawdown; no automatic reset of persistent lockout. The loss controls are triggers not guarantees.
- Paper-first, cash equity/eligible ETF only in first pilot; no leverage, derivatives, short positions, fractional shares, or blind retries.
- Do not invent Paytm Money APIs, authentication behavior, costs or session duration. Put unverifiable interfaces behind adapters with TODO verification and no-op safe behavior.
- Do not ask user for passwords, MPIN, OTP or copy secrets into AI prompts or source.
- Ensure data staleness, broker mismatch, auth expiry, unknown order status and risk failures block new positions.

## First execution steps in Claude Code
1. Inspect repo; make a plan and an architecture decision record (ADR).
2. Bootstrap Python 3.12 (`uv`, `pytest`, `pydantic`, `sqlalchemy`, `fastapi`, PostgreSQL, Docker Compose).
3. Implement risk policy/config persistence, paper broker, OMS state machine, market-data validation and deterministic sample strategies.
4. Implement structured AI trade-intent interface with schema validation and read-only tool boundary.
5. Add tests for every numerical limit and failure case listed in the specification.
6. Provide demo CLI: run offline sample/backtest, paper-simulate 30 days, generate P&L and risk report.
7. Only then implement a read-only Paytm Money adapter against verified official API docs; require manual broker onboarding before live execution functionality is enabled.
8. Run tests and report exact commands, results and remaining external dependencies.

## Deliverable standards
Every feature has tests. No hardcoded credentials. Every order intent has immutable audit identifiers. Default run must never place real orders. If required data, APIs or broker permissions are missing, report blockers and complete the paper trading subsystem. Do not claim profitability or 30-day uninterrupted authentication.
