# ATA-01: AI-assisted trading pilot (₹10,000, paper only)

An auditable, paper-first trading system for a ₹10,000 pilot. Deterministic strategies propose candidates, an AI layer may only rank them through a strict JSON contract, and an independent risk gateway sizes and approves every order against hard limits that nothing at runtime can loosen.

**This build cannot place real orders.** Live mode refuses to start, and the Paytm Money adapter is a stub until broker onboarding is verified (see `docs/API_BROKER.md`). Nothing here is a claim of profitability.

## Quick start

```bash
uv sync
uv run ata simulate --days 30            # offline paper run on synthetic data + review report
uv run ata simulate --days 30 --chaos -v # same, with timeouts, rejections, stale data, bad AI output, session expiry
uv run ata limits                        # active risk policy and mandate ceilings
uv run pytest --cov=ata                  # 204 tests
uv run ruff check src tests && uv run mypy
```

Postgres instead of SQLite: `uv sync --extra postgres` and set `ATA_DATABASE_URL=postgresql+psycopg://...`, or `docker compose -f infra/docker-compose.yml up --build`.

## Layout

```
config/            risk policy (tighten-only), cost estimates (unverified), sample instruments, calendar
src/ata/risk/      mandate ceilings, persistent risk state, pre-trade gateway
src/ata/execution/ OMS state machine, reconciliation, protective stops
src/ata/brokers/   broker protocol, paper broker, Paytm Money stub
src/ata/ai/        snapshot/decision schemas, semantic policy, offline ranker
src/ata/data/      validators, calendar, instruments, synthetic history, features
src/ata/strategies sample deterministic strategies (not validated)
src/ata/app.py     daily paper engine
docs/              PLAN, ADR, RISK_POLICY, API_BROKER, RUNBOOK
```

Start with `docs/PLAN.md` for status, acceptance-criteria mapping and owner decisions.
