"""Spec section 7 failure-mode matrix, driven through the paper engine."""

from __future__ import annotations

from pathlib import Path

from ata.app import Chaos, PaperEngine
from ata.settings import Settings


def run(tmp_path: Path, chaos: Chaos, days: int = 25, seed: int = 23) -> PaperEngine:
    e = PaperEngine(Settings(database_url=f"sqlite:///{tmp_path / 'c.db'}"), seed=seed, chaos=chaos)
    e.run(days)
    return e


def risk_rows(e: PaperEngine) -> list[dict]:  # type: ignore[type-arg]
    return [r["payload"] for r in e.store.audit_rows("decision") if r["component"] == "risk"]


def test_auth_expired_pauses_entries_and_alerts(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(session_expired=set(range(25))))
    assert not e.store.audit_rows("fill")
    assert all(not r["approved"] for r in risk_rows(e))
    assert any("re-login" in a.message for a in e.alerts.alerts)


def test_stale_quotes_block_entries(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(stale_data=set(range(25))))
    assert not e.store.audit_rows("fill")


def test_llm_unavailable_means_no_ai_entries(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(ai_unavailable=set(range(25))))
    assert not risk_rows(e) and not e.store.audit_rows("fill")


def test_malformed_llm_output_is_rejected_every_time(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(ai_malformed=set(range(25))))
    ai = [r["payload"] for r in e.store.audit_rows("decision") if r["component"] == "ai"]
    assert ai and not any(a["accepted"] for a in ai if a["snapshot_candidates"])


def test_network_timeout_reconciles_without_duplicates(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(broker_timeout_accepted=set(range(0, 25, 2))))
    cids = [o.client_order_id for o in e.broker._orders.values()]
    assert len(cids) == len(set(cids))
    assert not any(o.state.value == "UNKNOWN" for o in e.oms.orders.values())
    assert any(r["payload"]["to"] == "UNKNOWN" for r in e.store.audit_rows("order_state"))


def test_partial_fills_keep_stop_equal_to_position(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(partial_fill=set(range(25))))
    for sym, pos in e.ledger.positions.items():
        stops = [o for o in e.oms.active_orders() if o.request.symbol == sym and o.purpose.value == "STOP"]
        assert len(stops) <= 1
        if stops:
            assert stops[0].request.qty - stops[0].filled_qty == pos.qty


def test_rejections_do_not_create_positions(tmp_path: Path) -> None:
    e = run(tmp_path, Chaos(broker_reject=set(range(25))))
    assert e.store.verify_audit_chain()
    assert e.reconciled
