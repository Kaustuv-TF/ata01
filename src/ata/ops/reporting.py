"""30-day owner review report (markdown)."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from ata.money import ZERO, inr

if TYPE_CHECKING:
    from ata.app import PaperEngine


def review_report(engine: PaperEngine) -> str:
    snaps = engine.snapshots
    L = engine.limits
    start_eq = L.initial_capital_inr
    end_eq = snaps[-1].equity if snaps else start_eq
    peak, max_dd = start_eq, ZERO
    for s in snaps:
        peak = max(peak, s.equity)
        max_dd = max(max_dd, peak - s.equity)
    decisions = engine.store.audit_rows("decision")
    risk_rows = [r["payload"] for r in decisions if r["component"] == "risk"]
    ai_rows = [r["payload"] for r in decisions if r["component"] == "ai"]
    approved = [r for r in risk_rows if r["approved"]]
    fail_counts: Counter[str] = Counter()
    for r in risk_rows:
        for rule in r["rules"]:
            if not rule["passed"]:
                fail_counts[rule["rule"]] += 1
    closed = engine.ledger.closed
    wins = [c for c in closed if c.pnl_after_fees > 0]
    cost_basis = sum((p.qty * p.avg_cost for p in engine.ledger.positions.values()), ZERO)
    unrealized = end_eq - engine.ledger.cash - cost_basis
    max_open_risk = max((s.open_risk for s in snaps), default=ZERO)
    max_gross = max((s.gross for s in snaps), default=ZERO)
    max_pos = max((s.positions for s in snaps), default=0)
    freeze = f"triggered {len(engine.store.audit_rows('daily_freeze'))}x" if engine.store.audit_rows("daily_freeze") else "not triggered"
    crit = [a for a in engine.alerts.alerts if a.severity == "CRITICAL"]
    lines = [
        f"# ATA-01 paper review: {snaps[0].day} to {snaps[-1].day}" if snaps else "# ATA-01 paper review (no sessions)",
        "",
        "> PAPER SIMULATION on synthetic data. Not market data, not a performance claim. Fees are UNVERIFIED estimates"
        f" ({'verified' if engine.costs.verified else 'placeholder schedule'});"
        f" holiday list {'verified' if engine.calendar.verified else 'is a sample'}.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Sessions | {len(snaps)} |",
        f"| Starting equity | ₹{start_eq:,.2f} |",
        f"| Ending equity (marked at close) | ₹{end_eq:,.2f} |",
        f"| Net change | ₹{end_eq - start_eq:,.2f} ({(end_eq - start_eq) / start_eq * 100:.2f}%) |",
        f"| Realized P&L after fees | ₹{inr(engine.ledger.realized_pnl):,.2f} |",
        f"| Unrealized (open positions, marked) | ₹{inr(unrealized):,.2f} |",
        f"| Fees and charges paid (estimated) | ₹{inr(engine.ledger.fees_total):,.2f} |",
        f"| Max drawdown from peak | ₹{max_dd:,.2f} (lockout at ₹{L.drawdown_lockout_inr}) |",
        f"| Closed trades / winners | {len(closed)} / {len(wins)} |",
        f"| Open positions at end | {len(engine.ledger.positions)} |",
        "",
        "## Limit utilisation (peak over the period)",
        "",
        "| Limit | Peak used | Cap |",
        "|---|---|---|",
        f"| Aggregate open stop risk | ₹{max_open_risk:,.2f} | ₹{L.aggregate_risk_inr} |",
        f"| Gross invested | ₹{max_gross:,.2f} | ₹{L.gross_invested_cap_inr} |",
        f"| Concurrent positions | {max_pos} | {L.max_positions} |",
        f"| Daily loss freeze | {freeze} | ₹{L.daily_loss_freeze_inr} |",
        f"| Drawdown lockout | {'ACTIVE' if engine.risk_state.lockout() else 'not active'} | ₹{L.drawdown_lockout_inr} |",
        "",
        "## Decision funnel",
        "",
        f"- AI decisions: {len(ai_rows)} ({sum(1 for a in ai_rows if not a['accepted'])} rejected by schema/policy)",
        f"- Trade intents risk-checked: {len(risk_rows)}; approved: {len(approved)}; skipped: {len(risk_rows) - len(approved)}",
        "",
        "Skip reasons (rule failures, an intent can fail several):",
        "",
    ]
    lines += [f"- `{rule}`: {n}" for rule, n in fail_counts.most_common()] or ["- none"]
    lines += [
        "",
        "## Closed trades",
        "",
        "| Symbol | Strategy | Qty | Entry | Exit | Fees | P&L after fees | Opened | Closed | Reason |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {c.symbol} | {c.strategy_id} | {c.qty} | {c.entry:.2f} | {c.exit:.2f} | {c.fees:.2f} | {c.pnl_after_fees:.2f}"
        f" | {c.opened} | {c.closed} | {c.reason} |"
        for c in closed
    ] or ["| none |||||||||| "]
    lines += ["", "## Open positions", "", "| Symbol | Qty | Avg cost | Stop | Target | Stop risk |", "|---|---|---|---|---|---|"]
    lines += [
        f"| {p.symbol} | {p.qty} | {p.avg_cost:.2f} | {p.stop_price} | {p.target_price} | {p.stop_risk:.2f} |"
        for p in engine.ledger.positions.values()
    ] or ["| none ||||||"]
    lines += ["", f"## Critical alerts ({len(crit)})", ""]
    lines += [f"- {a.ts:%Y-%m-%d %H:%M} {a.message}" for a in crit] or ["- none"]
    lines += ["", "## Control events", ""]
    lines += [f"- {e['ts']} {e['type']} by {e['author']}: {e['reason']}" for e in engine.store.control_events()] or ["- none"]
    lines += [
        "",
        "## Integrity",
        "",
        f"- Audit hash chain valid: {engine.store.verify_audit_chain()}",
        f"- Risk policy version {engine.policy_version}, hash `{L.policy_hash()[:16]}`",
        "",
        "## Owner actions before any live step",
        "",
        "- Complete Paytm Money API onboarding and the checklist in docs/API_BROKER.md (Gate 0).",
        "- Replace config/costs.yaml with the verified fee schedule; replace the holiday list.",
        "- Run Gate 2 (3-5 years of real history, walk-forward) and Gate 3 (20+ paper-live sessions).",
    ]
    return "\n".join(lines) + "\n"
