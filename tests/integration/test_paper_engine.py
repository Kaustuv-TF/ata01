"""End-to-end paper runs: limits hold across whole simulations, every decision is audited,
restart keeps the lockout, and AI output cannot change limits."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from ata.app import Chaos, PaperEngine
from ata.cli import main as cli_main
from ata.money import D
from ata.ops.reporting import review_report
from ata.settings import Settings

SEEDS = [7, 11, 23, 42]


def engine(tmp_path: Path, seed: int, **kw: object) -> PaperEngine:
    return PaperEngine(Settings(database_url=f"sqlite:///{tmp_path / f'e{seed}.db'}"), seed=seed, **kw)  # type: ignore[arg-type]


def assert_limits_held(e: PaperEngine) -> None:
    L = e.limits
    for s in e.snapshots:
        assert s.positions <= L.max_positions
        assert s.open_risk <= L.aggregate_risk_inr
        assert s.cash >= 0
    for row in e.store.audit_rows("decision"):
        p = row["payload"]
        if row["component"] != "risk" or not p["approved"]:
            continue
        assert D(p["est_stop_risk"]) <= L.per_trade_risk_inr
        assert D(p["qty"]) * D(p["limit"]) <= L.per_symbol_cap_inr
        assert D(p["est_edge"]) >= D(p["est_costs"]) * L.min_edge_to_cost_ratio
    # gross at any moment is bounded by approvals: cost basis of positions is checked at entry
    for row in e.store.audit_rows("snapshot"):
        assert D(row["payload"]["gross"]) <= L.gross_invested_cap_inr * D("1.25")  # marks can drift above entry cost


@pytest.mark.parametrize("seed", SEEDS)
def test_thirty_day_run_respects_limits(tmp_path: Path, seed: int) -> None:
    e = engine(tmp_path, seed)
    snaps = e.run(30)
    assert len(snaps) == 30
    assert_limits_held(e)
    assert e.store.verify_audit_chain()
    assert e.limits.policy_hash() == e.store.audit_rows("startup")[0]["payload"]["policy_hash"]


@pytest.mark.parametrize("seed", SEEDS)
def test_chaos_run_respects_limits_and_recovers(tmp_path: Path, seed: int) -> None:
    e = engine(tmp_path, seed, chaos=Chaos.demo())
    e.run(30)
    assert_limits_held(e)
    states = [r["payload"] for r in e.store.audit_rows("order_state")]
    assert not any(s["to"] == "UNKNOWN" for s in states) or any(s["from"] == "UNKNOWN" for s in states)
    assert not any(o.state.value == "UNKNOWN" for o in e.oms.orders.values())
    critical = [a.message for a in e.alerts.alerts if a.severity == "CRITICAL"]
    assert any("session expired" in m for m in critical)


def test_every_ai_proposal_and_risk_decision_is_logged(tmp_path: Path) -> None:
    e = engine(tmp_path, 23)
    e.run(30)
    ai = [r["payload"] for r in e.store.audit_rows("decision") if r["component"] == "ai"]
    risk = [r["payload"] for r in e.store.audit_rows("decision") if r["component"] == "risk"]
    assert len(ai) == 30 and all("raw" in a and "decision_id" in a for a in ai)
    proposed = {i["unique_key"] for a in ai for i in a["intents"]}
    assert {r["intent"] for r in risk} <= proposed
    for r in risk:
        assert {"qty", "rules", "est_costs", "est_stop_risk"} <= set(r)
        assert all({"rule", "passed", "detail"} <= set(x) for x in r["rules"])
    fills = e.store.audit_rows("fill")
    assert all(D(f["payload"]["fees"]) > 0 for f in fills)


def test_restart_keeps_lockout_and_blocks_entries(tmp_path: Path) -> None:
    db = tmp_path / "restart.db"
    first = PaperEngine(Settings(database_url=f"sqlite:///{db}"), seed=23)
    first.risk_state.on_equity(date(2026, 10, 30), D(9400), first._ts(date(2026, 10, 30), 15, 30))
    assert first.risk_state.lockout()
    del first  # "crash"
    second = PaperEngine(Settings(database_url=f"sqlite:///{db}"), seed=23)
    second.run(20)
    risk = [r["payload"] for r in second.store.audit_rows("decision") if r["component"] == "risk"]
    assert risk and not any(r["approved"] for r in risk)
    assert all(any(x["rule"] == "drawdown_lockout" and not x["passed"] for x in r["rules"]) for r in risk)
    assert not second.ledger.positions


class GreedyModel:
    """A hostile model: tries to raise limits, add products and size orders itself."""

    model_version = "greedy"

    def propose(self, snapshot_json: str) -> str:
        snap = json.loads(snapshot_json)
        intents = [
            {
                "symbol": c["symbol"],
                "exchange": c["exchange"],
                "segment": c["segment"],
                "side": "BUY",
                "reference_price": c["reference_price"],
                "stop_price": str(Decimal(c["stop_price"]) * Decimal("0.5")),
                "target_price": c["target_price"],
                "horizon": 60,
                "confidence": "1",
                "evidence_ids": [c["evidence_id"]],
            }
            for c in snap["candidates"]
        ]
        return json.dumps(
            {
                "timestamp": snap["timestamp"],
                "model_version": "greedy",
                "market_regime": "trending_up",
                "strategies": [{"id": s["id"], "weight": "1", "justification": "all in"} for s in snap["approved_strategies"]],
                "trade_intents": intents,
                "warnings": [],
                "per_trade_risk_inr": 10000,
            }
        )


def test_hostile_ai_cannot_change_limits_or_trade(tmp_path: Path) -> None:
    e = engine(tmp_path, 23, model=GreedyModel())
    before = e.limits.policy_hash()
    e.run(20)
    assert e.limits.policy_hash() == before
    assert not e.store.audit_rows("fill")
    ai = [r["payload"] for r in e.store.audit_rows("decision") if r["component"] == "ai"]
    assert ai and not any(a["accepted"] for a in ai)


def test_no_data_means_no_trading(tmp_path: Path) -> None:
    e = engine(tmp_path, 7)
    e.run(10)
    bad = {s: [b.model_copy(update={"low": D(0)}) for b in bars] for s, bars in (e.bars or {}).items()}
    e2 = PaperEngine(Settings(database_url=f"sqlite:///{tmp_path / 'nodata.db'}"), seed=7, bars=bad)
    e2.run(10)
    assert not e2.store.audit_rows("fill")
    assert any("history invalid" in a.message for a in e2.alerts.alerts)


def test_live_mode_refuses_to_start(tmp_path: Path) -> None:
    from ata.brokers.base import LiveTradingDisabled

    with pytest.raises(LiveTradingDisabled):
        PaperEngine(Settings(database_url=f"sqlite:///{tmp_path / 'x.db'}", market_mode="live"))


def test_review_report(tmp_path: Path) -> None:
    e = engine(tmp_path, 11)
    e.run(30)
    md = review_report(e)
    for heading in (
        "## Summary",
        "## Limit utilisation",
        "## Decision funnel",
        "## Closed trades",
        "## Critical alerts",
        "Audit hash chain valid: True",
        "PAPER SIMULATION",
    ):
        assert heading in md


def test_cli_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "cli.db")
    report = tmp_path / "r.md"
    assert cli_main(["--db", db, "simulate", "--days", "5", "--chaos", "--report", str(report)]) == 0
    assert report.exists()
    assert cli_main(["--db", db, "limits"]) == 0
    assert cli_main(["--db", db, "kill", "--author", "Sourav", "--reason", "testing kill switch"]) == 0
    assert cli_main(["--db", db, "status"]) == 0
    assert "kill switch: {" in capsys.readouterr().out
    assert (
        cli_main(["--db", db, "owner-clear", "kill_switch", "--author", "Sourav", "--reason", "test complete ok", "--confirm", "nope"]) == 2
    )
    assert (
        cli_main(
            [
                "--db",
                db,
                "owner-clear",
                "kill_switch",
                "--author",
                "Sourav",
                "--reason",
                "test complete ok",
                "--confirm",
                "CLEAR KILL_SWITCH",
            ]
        )
        == 0
    )
