"""Every numerical limit and fail-closed gate in the pre-trade risk gateway."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ata.backtest.costs import CostModel
from ata.data.validators import DataCheck
from ata.models import InstrumentStatus, Segment, Side
from ata.money import D
from ata.risk.limits import RiskLimits
from ata.risk.rules import PortfolioView, RiskDecision, RiskGateway
from tests.conftest import NOW, empty_view, make_ctx, make_instrument, make_intent, make_quote


def failed(decision: RiskDecision) -> set[str]:
    return {r.rule for r in decision.failed}


@pytest.fixture
def gw(limits: RiskLimits, zero_costs: CostModel) -> RiskGateway:
    return RiskGateway(limits, zero_costs)


def test_happy_path_records_every_rule(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx())
    assert d.approved, d.skip_reason
    names = {r.rule for r in d.rules}
    for rule in (
        "per_trade_risk",
        "aggregate_risk",
        "per_symbol_cap",
        "gross_cap",
        "bot_capital_cap",
        "cash_buffer",
        "max_positions",
        "after_cost_edge",
        "drawdown_lockout",
        "daily_loss_freeze",
        "kill_switch",
        "data_quality",
    ):
        assert rule in names


# ---- INR 50 per-trade risk ----
def test_per_trade_risk_budget_inr_50(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx())
    per_share = D("100.05") - D(95)
    assert d.qty == 9 and d.est_stop_risk == per_share * 9
    assert d.est_stop_risk <= 50 < per_share * (d.qty + 1)


def test_per_trade_risk_shrinks_with_equity(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(equity="8000"), make_ctx())
    assert d.est_stop_risk <= 40 and d.qty == 7


# ---- INR 100 aggregate risk ----
def test_aggregate_risk_budget_limits_new_trade(gw: RiskGateway) -> None:
    view = PortfolioView(equity=D(10000), cash=D(8000), position_cost={"A": D(1000)}, position_marks={"A": D(1000)}, open_risk=D(80))
    d = gw.evaluate(make_intent(stop="95"), view, make_ctx())
    assert d.approved and d.qty == 3  # remaining budget 20 / 5.05


def test_aggregate_risk_exhausted_rejects(gw: RiskGateway) -> None:
    view = PortfolioView(equity=D(10000), cash=D(8000), position_cost={"A": D(1000)}, position_marks={"A": D(1000)}, open_risk=D(100))
    d = gw.evaluate(make_intent(stop="95"), view, make_ctx())
    assert not d.approved and "min_quantity" in failed(d)


def test_pending_entry_risk_counts_toward_aggregate(gw: RiskGateway) -> None:
    view = empty_view()
    view.pending_entry_notional = {"B": D(500)}
    view.pending_entry_risk = D(95)
    d = gw.evaluate(make_intent(stop="95"), view, make_ctx())
    assert d.qty == 0 or d.est_stop_risk <= 5


# ---- INR 2,500 per symbol ----
def test_per_symbol_cap_inr_2500(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="99.5"), empty_view(), make_ctx())
    assert d.approved and d.qty == 24 and d.qty * d.limit_price <= 2500


def test_per_symbol_cap_counts_existing_holding(gw: RiskGateway) -> None:
    view = PortfolioView(equity=D(10000), cash=D(8000), position_cost={"TEST": D(2000)}, position_marks={"TEST": D(2000)}, open_risk=D(0))
    d = gw.evaluate(make_intent(stop="99.5"), view, make_ctx())
    assert d.qty == 4 and D(2000) + d.qty * d.limit_price <= 2500


def test_single_share_above_symbol_cap_is_skipped(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(ref="3000", stop="2950", target="3300"), empty_view(), make_ctx(quote=make_quote("3000")))
    assert not d.approved and "min_quantity" in failed(d) and d.qty == 0


def test_minimum_lot_above_cap_is_skipped(gw: RiskGateway) -> None:
    inst = make_instrument("LOT5", lot=5)
    d = gw.evaluate(
        make_intent("LOT5", ref="600", stop="590", target="700"), empty_view(), make_ctx(inst, make_quote("600", symbol="LOT5"))
    )
    assert not d.approved and "min_quantity" in failed(d)


# ---- INR 7,500 gross, INR 2,500 cash buffer, INR 10,000 bot cap ----
def test_gross_invested_cap_inr_7500(gw: RiskGateway) -> None:
    view = PortfolioView(
        equity=D(10000),
        cash=D(3000) + D(2500) * 2,
        position_cost={"A": D(3500), "B": D(3500)},
        position_marks={"A": D(3500), "B": D(3500)},
        open_risk=D(0),
    )
    d = gw.evaluate(make_intent(stop="99.5"), view, make_ctx())
    assert d.approved and D(7000) + d.qty * d.limit_price <= 7500 and d.qty == 4


def test_cash_buffer_inr_2500(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="99.5"), empty_view(equity="10000", cash="3000"), make_ctx())
    assert d.approved and D(3000) - d.qty * d.limit_price >= 2500


def test_no_cash_above_buffer_rejects(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="99.5"), empty_view(equity="10000", cash="2500"), make_ctx())
    assert not d.approved


def test_account_cash_above_pilot_cap_is_not_used(gw: RiskGateway) -> None:
    """A broker account with INR 1 lakh still only deploys within the INR 10k mandate caps."""
    view = PortfolioView(
        equity=D(100000),
        cash=D(95000),
        position_cost={"A": D(2500), "B": D(2500)},
        position_marks={"A": D(2500), "B": D(2500)},
        open_risk=D(0),
    )
    d = gw.evaluate(make_intent(stop="99.5"), view, make_ctx())
    assert d.est_stop_risk <= 50
    assert D(5000) + d.qty * d.limit_price <= 7500


# ---- 3 positions ----
def test_max_three_positions(gw: RiskGateway) -> None:
    held = {s: D(500) for s in ("A", "B", "C")}
    view = PortfolioView(equity=D(10000), cash=D(8500), position_cost=held, position_marks=dict(held), open_risk=D(0))
    d = gw.evaluate(make_intent(stop="95"), view, make_ctx())
    assert not d.approved and failed(d) == {"max_positions"}


def test_adding_to_held_symbol_does_not_count_as_new_position(gw: RiskGateway) -> None:
    held = {s: D(500) for s in ("A", "B", "TEST")}
    view = PortfolioView(equity=D(10000), cash=D(8500), position_cost=held, position_marks=dict(held), open_risk=D(0))
    assert gw.evaluate(make_intent(stop="95"), view, make_ctx()).approved


def test_pending_entries_count_as_positions(gw: RiskGateway) -> None:
    held = {s: D(500) for s in ("A", "B")}
    view = PortfolioView(
        equity=D(10000), cash=D(9000), position_cost=held, position_marks=dict(held), open_risk=D(0), pending_entry_notional={"C": D(300)}
    )
    assert "max_positions" in failed(gw.evaluate(make_intent(stop="95"), view, make_ctx()))


# ---- persistent and daily blocks ----
@pytest.mark.parametrize(
    "flag,rule", [("daily_freeze", "daily_loss_freeze"), ("drawdown_lockout", "drawdown_lockout"), ("kill_switch", "kill_switch")]
)
def test_risk_blocks_reject(gw: RiskGateway, flag: str, rule: str) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(**{flag: True}))
    assert not d.approved and rule in failed(d)


# ---- costs ----
def test_after_cost_edge_rejects_thin_target(limits: RiskLimits, costs: CostModel) -> None:
    d = RiskGateway(limits, costs).evaluate(make_intent(stop="95", target="103"), empty_view(), make_ctx())
    assert not d.approved and "after_cost_edge" in failed(d)
    assert d.est_round_trip_costs > 0


def test_after_cost_edge_passes_wide_target(limits: RiskLimits, costs: CostModel) -> None:
    d = RiskGateway(limits, costs).evaluate(make_intent(stop="95", target="130"), empty_view(), make_ctx())
    assert d.approved, d.skip_reason


def test_costs_are_reserved_from_deployable_cash(limits: RiskLimits, costs: CostModel) -> None:
    d = RiskGateway(limits, costs).evaluate(make_intent(stop="99.5", target="130"), empty_view(cash="3000"), make_ctx())
    buy_fees = costs.leg(Side.BUY, d.qty, d.limit_price).total
    assert D(3000) - d.qty * d.limit_price - buy_fees >= 2500


# ---- permissions and instrument gates ----
def test_no_short_selling(gw: RiskGateway) -> None:
    assert "side" in failed(gw.evaluate(make_intent(side=Side.SELL), empty_view(), make_ctx()))


@pytest.mark.parametrize("segment", [Segment.FUT, Segment.OPT])
def test_no_derivatives(gw: RiskGateway, segment: Segment) -> None:
    assert "segment" in failed(gw.evaluate(make_intent(segment=segment, stop="95"), empty_view(), make_ctx()))


@pytest.mark.parametrize("status", [InstrumentStatus.ASM, InstrumentStatus.GSM, InstrumentStatus.SUSPENDED, InstrumentStatus.RESTRICTED])
def test_restricted_instruments(gw: RiskGateway, status: InstrumentStatus) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(make_instrument(status=status)))
    assert "instrument_status" in failed(d)


def test_missing_instrument_metadata(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(instrument=None))
    assert not d.approved


def test_mismatched_instrument_metadata(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(make_instrument("OTHER")))
    assert "instrument_metadata" in failed(d)


def test_price_band(gw: RiskGateway) -> None:
    q = make_quote("100").model_copy(update={"prev_close": D(80)})
    assert "price_band" in failed(gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(quote=q)))


def test_wide_spread(gw: RiskGateway) -> None:
    assert "spread" in failed(gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(quote=make_quote(spread_bps=200))))


@pytest.mark.parametrize("stop", ["100.05", "101", "85", "0.01"])
def test_invalid_stop_distance(gw: RiskGateway, stop: str) -> None:
    d = gw.evaluate(make_intent(stop=stop), empty_view(), make_ctx())
    assert not d.approved and "stop_distance" in failed(d)


def test_stop_must_be_placeable_inside_band(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="94"), empty_view(), make_ctx(make_instrument(band="0.05")))
    assert "stop_placeable" in failed(d)


def test_liquidity_cap(gw: RiskGateway) -> None:
    d = gw.evaluate(make_intent(stop="99.5"), empty_view(), make_ctx(prev_volume=500))
    assert d.qty == 5


def test_target_below_entry(gw: RiskGateway) -> None:
    assert "target" in failed(gw.evaluate(make_intent(stop="95", target="100"), empty_view(), make_ctx()))


# ---- fail-closed environment ----
@pytest.mark.parametrize(
    "override,rule",
    [
        ({"broker_session_valid": False}, "broker_session"),
        ({"reconciled": False}, "reconciled"),
        ({"entries_halted": ("unknown order status",)}, "oms_entries"),
        ({"market_open": False}, "market_hours"),
        ({"trading_day": False}, "trading_day"),
        ({"live_mode": True}, "mode"),
        ({"data_check": DataCheck(ok=False, reason="stale")}, "data_quality"),
        ({"quote": None}, "quote"),
    ],
)
def test_fail_closed(gw: RiskGateway, override: dict[str, object], rule: str) -> None:
    ctx = make_ctx(**override)
    d = gw.evaluate(make_intent(stop="95"), empty_view(), ctx)
    assert not d.approved and rule in failed(d)


def test_stale_quote_flows_through_validator(gw: RiskGateway, limits: RiskLimits) -> None:
    from ata.data.validators import validate_quote

    q = make_quote(ts=NOW - timedelta(hours=1))
    check = validate_quote(q, NOW, timedelta(minutes=limits.max_data_age_minutes), limits.max_spread_bps)
    assert not gw.evaluate(make_intent(stop="95"), empty_view(), make_ctx(quote=q, data_check=check)).approved
