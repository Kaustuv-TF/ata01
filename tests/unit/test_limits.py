"""The mandate: every INR 10,000 limit is loaded, and nothing can loosen it."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from ata.money import D
from ata.risk.limits import MANDATE_CEILINGS, LimitViolation, RiskLimits, load_limits
from tests.conftest import CONFIG


def test_default_policy_matches_mandate(limits: RiskLimits) -> None:
    assert limits.initial_capital_inr == D(10000)
    assert limits.bot_capital_cap_inr == D(10000)
    assert limits.per_trade_risk_inr == D(50)
    assert limits.aggregate_risk_inr == D(100)
    assert limits.per_symbol_cap_inr == D(2500)
    assert limits.gross_invested_cap_inr == D(7500)
    assert limits.min_cash_buffer_inr == D(2500)
    assert limits.max_positions == 3
    assert limits.daily_loss_freeze_inr == D(150)
    assert limits.drawdown_lockout_inr == D(500)
    assert limits.max_orders_per_second == 1
    assert {s.value for s in limits.permitted_segments} == {"EQ", "ETF"}
    assert {p.value for p in limits.permitted_products} == {"DELIVERY"}


@pytest.mark.parametrize("field", sorted(MANDATE_CEILINGS))
def test_loosening_any_ceiling_is_rejected(limits: RiskLimits, field: str) -> None:
    data = limits.model_dump()
    data[field] = MANDATE_CEILINGS[field] * 2
    with pytest.raises(LimitViolation):
        RiskLimits.model_validate(data)


def test_tightening_is_allowed(limits: RiskLimits) -> None:
    data = limits.model_dump() | {"per_trade_risk_inr": D(25), "max_positions": 2}
    tighter = RiskLimits.model_validate(data)
    assert tighter.per_trade_risk_inr == D(25)


def test_cash_buffer_floor(limits: RiskLimits) -> None:
    with pytest.raises(LimitViolation):
        RiskLimits.model_validate(limits.model_dump() | {"min_cash_buffer_inr": D(1000)})


@pytest.mark.parametrize("segment", ["FUT", "OPT"])
def test_derivatives_cannot_be_enabled(limits: RiskLimits, segment: str) -> None:
    with pytest.raises(LimitViolation):
        RiskLimits.model_validate(limits.model_dump() | {"permitted_segments": {"EQ", segment}})


def test_leveraged_products_cannot_be_enabled(limits: RiskLimits) -> None:
    with pytest.raises(LimitViolation):
        RiskLimits.model_validate(limits.model_dump() | {"permitted_products": {"DELIVERY", "MARGIN"}})


def test_limits_are_immutable(limits: RiskLimits) -> None:
    with pytest.raises(ValidationError):
        limits.per_trade_risk_inr = D(5000)  # type: ignore[misc]


def test_yaml_with_looser_value_fails_closed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    raw = yaml.safe_load((CONFIG / "defaults.yaml").read_text())
    raw["risk"]["bot_capital_cap_inr"] = 100000
    p = tmp_path / "loose.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(LimitViolation):
        load_limits(p)


def test_percentage_limits_shrink_with_equity(limits: RiskLimits) -> None:
    assert limits.per_trade_risk(D(10000)) == D(50)
    assert limits.per_trade_risk(D(8000)) == D(40)
    assert limits.per_trade_risk(D(20000)) == D(50)  # INR cap still binds if equity grows
    assert limits.aggregate_risk(D(9000)) == D(90)
    assert limits.daily_loss_freeze(D(10000)) == D(150)
    assert limits.drawdown_lockout(D(12000)) == D(500)


def test_policy_hash_is_stable(limits: RiskLimits) -> None:
    assert limits.policy_hash() == load_limits(CONFIG / "defaults.yaml")[0].policy_hash()
