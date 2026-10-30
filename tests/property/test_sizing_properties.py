"""Property tests: for arbitrary prices, stops and portfolio states, an approved entry never breaches a cap."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from ata.backtest.costs import CostModel
from ata.models import Side
from ata.money import ZERO, D
from ata.risk.limits import load_limits
from ata.risk.rules import PortfolioView, RiskGateway
from tests.conftest import CONFIG, make_ctx, make_instrument, make_intent, make_quote

LIMITS = load_limits(CONFIG / "defaults.yaml")[0]
COSTS = CostModel.load(CONFIG / "costs.yaml")
GW = RiskGateway(LIMITS, COSTS)

prices = st.decimals(min_value=Decimal("1"), max_value=Decimal("5000"), places=2)


@settings(max_examples=400, deadline=None)
@given(
    price=prices,
    stop_frac=st.decimals(min_value=Decimal("0.001"), max_value=Decimal("0.15"), places=3),
    target_mult=st.decimals(min_value=Decimal("1.0"), max_value=Decimal("1.5"), places=3),
    cash=st.decimals(min_value=Decimal("0"), max_value=Decimal("50000"), places=2),
    held=st.lists(st.decimals(min_value=Decimal("0"), max_value=Decimal("2500"), places=2), max_size=4),
    open_risk=st.decimals(min_value=Decimal("0"), max_value=Decimal("120"), places=2),
    equity=st.decimals(min_value=Decimal("5000"), max_value=Decimal("100000"), places=2),
    volume=st.integers(min_value=0, max_value=10_000_000),
    lot=st.sampled_from([1, 1, 1, 5, 25]),
)
def test_approved_entries_never_breach_caps(
    price: Decimal,
    stop_frac: Decimal,
    target_mult: Decimal,
    cash: Decimal,
    held: list[Decimal],
    open_risk: Decimal,
    equity: Decimal,
    volume: int,
    lot: int,
) -> None:
    tick = D("0.05")
    price = (price / tick).quantize(Decimal(1)) * tick or tick
    stop = ((price * (1 - stop_frac)) / tick).quantize(Decimal(1)) * tick
    target = ((price * target_mult) / tick).quantize(Decimal(1)) * tick
    positions = {f"H{i}": v for i, v in enumerate(held)}
    view = PortfolioView(equity=equity, cash=cash, position_cost=positions, position_marks=dict(positions), open_risk=open_risk)
    inst = make_instrument(lot=lot, band="0.20")
    d = GW.evaluate(
        make_intent(ref=str(price), stop=str(stop), target=str(target)), view, make_ctx(inst, make_quote(price), prev_volume=volume)
    )
    if not d.approved:
        assert d.qty == 0
        return
    notional = d.qty * d.limit_price
    assert d.qty > 0 and d.qty % lot == 0
    assert d.est_stop_risk <= min(LIMITS.per_trade_risk_inr, LIMITS.per_trade_risk_pct * equity)
    assert open_risk + d.est_stop_risk <= min(LIMITS.aggregate_risk_inr, LIMITS.aggregate_risk_pct * equity)
    assert notional <= LIMITS.per_symbol_cap_inr
    assert sum(held, ZERO) + notional <= LIMITS.gross_invested_cap_inr
    assert cash - notional - COSTS.leg(Side.BUY, d.qty, d.limit_price).total >= LIMITS.min_cash_buffer_inr
    assert len([v for v in held if v > 0]) < LIMITS.max_positions
    assert d.qty <= volume * LIMITS.max_participation_pct
    assert d.est_gross_edge >= d.est_round_trip_costs * LIMITS.min_edge_to_cost_ratio
