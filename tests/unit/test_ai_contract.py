"""AI boundary: strict schema, semantic policy, and no path to limits, secrets or orders."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from ata.ai.client import DeterministicRanker, UnavailableClient
from ata.ai.policy import evaluate_ai_output
from ata.ai.schemas import AISnapshot, Candidate, StrategyInfo
from ata.data.calendar import IST
from ata.models import Exchange, Segment
from ata.money import D

TS = datetime(2026, 11, 3, 16, 0, tzinfo=IST)


def snapshot(fresh: bool = True) -> AISnapshot:
    return AISnapshot(
        timestamp=TS,
        data_as_of=TS - timedelta(minutes=30),
        data_fresh=fresh,
        approved_strategies=[
            StrategyInfo(id="momentum_v1", version="abc", description="m"),
            StrategyInfo(id="breakout_v1", version="def", description="b"),
        ],
        regime_factors={"breadth_above_sma50": "0.7"},
        candidates=[
            Candidate(
                evidence_id="momentum_v1:AAA:2026-11-03",
                strategy_id="momentum_v1",
                symbol="AAA",
                exchange=Exchange.NSE,
                segment=Segment.EQ,
                reference_price=D(100),
                stop_price=D(95),
                target_price=D(115),
                horizon_days=20,
                features={},
            ),
            Candidate(
                evidence_id="breakout_v1:BBB:2026-11-03",
                strategy_id="breakout_v1",
                symbol="BBB",
                exchange=Exchange.NSE,
                segment=Segment.EQ,
                reference_price=D(50),
                stop_price=D(48),
                target_price=D(56),
                horizon_days=10,
                features={},
            ),
        ],
    )


def decision(**over: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "timestamp": (TS + timedelta(seconds=5)).isoformat(),
        "model_version": "test",
        "market_regime": "trending_up",
        "strategies": [
            {"id": "momentum_v1", "weight": "0.6", "justification": "x"},
            {"id": "breakout_v1", "weight": "0.4", "justification": "y"},
        ],
        "trade_intents": [intent()],
        "warnings": [],
    }
    d.update(over)
    return d


def intent(**over: Any) -> dict[str, Any]:
    i: dict[str, Any] = {
        "symbol": "AAA",
        "exchange": "NSE",
        "segment": "EQ",
        "side": "BUY",
        "reference_price": "100",
        "stop_price": "96",
        "target_price": "112",
        "horizon": 15,
        "confidence": "0.7",
        "evidence_ids": ["momentum_v1:AAA:2026-11-03"],
    }
    i.update(over)
    return i


def run(d: dict[str, Any], snap: AISnapshot | None = None):  # type: ignore[no-untyped-def]
    return evaluate_ai_output(json.dumps(d), snap or snapshot())


def test_valid_decision_produces_intent() -> None:
    r = run(decision())
    assert r.accepted and len(r.intents) == 1
    it = r.intents[0]
    assert it.symbol == "AAA" and it.stop_price == D(96) and it.strategy_id == "momentum_v1"
    assert len(it.unique_key) == 64 and it.decision_id == r.decision_id


def test_intent_keys_are_deterministic() -> None:
    assert run(decision()).intents[0].unique_key == run(decision()).intents[0].unique_key


@pytest.mark.parametrize(
    "field,value",
    [
        ("risk_limits", {"per_trade_risk_inr": 5000}),
        ("max_positions", 10),
        ("live_trading_enabled", True),
        ("orders", [{"type": "MARKET", "qty": 1000}]),
        ("api_key", "x"),
    ],
)
def test_ai_cannot_smuggle_controls(field: str, value: Any) -> None:
    r = run(decision(**{field: value}))
    assert not r.accepted and not r.intents and "Extra inputs" in r.rejections[0]


def test_ai_cannot_add_order_fields_to_intents() -> None:
    r = run(decision(trade_intents=[intent(quantity=1000)]))
    assert not r.accepted


def test_malformed_json() -> None:
    r = evaluate_ai_output("{not json", snapshot())
    assert not r.accepted and not r.intents


def test_model_unavailable() -> None:
    r = evaluate_ai_output(UnavailableClient().propose("{}"), snapshot())
    assert not r.accepted and "unavailable" in r.rejections[0]


def test_stale_data_blocks_ai_entries() -> None:
    assert not run(decision(), snapshot(fresh=False)).accepted


@pytest.mark.parametrize("ts", [TS - timedelta(minutes=1), TS + timedelta(hours=2)])
def test_stale_or_predated_rationale(ts: datetime) -> None:
    assert not run(decision(timestamp=ts.isoformat())).accepted


def test_unapproved_strategy() -> None:
    r = run(decision(strategies=[{"id": "yolo_options", "weight": "1", "justification": "z"}]))
    assert not r.accepted and "unapproved" in r.rejections[0]


def test_weights_cannot_exceed_one() -> None:
    r = run(
        decision(
            strategies=[
                {"id": "momentum_v1", "weight": "0.9", "justification": "x"},
                {"id": "breakout_v1", "weight": "0.9", "justification": "y"},
            ]
        )
    )
    assert not r.accepted


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"symbol": "ZZZ", "evidence_ids": ["momentum_v1:ZZZ:2026-11-03"]}, "evidence id not in snapshot"),
        ({"symbol": "BBB"}, "different symbol"),
        ({"side": "SELL"}, "sells/shorts"),
        ({"segment": "ETF"}, "exchange/segment"),
        ({"reference_price": "110", "target_price": "130"}, "drifts"),
        ({"stop_price": "101"}, "stop < reference < target"),
        ({"stop_price": "90"}, "widen"),
        ({"horizon": 30}, "horizon"),
    ],
)
def test_semantic_rejections(over: dict[str, Any], reason: str) -> None:
    r = run(decision(trade_intents=[intent(**over)]))
    assert r.accepted and not r.intents and reason in r.rejections[0]


def test_zero_weight_strategy_intent_rejected() -> None:
    r = run(decision(strategies=[{"id": "breakout_v1", "weight": "1", "justification": "y"}]))
    assert not r.intents and "zero weight" in r.rejections[0]


def test_duplicate_symbol_in_decision() -> None:
    r = run(decision(trade_intents=[intent(), intent()]))
    assert len(r.intents) == 1 and "duplicate" in r.rejections[0]


def test_confidence_bounds_enforced_by_schema() -> None:
    assert not run(decision(trade_intents=[intent(confidence="1.5")])).accepted


def test_snapshot_has_no_account_or_secret_fields() -> None:
    fields = set(AISnapshot.model_fields) | set(Candidate.model_fields)
    forbidden = {"cash", "equity", "balance", "token", "api_key", "password", "limits", "risk_limits", "credentials", "account"}
    assert not fields & forbidden


def test_deterministic_ranker_output_passes_policy() -> None:
    snap = snapshot()
    r = evaluate_ai_output(DeterministicRanker().propose(snap.model_dump_json()), snap)
    assert r.accepted and {i.symbol for i in r.intents} == {"AAA", "BBB"}
