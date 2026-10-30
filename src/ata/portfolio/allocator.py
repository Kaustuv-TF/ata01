"""Allocator: order validated intents by AI strategy weight x confidence, one per symbol.
Capital and risk constraints are NOT applied here; the risk gateway owns those."""

from __future__ import annotations

from decimal import Decimal

from ata.ai.schemas import AIDecision
from ata.models import TradeIntent


def rank_intents(intents: list[TradeIntent], decision: AIDecision | None) -> list[TradeIntent]:
    weights = {w.id: w.weight for w in decision.strategies} if decision else {}
    best: dict[str, TradeIntent] = {}
    for it in intents:
        score = weights.get(it.strategy_id, Decimal(0)) * it.confidence
        cur = best.get(it.symbol)
        if cur is None or score > weights.get(cur.strategy_id, Decimal(0)) * cur.confidence:
            best[it.symbol] = it
    return sorted(best.values(), key=lambda x: (-(weights.get(x.strategy_id, Decimal(0)) * x.confidence), x.symbol))
