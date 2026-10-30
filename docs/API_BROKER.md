# Paytm Money broker integration: status and onboarding checklist

**Status: not integrated.** `src/ata/brokers/paytmmoney.py` is a fail-closed stub. It contains no endpoints, URLs, auth flow, session duration or fees, and every call raises `LiveTradingDisabled`.

## What the owner must obtain (Gate 0)

Send to openapi.care@paytmmoney.com (spec §2) and keep the written replies in `docs/broker-evidence/`:

1. Is client-direct API access available for this account, and what is the onboarding process under the SEBI retail-algo framework (applicable from 1 Apr 2026)?
2. Static IP registration and API key mapping: how many IPs, how to change them.
3. Authentication: login/2FA flow, token lifetime, refresh behaviour. Can a session last unattended between owner logins? (ATA will never bypass 2FA.)
4. Rate limits per endpoint, and the order-per-second threshold that triggers strategy registration.
5. Data: real-time quote scope, historical API limits, instrument master download, corporate actions.
6. Order types and products for cash delivery: LIMIT, SL/SL-M, GTT, validity, after-market orders. Do stop-loss orders persist across sessions?
7. Order identifiers: can the client supply its own order tag/ID? Is there an idempotency key? Postbacks/webhooks?
8. Order book / trade book semantics after a timeout: how soon is a new order visible?
9. Full charge schedule for delivery equity and ETFs (brokerage, DP, any API fees).
10. Sandbox or test environment availability.
11. Trading holiday feed and outage escalation contacts.
12. Whether a dedicated account or sub-account is available to isolate the ₹10,000 pilot funds.

## What happens next (milestone after onboarding)

1. Implement a **read-only** adapter (funds, positions, order book, trade book, instrument master) against the verified official docs or SDK, citing each call to the doc page and date it was verified.
2. Run it in paper mode alongside the simulator to reconcile real account state.
3. Only after Gates 0–3: broker-supported test orders, then a separately approved owner activation for live.

Credentials will be held only by the isolated execution service via a cloud secret manager. They are never placed in git, `.env`, logs, prompts or this repository, and never shared with any AI model.
