"""Independent pre-trade risk gateway.

Every rule is evaluated and recorded (pass or fail) so the audit log shows
exactly why an intent was approved or skipped. An intent is approved only if
all rules pass. The gateway reads limits from RiskLimits and state from
RiskState; nothing in an intent can change either.

Sizing (spec section 6):
  qty = floor(min(risk_budget / (entry - stop), symbol_room / entry,
                  deployable_cash / entry, liquidity_cap)), rounded down to lot.
Quantities are never inflated to satisfy a signal: qty < 1 lot means skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from pydantic import BaseModel, ConfigDict

from ata.backtest.costs import CostModel
from ata.data.instruments import Instrument
from ata.data.validators import DataCheck
from ata.execution.stops import stop_limit_price
from ata.models import InstrumentStatus, Product, Quote, Side, TradeIntent
from ata.money import ZERO, D, ceil_to_tick, inr
from ata.risk.limits import RiskLimits


class RuleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    passed: bool
    detail: str


class RiskDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_key: str
    symbol: str
    approved: bool
    qty: int
    limit_price: Decimal
    est_stop_risk: Decimal
    est_round_trip_costs: Decimal
    est_gross_edge: Decimal
    rules: tuple[RuleResult, ...]

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.passed]

    @property
    def skip_reason(self) -> str:
        return "; ".join(f"{r.rule}: {r.detail}" for r in self.failed)


@dataclass
class PortfolioView:
    """Point-in-time view used by the gateway. Pending entries count as exposure."""

    equity: Decimal
    cash: Decimal
    position_cost: dict[str, Decimal]  # symbol -> cost basis of held qty
    position_marks: dict[str, Decimal]  # symbol -> market value of held qty
    open_risk: Decimal
    pending_entry_notional: dict[str, Decimal] = field(default_factory=dict)
    pending_entry_risk: Decimal = ZERO

    @property
    def symbols(self) -> set[str]:
        return {s for s, v in self.position_marks.items() if v > 0} | {s for s, v in self.pending_entry_notional.items() if v > 0}

    @property
    def gross(self) -> Decimal:
        return sum(self.position_marks.values(), ZERO) + sum(self.pending_entry_notional.values(), ZERO)


@dataclass
class GateContext:
    now: datetime
    day: date
    market_open: bool
    trading_day: bool
    live_mode: bool
    broker_session_valid: bool
    reconciled: bool
    entries_halted: tuple[str, ...]
    kill_switch: bool
    drawdown_lockout: bool
    daily_freeze: bool
    data_check: DataCheck
    quote: Quote | None
    prev_volume: int
    instrument: Instrument | None


def _r(rule: str, ok: bool, detail: str) -> RuleResult:
    return RuleResult(rule=rule, passed=bool(ok), detail=detail)


class RiskGateway:
    def __init__(self, limits: RiskLimits, costs: CostModel) -> None:
        self.limits = limits
        self.costs = costs

    def size(self, intent: TradeIntent, entry: Decimal, view: PortfolioView, prev_volume: int, lot: int) -> tuple[int, dict[str, Decimal]]:
        L = self.limits
        per_share_risk = entry - intent.stop_price
        risk_budget = min(L.per_trade_risk(view.equity), L.aggregate_risk(view.equity) - view.open_risk - view.pending_entry_risk)
        symbol_room = (
            L.per_symbol_cap_inr - view.position_cost.get(intent.symbol, ZERO) - view.pending_entry_notional.get(intent.symbol, ZERO)
        )
        pending = sum(view.pending_entry_notional.values(), ZERO)
        deployable = min(
            view.cash - pending - L.min_cash_buffer_inr,
            L.gross_invested_cap_inr - view.gross,
            L.bot_capital_cap_inr - view.gross,
        )
        caps = {
            "risk": risk_budget / per_share_risk if per_share_risk > 0 else ZERO,
            "symbol": symbol_room / entry,
            "cash": deployable / entry,
            "liquidity": D(prev_volume) * L.max_participation_pct,
        }
        raw = max(ZERO, min(caps.values()))
        qty = int(raw.to_integral_value(rounding=ROUND_DOWN)) // lot * lot
        # Costs are paid in cash: shrink until notional + buy-leg fees fit deployable cash.
        while qty > 0 and entry * qty + self.costs.leg(Side.BUY, qty, entry).total > deployable:
            qty -= lot
        return qty, caps

    def evaluate(self, intent: TradeIntent, view: PortfolioView, ctx: GateContext) -> RiskDecision:
        L = self.limits
        rules: list[RuleResult] = []
        inst, quote = ctx.instrument, ctx.quote

        # --- environment / fail-closed gates ---
        rules.append(_r("mode", not ctx.live_mode, "paper mode" if not ctx.live_mode else "live mode not authorised in this build"))
        rules.append(_r("kill_switch", not ctx.kill_switch, "kill switch engaged" if ctx.kill_switch else "off"))
        rules.append(
            _r(
                "drawdown_lockout",
                not ctx.drawdown_lockout,
                "persistent lockout active; owner must clear" if ctx.drawdown_lockout else "clear",
            )
        )
        rules.append(_r("daily_loss_freeze", not ctx.daily_freeze, "daily loss freeze active" if ctx.daily_freeze else "clear"))
        rules.append(_r("broker_session", ctx.broker_session_valid, "valid" if ctx.broker_session_valid else "session invalid/expired"))
        rules.append(_r("reconciled", ctx.reconciled, "broker and ledger agree" if ctx.reconciled else "unreconciled state"))
        rules.append(_r("oms_entries", not ctx.entries_halted, ", ".join(ctx.entries_halted) or "open"))
        rules.append(_r("trading_day", ctx.trading_day, "trading day" if ctx.trading_day else "market holiday/weekend"))
        rules.append(_r("market_hours", ctx.market_open, "open" if ctx.market_open else "outside session"))
        rules.append(_r("data_quality", ctx.data_check.ok, ctx.data_check.reason or "fresh and consistent"))

        # --- instrument / permission gates ---
        rules.append(_r("side", intent.side is Side.BUY, "long entries only (no shorts)" if intent.side is not Side.BUY else "BUY"))
        rules.append(_r("segment", intent.segment in L.permitted_segments, str(intent.segment)))
        rules.append(_r("product", intent.product in L.permitted_products and intent.product is Product.DELIVERY, str(intent.product)))
        known = inst is not None and inst.symbol == intent.symbol and inst.exchange == intent.exchange and inst.segment == intent.segment
        rules.append(_r("instrument_metadata", known, "present" if known else "missing/mismatched instrument metadata"))
        status_ok = inst is not None and inst.status is InstrumentStatus.ACTIVE
        rules.append(_r("instrument_status", status_ok, str(inst.status) if inst else "unknown"))
        rules.append(_r("quote", quote is not None, "available" if quote else "no quote"))

        qty, entry, est_risk, costs, edge = 0, ZERO, ZERO, ZERO, ZERO
        if inst is not None and quote is not None:
            entry = ceil_to_tick(quote.ask, inst.tick)
            lo, hi = inst.band(quote.prev_close)
            rules.append(_r("price_band", lo <= entry <= hi, f"entry {entry} vs band [{lo:.2f}, {hi:.2f}]"))
            rules.append(_r("spread", quote.spread_bps <= L.max_spread_bps, f"{quote.spread_bps:.1f}bps (max {L.max_spread_bps})"))
            stop_ok = (
                ZERO < intent.stop_price < entry
                and (entry - intent.stop_price) / entry <= L.max_stop_distance_pct
                and entry - intent.stop_price >= inst.tick
            )
            rules.append(_r("stop_distance", stop_ok, f"entry {entry} stop {intent.stop_price}"))
            # The protective stop-limit must itself be placeable inside today's band, or the fill would be unprotected.
            stop_limit = stop_limit_price(intent.stop_price, inst.tick)
            rules.append(_r("stop_placeable", stop_limit >= lo, f"stop-limit {stop_limit} vs band low {lo:.2f}"))
            rules.append(_r("target", intent.target_price > entry, f"target {intent.target_price} vs entry {entry}"))

            if stop_ok:
                qty, caps = self.size(intent, entry, view, ctx.prev_volume, inst.lot)
                binding = min(caps, key=lambda k: caps[k])
                rules.append(
                    _r(
                        "min_quantity",
                        qty >= inst.lot,
                        f"qty {qty} (binding cap: {binding}; caps: " + ", ".join(f"{k}={v:.2f}" for k, v in caps.items()) + ")",
                    )
                )
            else:
                rules.append(_r("min_quantity", False, "cannot size without a valid stop"))

            if qty > 0:
                notional = entry * qty
                est_risk = inr((entry - intent.stop_price) * qty)
                costs = self.costs.round_trip(qty, entry, intent.target_price)
                edge = inr((intent.target_price - entry) * qty)
                new_symbol = intent.symbol not in view.symbols
                held = view.position_cost.get(intent.symbol, ZERO) + view.pending_entry_notional.get(intent.symbol, ZERO)
                # Explicit post-trade checks, independent of the sizing arithmetic above.
                rules += [
                    _r("per_trade_risk", est_risk <= L.per_trade_risk(view.equity), f"{est_risk} <= {L.per_trade_risk(view.equity)}"),
                    _r(
                        "aggregate_risk",
                        view.open_risk + view.pending_entry_risk + est_risk <= L.aggregate_risk(view.equity),
                        f"{view.open_risk + view.pending_entry_risk + est_risk} <= {L.aggregate_risk(view.equity)}",
                    ),
                    _r("per_symbol_cap", held + notional <= L.per_symbol_cap_inr, f"{held + notional:.2f} <= {L.per_symbol_cap_inr}"),
                    _r(
                        "gross_cap",
                        view.gross + notional <= L.gross_invested_cap_inr,
                        f"{view.gross + notional:.2f} <= {L.gross_invested_cap_inr}",
                    ),
                    _r(
                        "bot_capital_cap",
                        view.gross + notional <= L.bot_capital_cap_inr,
                        f"{view.gross + notional:.2f} <= {L.bot_capital_cap_inr}",
                    ),
                    _r(
                        "cash_buffer",
                        view.cash - sum(view.pending_entry_notional.values(), ZERO) - notional - self.costs.leg(Side.BUY, qty, entry).total
                        >= L.min_cash_buffer_inr,
                        f"cash after {view.cash - notional:.2f} >= {L.min_cash_buffer_inr}",
                    ),
                    _r(
                        "max_positions",
                        not new_symbol or len(view.symbols) < L.max_positions,
                        f"{len(view.symbols)} held/pending (max {L.max_positions})",
                    ),
                    _r(
                        "after_cost_edge",
                        edge - costs > 0 and edge >= costs * L.min_edge_to_cost_ratio,
                        f"gross edge {edge} vs est. costs {costs} (min ratio {L.min_edge_to_cost_ratio})",
                    ),
                ]
        approved = all(r.passed for r in rules) and qty > 0
        return RiskDecision(
            intent_key=intent.unique_key,
            symbol=intent.symbol,
            approved=approved,
            qty=qty if approved else 0,
            limit_price=entry,
            est_stop_risk=est_risk,
            est_round_trip_costs=costs,
            est_gross_edge=edge,
            rules=tuple(rules),
        )
