"""Semantic validation of model output. Schema-valid is necessary, not sufficient.

The AI may rank and weight approved candidates. It may not invent symbols or
evidence, open shorts, widen stops, extend horizons, use unapproved strategies,
or touch limits. Any decision-level violation discards the whole decision.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, ValidationError

from ata.ai.schemas import AIDecision, AISnapshot, Candidate
from ata.models import Side, TradeIntent
from ata.money import D

MAX_DECISION_AGE = timedelta(minutes=30)
MAX_REFERENCE_DRIFT = D("0.02")


class PolicyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str
    accepted: bool
    decision: AIDecision | None
    intents: tuple[TradeIntent, ...]
    rejections: tuple[str, ...]


def decision_id_for(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def intent_key(decision_id: str, symbol: str, side: Side, evidence: list[str], day: str) -> str:
    material = "|".join([decision_id, symbol, side.value, ",".join(sorted(evidence)), day])
    return hashlib.sha256(material.encode()).hexdigest()


def _reject(decision_id: str, reasons: list[str], decision: AIDecision | None = None) -> PolicyResult:
    return PolicyResult(decision_id=decision_id, accepted=False, decision=decision, intents=(), rejections=tuple(reasons))


def evaluate_ai_output(raw: str | None, snapshot: AISnapshot) -> PolicyResult:
    if raw is None:
        return _reject("none", ["model unavailable: no AI-originated entries this cycle"])
    did = decision_id_for(raw)
    try:
        decision = AIDecision.model_validate_json(raw)
    except ValidationError as exc:
        return _reject(did, [f"schema: {e['loc']} {e['msg']}" for e in exc.errors()][:10])

    problems: list[str] = []
    if not snapshot.data_fresh:
        problems.append("snapshot data stale: AI entries disabled")
    if decision.timestamp < snapshot.timestamp or decision.timestamp - snapshot.timestamp > MAX_DECISION_AGE:
        problems.append("decision timestamp outside snapshot window (stale or pre-dated rationale)")
    approved = {s.id for s in snapshot.approved_strategies}
    weights = {w.id: w.weight for w in decision.strategies}
    if unknown := set(weights) - approved:
        problems.append(f"unapproved strategies: {sorted(unknown)}")
    if sum(weights.values(), Decimal(0)) > D("1.0001"):
        problems.append("strategy weights sum > 1")
    if problems:
        return _reject(did, problems, decision)

    by_id: dict[str, Candidate] = {c.evidence_id: c for c in snapshot.candidates}
    intents: list[TradeIntent] = []
    rejections: list[str] = []
    seen_symbols: set[str] = set()
    for ti in decision.trade_intents:
        tag = f"{ti.symbol}"
        evidence = [by_id.get(e) for e in ti.evidence_ids]
        if any(c is None for c in evidence):
            rejections.append(f"{tag}: evidence id not in snapshot")
            continue
        cands = [c for c in evidence if c is not None]
        if any(c.symbol != ti.symbol for c in cands):
            rejections.append(f"{tag}: evidence refers to a different symbol")
            continue
        if ti.side is not Side.BUY:
            rejections.append(f"{tag}: AI may not originate sells/shorts in V1")
            continue
        if ti.symbol in seen_symbols:
            rejections.append(f"{tag}: duplicate symbol in decision")
            continue
        primary = cands[0]
        if (ti.exchange, ti.segment) != (primary.exchange, primary.segment):
            rejections.append(f"{tag}: exchange/segment differs from evidence")
            continue
        if weights.get(primary.strategy_id, D(0)) <= 0:
            rejections.append(f"{tag}: strategy {primary.strategy_id} has zero weight")
            continue
        if abs(ti.reference_price - primary.reference_price) / primary.reference_price > MAX_REFERENCE_DRIFT:
            rejections.append(f"{tag}: reference price drifts from evidence")
            continue
        if not (ti.stop_price < ti.reference_price < ti.target_price):
            rejections.append(f"{tag}: require stop < reference < target")
            continue
        if ti.stop_price < primary.stop_price:
            rejections.append(f"{tag}: AI may tighten but not widen the strategy stop")
            continue
        if ti.horizon > primary.horizon_days:
            rejections.append(f"{tag}: horizon longer than strategy horizon")
            continue
        seen_symbols.add(ti.symbol)
        intents.append(
            TradeIntent(
                unique_key=intent_key(did, ti.symbol, ti.side, ti.evidence_ids, snapshot.timestamp.date().isoformat()),
                decision_id=did,
                strategy_id=primary.strategy_id,
                symbol=ti.symbol,
                exchange=ti.exchange,
                segment=ti.segment,
                side=ti.side,
                reference_price=ti.reference_price,
                stop_price=ti.stop_price,
                target_price=ti.target_price,
                horizon_days=ti.horizon,
                confidence=ti.confidence,
                evidence_ids=tuple(ti.evidence_ids),
            )
        )
    return PolicyResult(decision_id=did, accepted=True, decision=decision, intents=tuple(intents), rejections=tuple(rejections))
