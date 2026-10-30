# Risk policy: ₹10,000 pilot

Source of truth: `src/ata/risk/limits.py` (ceilings) and `config/defaults.yaml` (active, tighten-only). Print the active policy with `uv run ata limits`.

| Limit | Value | Enforced in | Test |
|---|---|---|---|
| Starting capital / bot cap | ₹10,000 | `RiskLimits`, gateway `bot_capital_cap` | `test_limits.py`, `test_account_cash_above_pilot_cap_is_not_used` |
| Per-trade estimated stop risk | min(₹50, 0.5% equity) | sizing + `per_trade_risk` rule | `test_per_trade_risk_budget_inr_50` |
| Aggregate open stop risk | min(₹100, 1% equity), incl. pending entries | sizing + `aggregate_risk` rule | `test_aggregate_risk_*` |
| Per symbol | ₹2,500 cost basis incl. pending | sizing + `per_symbol_cap` | `test_per_symbol_cap_*` |
| Gross invested | ₹7,500 | sizing + `gross_cap` | `test_gross_invested_cap_inr_7500` |
| Cash buffer | ≥ ₹2,500 after notional and buy fees | sizing + `cash_buffer` | `test_cash_buffer_inr_2500` |
| Concurrent positions | 3 (pending entries count) | `max_positions` | `test_max_three_positions` |
| Daily new-entry freeze | loss ≥ min(₹150, 1.5% day-start equity); resets each trading day | `RiskState.on_equity` | `test_daily_freeze_at_inr_150` |
| Drawdown lockout | ≥ min(₹500, 5% HWM) from high-water mark; persistent; owner-only clear | `RiskState` | `test_drawdown_lockout_*`, `test_lockout_survives_restart` |
| Orders per second | 1 | `OrderManager` throttle | `test_rate_limit_*` |
| Products / segments | DELIVERY; EQ and ETF only. No leverage, shorts, F&O, fractional shares | limits validator + gateway + paper broker | `test_no_*` |
| Minimum quantity | whole lots only; qty < 1 lot means skip, never round up | sizing | `test_single_share_above_symbol_cap_is_skipped` |
| After-cost edge | gross target edge ≥ 1.5 × estimated round-trip costs | `after_cost_edge` | `test_after_cost_edge_*` |
| Data | quote ≤ 15 min old, spread ≤ 60 bps, valid OHLC history | validators + `data_quality` | `test_quote_validation` |
| Instrument | ACTIVE only (not ASM/GSM/suspended/restricted), metadata present, inside price band, stop-limit placeable in band | gateway | `test_restricted_instruments` etc. |
| Liquidity | ≤ 1% of prior day volume | sizing | `test_liquidity_cap` |

Fail-closed gates that block every new entry: live mode, kill switch, lockout, daily freeze, invalid broker session, unreconciled state, any order in `UNKNOWN`, holiday/weekend, outside 09:15–15:30 IST, bad data, missing quote.

Exits, protective stops and alerts keep running while entries are blocked.

These limits are triggers and budgets, not maximum losses. Gaps, partial fills and costs can make a realised loss exceed them.

## Clearing a lockout (owner only)

```
uv run ata status
uv run ata owner-clear drawdown_lockout --author "Sourav Patra" \
  --reason "30-day review done; resuming paper" --equity <verified equity> --confirm "CLEAR DRAWDOWN_LOCKOUT"
```

This rebases the high-water mark to the verified equity and writes a `control_events` row.
