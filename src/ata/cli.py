"""ATA-01 command line. Every command runs in paper mode; none can place a real order."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from ata.app import Chaos, DaySnapshot, PaperEngine
from ata.ops.reporting import review_report
from ata.ops.store import Store
from ata.risk.limits import MANDATE_CEILINGS, load_limits
from ata.risk.state import RiskState
from ata.settings import Settings


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    if getattr(args, "db", None):
        s = s.model_copy(update={"database_url": f"sqlite:///{Path(args.db).resolve()}"})
    s.assert_paper_only()
    return s


def cmd_simulate(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if settings.database_url.startswith("sqlite:///"):
        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = PaperEngine(
        settings, seed=args.seed, start=date.fromisoformat(args.start), chaos=Chaos.demo() if args.chaos else None, echo=args.verbose
    )

    def show(s: DaySnapshot) -> None:
        print(
            f"{s.day}  equity ₹{s.equity:>9,.2f}  cash ₹{s.cash:>9,.2f}  gross ₹{s.gross:>8,.2f}  "
            f"open-risk ₹{s.open_risk:>6,.2f}  dd ₹{s.drawdown:>6,.2f}  pos {s.positions}"
        )

    print(
        f"ATA-01 paper simulation: {args.days} sessions from {args.start}, seed {args.seed}"
        f"{', chaos scenarios ON' if args.chaos else ''} (synthetic data, no broker connection)"
    )
    engine.run(args.days, on_day=show)
    report = review_report(engine)
    out = Path(args.report or settings.report_dir / f"review_{engine.snapshots[-1].day}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"\nReview report: {out}")
    print(f"Audit log: {settings.database_url} (hash chain valid: {engine.store.verify_audit_chain()})")
    return 0


def cmd_limits(args: argparse.Namespace) -> int:
    settings = _settings(args)
    limits, version = load_limits(settings.config_dir / "defaults.yaml")
    print(f"Risk policy {version}  hash {limits.policy_hash()[:16]}")
    for k, v in limits.model_dump().items():
        ceiling = MANDATE_CEILINGS.get(k)
        print(f"  {k:28} {v!s:>20}" + (f"   (mandate ceiling {ceiling})" if ceiling is not None else ""))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    limits, _ = load_limits(settings.config_dir / "defaults.yaml")
    state = RiskState(Store(settings.database_url), limits)
    print(f"mode: {settings.market_mode} (live trading enabled: {settings.live_trading_enabled})")
    print(f"high-water mark: ₹{state.high_water_mark}")
    print(f"drawdown lockout: {state.lockout() or 'clear'}")
    print(f"kill switch: {state.kill_switch() or 'off'}")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    settings = _settings(args)
    limits, _ = load_limits(settings.config_dir / "defaults.yaml")
    RiskState(Store(settings.database_url), limits).engage_kill_switch(args.author, args.reason, datetime.now(UTC))
    print("Kill switch engaged: new entries blocked until the owner clears it.")
    return 0


def cmd_owner_clear(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if args.confirm != f"CLEAR {args.block.upper()}":
        print(f'Refused: pass --confirm "CLEAR {args.block.upper()}" to acknowledge this is a human owner action.', file=sys.stderr)
        return 2
    limits, _ = load_limits(settings.config_dir / "defaults.yaml")
    state = RiskState(Store(settings.database_url), limits)
    equity = limits.initial_capital_inr if args.equity is None else Decimal(args.equity)
    state.owner_clear(args.block, args.author, args.reason, datetime.now(UTC), current_equity=equity)
    print(f"{args.block} cleared by {args.author}; recorded in control_events.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ata", description=__doc__)
    p.add_argument("--db", help="SQLite file for audit/risk state (default var/ata.db or ATA_DATABASE_URL)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sim = sub.add_parser("simulate", help="offline paper simulation + 30-day review report")
    sim.add_argument("--days", type=int, default=30)
    sim.add_argument("--seed", type=int, default=23)
    sim.add_argument("--start", default="2026-11-02")
    sim.add_argument("--chaos", action="store_true", help="inject timeouts, rejections, stale data, bad AI output, session expiry")
    sim.add_argument("--report", help="report output path")
    sim.add_argument("-v", "--verbose", action="store_true", help="print alerts as they happen")
    sim.set_defaults(func=cmd_simulate)
    sub.add_parser("limits", help="print the active risk policy").set_defaults(func=cmd_limits)
    sub.add_parser("status", help="show persistent risk state").set_defaults(func=cmd_status)
    k = sub.add_parser("kill", help="engage the kill switch")
    k.add_argument("--author", required=True)
    k.add_argument("--reason", required=True)
    k.set_defaults(func=cmd_kill)
    c = sub.add_parser("owner-clear", help="HUMAN OWNER ONLY: clear a drawdown lockout or kill switch")
    c.add_argument("block", choices=["drawdown_lockout", "kill_switch"])
    c.add_argument("--author", required=True)
    c.add_argument("--reason", required=True)
    c.add_argument("--confirm", required=True)
    c.add_argument("--equity", help="current verified equity to rebase the high-water mark")
    c.set_defaults(func=cmd_owner_clear)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
