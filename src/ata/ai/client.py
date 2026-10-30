"""Model clients. The boundary is text in, text out: a client receives the
sanitized snapshot JSON and returns raw JSON text. It has no broker handle,
no credentials and no tools that write anything.

Only a deterministic offline ranker ships in this milestone. A hosted-LLM
client (separate paid API key held outside the trading process) is a later
milestone and must implement the same `propose` signature.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from ata.ai.schemas import AISnapshot, Candidate


class ModelClient(Protocol):
    model_version: str

    def propose(self, snapshot_json: str) -> str | None:
        """Return raw JSON text, or None if the model is unavailable."""


class DeterministicRanker:
    """Offline stand-in for the LLM: ranks candidates by reward/risk. No network."""

    model_version = "deterministic-ranker-1"

    def __init__(self, max_intents: int = 3) -> None:
        self.max_intents = max_intents

    def propose(self, snapshot_json: str) -> str | None:
        snap = AISnapshot.model_validate_json(snapshot_json)
        breadth = Decimal(snap.regime_factors.get("breadth_above_sma50", "0.5"))
        regime = "trending_up" if breadth >= Decimal("0.6") else "trending_down" if breadth <= Decimal("0.4") else "range_bound"

        def rr(c: Candidate) -> Decimal:
            return (c.target_price - c.reference_price) / (c.reference_price - c.stop_price)

        ranked = sorted(snap.candidates, key=lambda c: (-rr(c), c.evidence_id))
        chosen, symbols = [], set()
        for c in ranked:
            if c.symbol in symbols:
                continue
            symbols.add(c.symbol)
            chosen.append(c)
            if len(chosen) >= self.max_intents:
                break
        used = sorted({c.strategy_id for c in chosen})
        decision = {
            "timestamp": (snap.timestamp + timedelta(seconds=1)).isoformat(),
            "model_version": self.model_version,
            "market_regime": regime,
            "strategies": [
                {"id": s, "weight": str(round(Decimal(1) / len(used), 4)), "justification": "has candidates with best reward/risk"}
                for s in used
            ],
            "trade_intents": [
                {
                    "symbol": c.symbol,
                    "exchange": c.exchange.value,
                    "segment": c.segment.value,
                    "side": "BUY",
                    "reference_price": str(c.reference_price),
                    "stop_price": str(c.stop_price),
                    "target_price": str(c.target_price),
                    "horizon": c.horizon_days,
                    "confidence": str(min(Decimal("0.9"), round(rr(c) / 4, 2))),
                    "evidence_ids": [c.evidence_id],
                }
                for c in chosen
            ],
            "warnings": ["synthetic data; confidence is uncalibrated"],
        }
        return json.dumps(decision)


class UnavailableClient:
    model_version = "unavailable"

    def propose(self, snapshot_json: str) -> str | None:
        return None
