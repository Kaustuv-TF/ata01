# ATA-01 — Autonomous AI Trading Agent
Version 1.1 — ₹10,000 Pilot | 30 October 2026 | Owner: Sourav Patra

## Executive design
Build an independent Python service, constructed with Claude Code, that selects among approved algorithmic strategies, generates trade intents, sizes and routes orders through an independent risk gateway, and executes via Paytm Money only after account-specific API and compliance approval. User reviews every 30 days; unattended operation is an engineering objective, not a broker-guaranteed capability.

## Non-negotiable principles
- The LLM does not hold credentials or invoke unrestricted broker endpoints.
- No model-generated executable code or rule changes are deployed live without offline validation and owner approval.
- No leverage, short sales, or F&O at launch (capital and lot/margin feasibility constraints make derivatives unsuitable for this pilot). Additional segments remain disabled until entitlement, margin, regulatory and instrument-specific risk gates pass.
- Broker truth supersedes internal portfolio records. Reconcile before orders and following every uncertain network response.
- Fail closed: stale data, unknown positions, expired session, disconnected broker, missing instrument metadata, breached limit or unknown exchange status blocks new orders.
- On kill switch, block new risk, cancel pending entry orders where possible, preserve broker-side protective orders, and initiate controlled closeout only if connectivity/order rules permit. Alert immediately.

## 1. Mandate
Trading mandate capital INR 10,000 (TEN THOUSAND RUPEES) for paper and subsequent small-live pilot. Do not deploy or assume INR 100,000. In live mode, verify actual account equity and isolate/restrict bot usable funds to INR 10,000 or less; a software cap alone does not segregate broker account funds. Higher capital requires explicit owner change-control and fresh validation. 30-day review cadence. Initial cash/ETF only; strategy universe may later expand to approved intraday equities, futures/options by explicit owner mandate. Hard maximum risk budget 0.5% of assigned account equity per trade (risk estimation, not guaranteed fill loss); max per instrument 25% (INR 2,500 at initial equity); target max gross invested 75% (INR 7,500); max concurrent positions 3; max total open portfolio estimated stop risk 1% (INR 100); daily new-entry freeze at -1.5% marked equity change (INR 150); new-trading lockout at 5% equity drawdown (INR 500) from high-water mark; position management and alerts continue. Restrict price-band, illiquid, suspended, ASM/GSM/restricted instruments per up-to-date broker/exchange lists. Use only broker-approved order types and segment permissions. No assumption that stop orders guarantee exits.

## 1A. ₹10,000 micro-capital pilot — binding V1.1 overrides
- **Capital policy**: use INR 10,000 as starting strategy/account notional; not INR 1 lakh. Set `INITIAL_CAPITAL_INR=10000`, `BOT_CAPITAL_CAP_INR=10000`, `LIVE_TRADING_ENABLED=false`, `MARKET_MODE=paper`, `REVIEW_CYCLE_DAYS=30`. Require a separate owner-controlled activation before any live orders and never infer permission from test config.
- **Per-trade estimated risk**: 0.5% = INR 50; **portfolio aggregate estimated risk**: 1% = INR 100; **daily stop-new-entry loss trigger**: 1.5% = INR 150; **peak-to-trough drawdown lockout**: 5% = INR 500. These are triggers/budgets, not maximum possible losses.
- **Position allocation**: <=INR 2,500 per symbol, <=INR 7,500 gross invested, >=INR 2,500 cash buffer, at most 3 positions. No leverage, margin financing, overnight short, futures or options, unless a later separately approved mandate explicitly changes permitted products. Do not imply derivatives are feasible with INR 10,000.
- **Minimum quantity feasibility**: with a fractional-share prohibition, order quantity is whole shares or exchange-prescribed units. If price, spread, brokerage/fees, minimum ticks, stop distance, or risk budget make quantity <1 or not economically viable, **skip trade**. Never inflate trade size to satisfy a signal. Compute costs **before** approval and record a skip reason.
- **P&L and costs**: account for applicable brokerage, STT, exchange and SEBI fees, GST, stamp duty, DP charges where applicable, bid/ask spread and slippage. Estimate breakeven after costs; skip low expected-edge orders. Check broker fee schedule; never hardcode unverified fees as actual.
- **Risk limit reset**: daily loss threshold resets each exchange trading day, subject to current equity; peak-equity drawdown remains persistent across the 30-day cycle and a restart. A lockout cannot be auto-cleared by the LLM or by process restart.
- **Owner review**: 30 days means owner review, not permission to ignore critical alerts; owner must be reachable for auth, compliance and unexpected uncovered positions. Monthly changes go through a signed configuration version; the agent cannot autonomously raise caps.
- **Capital isolation**: if other holdings or cash coexist in the same Paytm account, consider a dedicated account/subaccount if supported. In all cases reconcile the entire account and enforce the lower of actual available cash, reserved pilot balance and bot cap. A software cap does not physically ring-fence funds.

## 2. Broker feasibility
Public Paytm Money docs: REST API, order placement, order book, trade book, position detail, historical price API, order types for equity/derivatives; public documentation does not confirm user eligibility, session lifetime, 30-day autonomous authentication, price or 2026 retail-algo onboarding for this account. Confirm via openapi.care@paytmmoney.com: client-direct API support and onboarding, static IP/key mapping, 2FA/auth/session refresh, rate limits, real-time/historical data scope, supported product/order types, GTT/SL functionality, derivatives permissions, trading holidays and outage escalation, charges, sandbox test environment, response identifiers and postbacks. No live funds/order until confirmed.

Regulatory basis: SEBI Feb 2025 retail algo framework applicable to all brokers from Apr 1 2026; NSE client-direct API standards, tagged API orders, static-IP requirement for tech-savvy investor self-hosted routes, ~10 OPS threshold for strategy-registration distinction. Specific obligations depend on broker/segment/classification. Orders stay under configured max 1 order/s in V1 (not an exemption from all registration/approval). Broker approval and legal review first.

## 3. Architecture
market feed -> data validator -> feature store -> quant strategy catalog + LLM research -> strategy allocator -> trade intent -> independent risk/compliance gateway -> order management/reconciliation -> Paytm API -> exchange. Postgres stores snapshots, signals, intents, orders, fills, cash, positions, P&L, risks, AI rationales, overrides, audit events. Redis optional for queue; avoid complexity until needed. Scheduler with market calendar and timezone Asia/Kolkata. Separate risk process/permissions; execution credentials only in isolated service.

Modules:
- `data`: live feeds, instrument master, corporate actions, history, stale-data gate
- `strategies`: trend/momentum, mean-reversion, volatility breakout, event-filtered strategies
- `ai`: bounded market-regime categorization, filing/news analysis, rank approved candidates; JSON schema + provenance
- `allocator`: maximize risk-adjusted capital use within exposure constraints; deconflict signals
- `risk`: immutable limits, permissions, concentration, P&L/drawdown, market hours, slippage, liquidations and kill-switch
- `execution`: broker interface, idempotent client IDs, retry reconciliation, order-state machine, partial fill protection
- `monitor`: telemetry, heartbeats, alerts and auto suspension
- `reports`: 30-day reviews and reconciliation reports
- `backtest`: historical event-driven simulation, broker charges and taxes, benchmark and robustness

## 4. Strategy catalog and promotion
Candidate strategies:
A. Cash equity momentum (20–120-day trend, relative strength, liquidity)
B. Cash equity short-horizon mean reversion (event and trend filters)
C. Cash equity volatility breakout (ATR-adjusted position sizing)
D. Liquid ETFs trend or rotation
E. Intraday equities (future phase)
F. Defined-risk option structures only after derivative margin/lot/greek/expiry/intraday-risk feasibility verification (future phase)
G. Futures only after explicit leverage permission (future phase)
LLM may select and weight only approved strategies; it cannot write and hot-deploy a new strategy. Train/test splits, walk-forward, leakage prevention, survivorship bias checks, corporate-action adjustment, multiple-testing correction and slippage sensitivity required. Flat bench is preferable to unproven trading.

## 5. AI contract
LLM receives sanitized snapshot: timestamp, version, approved strategy list, data freshness, corporate events, regime factors, candidate signals with quant evidence. LLM returns schema: `{timestamp,model_version,market_regime,strategies:[{id,weight,justification}],trade_intents:[{symbol,exchange,segment,side,reference_price,stop_price,target_price,horizon,confidence,evidence_ids}],warnings}`. Validate JSON strict schema and semantic constraints; unknown symbols or stale rationale invalidate. Confidence is not assumed calibrated. LLM never adjusts hard risk numbers, source code, allowlists, live permissions, secrets or order instructions.

## 6. Trade intent to order policy
- Pre-flight reconcile cash, open orders, holdings/positions and active protective instructions with broker.
- Verify permissions, instrument, price band, max spread/depth, circuit limits, trading window, staleness, lot size, free funds and overlapping orders.
- Size `floor(min(per-trade-risk-budget / |entry-stop|, per-symbol-cap / entry, deployable-cash / entry, liquidity-based-cap))`; apply tick/lot rules; reject invalid stop distance or zero size.
- Use limit or supported stop-limit orders only, per current broker/exchange policy.
- Track states CREATED -> RISK_APPROVED -> SUBMITTED -> ACKNOWLEDGED -> PART_FILLED/FILLED or REJECTED/CANCELLED/UNKNOWN.
- On timeout: query broker order book/trades before retry. Never blindly resubmit.
- Place protective instruction after confirmed fill if supported; otherwise reduce exposure or halt depending risk policy. Verify stop active.
- Reconcile at start, after fills, after reconnect, and end of day. On discrepancy halt new orders, notify owner.

## 7. Failure-mode matrix
- Auth expired: pause new entries, do not bypass 2FA; owner alert; existing broker protections remain active where supported.
- Stale quotes: cancel pending entries if safe; do not trade stale quotes.
- Network/broker error: order status UNKNOWN; reconcile before retry.
- Partial fill: resize protection to confirmed filled quantity.
- Data provider disconnect: freeze new risk; verify existing protections.
- LLM unavailable/malformed output: no AI-generated entries, maintain existing deterministic risk management.
- Volatility spike/limit/circuit: avoid new positions; respect price bands and exchange restrictions.
- Intraday mandatory square-off: broker-specific scheduled flatten and verification, only when permissions enabled.
- Overnight gap: acknowledge stop may be skipped and loss exceed budget.
- Risk breach: freeze new entries; controlled reduce risk where execution possible.

## 8. Testing and deployment gates
Gate 0 Broker agreement: terms and permitted unattended API operation checked, regulatory classification confirmed, static IP mapped, no credentials shared with LLM.
Gate 1 Unit tests: >90% coverage on order state/risk critical flows; forbidden orders consistently rejected.
Gate 2 Historical: at least 3-5 years where data exists; realistic cash/fees/STT/exchange charges/GST/stamp duty/slippage; out-of-sample + walk-forward; trading-cost sensitivity; benchmark comparison.
Gate 3 Paper live: minimum 20 trading sessions with stable data/feed, order state machine, risk enforcement, logs; simulated adverse scenarios.
Gate 4 Tiny live: up to INR 10,000 only, hard kill switch, 20 sessions error-free execution and observed cost estimates, after broker permission and explicit live activation by owner.
Gate 5 Scale: do not automatically scale. After the 30-day review, owner may propose a separately authorized mandate change from INR 10,000; all existing limits remain until approval and retesting. No guarantee of positive return.
Never promote based solely on attractive backtested CAGR. Reject strategies with unstable parameters, concentrated P&L, insufficient independent trades, high turnover or poor net cost robustness.

## 9. Technical stack
Python 3.12+, `uv`, `pydantic`, `httpx`, `pandas`, `numpy`, `sqlalchemy`, `psycopg`, `FastAPI`, `apscheduler`/durable job runner, `pytest`, `ruff`, `mypy`, Docker Compose, Postgres 16, optional Redis, Prometheus/Grafana or simpler OpenTelemetry/Sentry, secrets via cloud secret manager. Cloud Linux VM in Indian region with static public egress IP, encrypted disk, SSH keys, firewall, NTP/IST time aware calendars, automatic patches. Use broker-supported official Python SDK or direct HTTP adapter after validating current endpoints. No browser automation to bypass session controls. Model provider with separate paid API key; ChatGPT Plus is separate from API billing.

## 10. Suggested repo
```
ata01/
  README.md
  docs/{PRD,ARCHITECTURE,API_BROKER,REGULATORY,RISK_POLICY,RUNBOOK,STRATEGIES,TEST_PLAN}.md
  config/{defaults.yaml,markets.yaml,instruments.yaml}
  src/ata/{app.py,settings.py}
  src/ata/data/{feeds.py,history.py,features.py,validators.py}
  src/ata/strategies/{base.py,momentum.py,mean_reversion.py,breakout.py,etf_rotation.py}
  src/ata/ai/{client.py,schemas.py,policy.py}
  src/ata/portfolio/{allocator.py,ledger.py}
  src/ata/risk/{rules.py,limits.py,kill_switch.py,compliance.py}
  src/ata/brokers/{base.py,paytmmoney.py,paper.py}
  src/ata/execution/{orders.py,reconciliation.py,stops.py}
  src/ata/backtest/{engine.py,costs.py,validation.py}
  src/ata/ops/{scheduler.py,alerts.py,health.py,reporting.py}
  migrations/
  tests/{unit,integration,property,chaos}/
  infra/{docker-compose.yml,Dockerfile}
  .env.example
  pyproject.toml
```

## 11. Database entities
`market_bars(symbol,venue,interval,ts,OHLCV,source,ingest_ts)`; `instruments(security_id,lot,tick,segment,status,asof)`; `strategy_versions(id,hash,status,approved_at)`; `signals(id,strategy,version,ts,symbol,side,features_hash)`; `ai_decisions(id,prompt_hash,model,output_json,evidence)`; `trade_intents(id,unique_key,decision_id,...)`; `risk_decisions(id,intent_id,rule,passed,reason,ts)`; `orders(client_order_id,broker_order_id,status,qty,filled_qty,...)`; `fills(trade_id,order_id,qty,price,fees,ts)`; `positions(symbol,segment,qty,avg_cost,asof)`; `portfolio_snapshots(ts,cash,equity,gross,drawdown)`; `control_events(ts,type,author,reason)`; `alerts(id,severity,acknowledged_at)`; `audit_log(ts,component,action,checksum)`.

## 12. Monitoring SLIs
Order reconciliation drift = zero tolerance; duplicate live orders = zero; session/auth health; data latency; unexpected API errors; pending unprotected exposure; portfolio exposure and drawdown; realized/unrealized P&L after charges; out-of-hours orders; model-output validation errors. Alerts via Telegram/email and local dashboard. Stop-on-repeated-critical-error. Reports daily and comprehensive every 30 days.

## 13. Security
Broker OAuth/API keys stored only in cloud secret manager, not git, logs, prompts or dashboard; least privilege; no withdrawal/transfer automation; read-only LLM tools; network egress allowlists; encrypted backups; 2FA and human login honoured; emergency revoke/key rotation; append-only audit. Never provide login password, OTP, MPIN or tokens to Claude/ChatGPT.

## 14. Development roadmap
Week 1: broker confirmation and account/API compliance, full data/spec, repository bootstrap.
Week 2: data adapter, market calendar, Postgres, paper broker, instrument master.
Week 3: strategies, event-driven backtest, cost/slippage modelling, evaluation scripts.
Week 4: AI schemas, regime agent, allocation, immutable independent risk engine.
Week 5: OMS, Paytm adapter (read-only then broker-supported test orders), idempotency, reconciliation and failure tests.
Week 6: deployment, monitoring, alerts, paper execution, operations runbook.
Then 20+ trading days of automated paper validation, followed by a <=INR 10,000 live pilot only after all gates and explicit owner activation. Expanded capital requires separate authorization. These are milestones rather than delivery guarantees.

## 15. Claude Code kickoff prompt
"Act as staff software architect and senior Python quant engineer. Read docs first. Implement ATA-01 as an auditable production-minded paper-trading-first application, not a profit-guaranteeing bot. Build independent broker and risk interfaces; maintain strict schema validation, broker reconciliation, immutable risk limits, and detailed unit/integration/chaos tests. Never place live orders without explicit owner authorization, CONFIG_LIVE_ENABLED and broker-compliance gate flags; default both false. All paper and live trade sizing assumes an INR 10,000 maximum pilot, INR 50 per-trade intended risk, and 3 positions maximum. Start with project skeleton, architecture ADRs, data model, Paytm Money API interface stubs and paper broker. Use Python 3.12, uv, Pydantic, FastAPI, PostgreSQL, SQLAlchemy, pytest and Docker. Do not invent undocumented broker behavior. Report incomplete dependencies and human broker onboarding steps."

## Sources verified 30 Oct 2026
- https://developer.paytmmoney.com/
- https://developer.paytmmoney.com/docs/api/order-book-api-filter-by-api-key/
- https://developer.paytmmoney.com/docs/api/historical-api-beta
- https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf
- https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail%20Algo_03112025_NSE.pdf
- https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf

## 16. First implementation acceptance criteria for the ₹10k mandate
1. `pytest` covers INR 10,000 capital cap, INR 2,500 symbol cap, INR 7,500 invested cap, INR 50 per-trade stop-risk budget, INR 100 aggregate stop-risk budget, 3-position cap, INR 150 daily freeze, and INR 500 drawdown lockout.
2. Reject an entry whose single share/required minimum tradable lot exceeds any sizing or capital cap.
3. Reject entry whenever estimated round-trip costs and minimum spread remove the projected after-cost edge.
4. Exercise crash/restart with persistent lockout; verify AI suggestions cannot change limits.
5. Require brokerage/account API onboarding proof; `paytmmoney.py` must not fabricate method names or endpoints.
6. Paper engine needs bid/ask assumptions, partial fills, rejection, timeout/reconciliation, price-band and market-holiday simulation.
7. Log every AI trade proposal, computed quantity, accepted/rejected risk rule, execution state and realized after-fee P&L.
8. Project must start in paper mode with mock/historical data and **no live credentials**, remain non-trading if data is unavailable, and produce a sample 30-day review report.
