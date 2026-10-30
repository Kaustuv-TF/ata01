"""Paper trading engine: one simulated trading day at a time on daily bars.

Morning  09:15  open session, pre-flight reconcile, exits, risk-gated entries
Session         paper broker matches orders against the day's bar
Close    15:30  sync fills -> ledger, protective stops, expire DAY orders,
                mark equity, daily freeze / drawdown lockout, reconcile
Evening         strategies -> sanitized snapshot -> AI -> policy -> intents for tomorrow
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ata.ai.client import DeterministicRanker, ModelClient
from ata.ai.policy import evaluate_ai_output
from ata.ai.schemas import AISnapshot, Candidate, StrategyInfo
from ata.backtest.costs import CostModel
from ata.brokers.base import BrokerError, OrderRequest
from ata.brokers.paper import PaperBroker
from ata.data import features
from ata.data.calendar import MarketCalendar
from ata.data.history import synthetic_universe
from ata.data.instruments import InstrumentMaster
from ata.data.validators import DataCheck, validate_history, validate_quote
from ata.execution.orders import NewEntriesHalted, OrderManager, OrderState, Purpose, client_order_id
from ata.execution.reconciliation import reconcile
from ata.execution.stops import sync_protection
from ata.models import Bar, OrderType, Product, Side, TradeIntent
from ata.money import ZERO, D, floor_to_tick, inr
from ata.ops.alerts import AlertSink
from ata.ops.store import Store
from ata.portfolio.allocator import rank_intents
from ata.portfolio.ledger import Ledger
from ata.risk.limits import RiskLimits, load_limits
from ata.risk.rules import GateContext, PortfolioView, RiskGateway
from ata.risk.state import RiskState
from ata.settings import Settings
from ata.strategies.catalog import APPROVED

WARMUP_DAYS = 120


@dataclass
class DaySnapshot:
    day: date
    cash: Decimal
    equity: Decimal
    gross: Decimal
    open_risk: Decimal
    drawdown: Decimal
    positions: int


@dataclass
class Chaos:
    """Per-day fault injections, keyed by simulation day index (0-based)."""

    stale_data: set[int] = field(default_factory=set)
    ai_malformed: set[int] = field(default_factory=set)
    ai_unavailable: set[int] = field(default_factory=set)
    broker_timeout_accepted: set[int] = field(default_factory=set)
    broker_reject: set[int] = field(default_factory=set)
    partial_fill: set[int] = field(default_factory=set)
    session_expired: set[int] = field(default_factory=set)

    @classmethod
    def demo(cls) -> Chaos:
        return cls(
            stale_data={4},
            ai_malformed={6},
            broker_timeout_accepted={2, 9},
            broker_reject={11},
            partial_fill={3, 13},
            session_expired={15},
            ai_unavailable={18},
        )


class PaperEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        seed: int = 7,
        start: date | None = None,
        model: ModelClient | None = None,
        chaos: Chaos | None = None,
        echo: bool = False,
        bars: dict[str, list[Bar]] | None = None,
    ) -> None:
        settings.assert_paper_only()
        self.settings = settings
        cfg = settings.config_dir
        self.limits: RiskLimits
        self.limits, self.policy_version = load_limits(cfg / "defaults.yaml")
        self.costs = CostModel.load(cfg / "costs.yaml")
        self.calendar = MarketCalendar.load(cfg / "markets.yaml")
        self.instruments = InstrumentMaster.load(cfg / "instruments.yaml")
        self.store = Store(settings.database_url)
        self.risk_state = RiskState(self.store, self.limits)
        self.alerts = AlertSink(self.store, echo=echo)
        self.gateway = RiskGateway(self.limits, self.costs)
        self.model = model or DeterministicRanker()
        self.chaos = chaos or Chaos()
        self.seed = seed
        self.start = start or date(2026, 11, 2)
        self.broker = PaperBroker(self.instruments, self.calendar, self.costs, cash=self.limits.initial_capital_inr)
        self.clock_offset = timedelta(0)
        self.oms = OrderManager(self.broker, self.store, self.limits.max_orders_per_second, throttle=self._advance)
        self.ledger = Ledger(cash=self.limits.initial_capital_inr)
        self.intents: dict[str, TradeIntent] = {}
        self.queued_intents: list[TradeIntent] = []
        self.queued_exits: dict[str, str] = {}
        self.stop_seq: dict[str, int] = {}
        self.snapshots: list[DaySnapshot] = []
        self.reconciled = False
        self.bars = bars
        self.store.audit(
            "app",
            "startup",
            {
                "mode": settings.market_mode,
                "policy_version": self.policy_version,
                "policy_hash": self.limits.policy_hash(),
                "costs_verified": self.costs.verified,
                "holidays_verified": self.calendar.verified,
                "seed": seed,
            },
        )

    def _advance(self, delay: timedelta) -> None:
        self.clock_offset += delay

    def _ts(self, d: date, hh: int, mm: int) -> datetime:
        return self.calendar.session_ts(d, hh, mm) + self.clock_offset

    # ------------------------------------------------------------------ run
    def run(self, days: int, on_day: Callable[[DaySnapshot], None] | None = None) -> list[DaySnapshot]:
        all_days = self.calendar.trading_days(self.start - timedelta(days=WARMUP_DAYS * 2), 10_000)
        all_days = [d for d in all_days if d < self.start][-WARMUP_DAYS:] + self.calendar.trading_days(self.start, days)
        if self.bars is None:
            self.bars = synthetic_universe(self.instruments.all(), self.calendar, all_days[0], len(all_days), self.seed)
        history_check = {s: validate_history(b) for s, b in self.bars.items()}
        for sym, chk in history_check.items():
            if not chk.ok:
                self.alerts.emit("CRITICAL", f"history invalid for {sym}: {chk.reason}; symbol blocked", self._ts(self.start, 9, 0))
        self.history_ok = {s: c.ok for s, c in history_check.items()}
        self.index = {d: i for i, d in enumerate(all_days)}
        for n, d in enumerate(all_days[WARMUP_DAYS:]):
            snap = self.run_day(n, d)
            if on_day:
                on_day(snap)
        return self.snapshots

    def run_day(self, n: int, d: date) -> DaySnapshot:
        assert self.bars is not None
        self.clock_offset = timedelta(0)
        i = self.index[d]
        prev_bars = {s: b[i - 1] for s, b in self.bars.items()}
        today = {s: b[i] for s, b in self.bars.items()}
        self.broker.faults.session_expired = n in self.chaos.session_expired
        # one-shot broker faults armed from day n; consumed by the next order placed (entry, stop or exit)
        if n in self.chaos.broker_timeout_accepted:
            self.broker.faults.timeout_next = "accepted"
        if n in self.chaos.broker_reject:
            self.broker.faults.reject_next = "RMS: simulated exchange rejection"
        if n in self.chaos.partial_fill:
            self.broker.faults.partial_next = 1
        if n in self.chaos.session_expired:
            self.alerts.emit("CRITICAL", "broker session expired: new entries paused; human re-login required", self._ts(d, 9, 0))

        # ---- morning
        t = self._ts(d, 9, 15)
        self.broker.open_session(t, prev_bars)
        self._reconcile(t, "pre-open")
        prev_marks = {s: b.close for s, b in prev_bars.items()}
        self.risk_state.start_day(d, self.ledger.equity(prev_marks))
        self._morning_exits(d)
        self._morning_entries(n, d, prev_bars)

        # ---- session: match orders against today's bar
        for sym in sorted(today):
            self.broker.process_bar(today[sym], self._ts(d, 15, 29))

        # ---- close
        t = self._ts(d, 15, 30)
        self._sync(t)
        self._protect(t)
        self.broker.end_session()
        self._sync(t)
        self._protect(t)
        marks = {s: b.close for s, b in today.items()}
        equity = self.ledger.equity(marks)
        for event in self.risk_state.on_equity(d, equity, t):
            self.alerts.emit("CRITICAL" if event == "drawdown_lockout" else "WARN", f"{event} triggered at equity {equity}", t)
        self._reconcile(t, "end-of-day")

        # ---- evening planning
        self._plan_exits(d, today)
        self._plan_entries(n, d, i)
        hwm = self.risk_state.high_water_mark
        snap = DaySnapshot(
            d,
            self.ledger.cash,
            equity,
            inr(self.ledger.gross_invested(marks)),
            self.ledger.open_risk(),
            inr(hwm - equity),
            len(self.ledger.positions),
        )
        self.snapshots.append(snap)
        self.store.audit("portfolio", "snapshot", snap.__dict__, t)
        return snap

    # ------------------------------------------------------------- helpers
    def _reconcile(self, t: datetime, when: str) -> None:
        result = reconcile(self.broker, self.ledger, self.oms)
        self.reconciled = result.ok
        self.store.audit("reconciliation", when, {"ok": result.ok, "discrepancies": list(result.discrepancies)}, t)
        if not result.ok:
            self.alerts.emit("CRITICAL", f"reconciliation failed ({when}): {'; '.join(result.discrepancies)}; new entries halted", t)

    def _sync(self, t: datetime) -> None:
        try:
            fills = self.oms.sync(t)
        except BrokerError as exc:
            self.alerts.emit("CRITICAL", f"cannot sync orders: {exc}", t)
            return
        for f in fills:
            if f.purpose is Purpose.ENTRY:
                intent = self.intents[f.intent_key]
                self.ledger.apply_entry_fill(
                    f.trade,
                    stop=intent.stop_price,
                    target=intent.target_price,
                    intent_key=intent.unique_key,
                    strategy_id=intent.strategy_id,
                    horizon=intent.horizon_days,
                )
            else:
                reason = "stop" if f.purpose is Purpose.STOP else self.queued_exits.pop(f.trade.symbol, "exit")
                self.ledger.apply_exit_fill(f.trade, reason)

    def _protect(self, t: datetime) -> None:
        try:
            unprotected = sync_protection(self.ledger, self.oms, self.instruments, t, self.stop_seq)
        except BrokerError as exc:
            self.alerts.emit("CRITICAL", f"protective stop sync failed: {exc}", t)
            return
        for sym in unprotected:
            if sym in self.queued_exits:
                continue
            self.alerts.emit("CRITICAL", f"{sym} position unprotected; scheduling exit", t)
            self.queued_exits.setdefault(sym, "unprotected")

    def _morning_exits(self, d: date) -> None:
        for sym, reason in list(self.queued_exits.items()):
            pos = self.ledger.positions.get(sym)
            quote = self.broker.quote(sym, self._ts(d, 9, 16))
            inst = self.instruments.get(sym)
            if pos is None or quote is None or inst is None:
                self.queued_exits.pop(sym, None)
                continue
            t = self._ts(d, 9, 16)
            for s in self.oms.active_orders(Purpose.STOP, sym):
                self.oms.cancel(s, t)
            if self.oms.active_orders(Purpose.STOP, sym):
                self.alerts.emit("WARN", f"{sym}: stop not cancellable; exit deferred", t)
                continue
            self.stop_seq[sym] = self.stop_seq.get(sym, 0) + 1
            req = OrderRequest(
                client_order_id=client_order_id(pos.intent_key, Purpose.EXIT, self.stop_seq[sym]),
                symbol=sym,
                exchange=inst.exchange,
                segment=inst.segment,
                product=Product.DELIVERY,
                side=Side.SELL,
                order_type=OrderType.LIMIT,
                qty=pos.qty,
                limit_price=floor_to_tick(quote.bid, inst.tick),
            )
            order = self.oms.create(req, pos.intent_key, Purpose.EXIT, t)
            self.oms.transition(order, OrderState.RISK_APPROVED, t, f"exit: {reason}")
            self.oms.submit(order, t)

    def _view(self, marks: dict[str, Decimal]) -> PortfolioView:
        pending_notional: dict[str, Decimal] = {}
        pending_risk = ZERO
        for o in self.oms.active_orders(Purpose.ENTRY):
            remaining = o.request.qty - o.filled_qty
            pending_notional[o.request.symbol] = pending_notional.get(o.request.symbol, ZERO) + o.request.limit_price * remaining
            pending_risk += (o.request.limit_price - self.intents[o.intent_key].stop_price) * remaining
        return PortfolioView(
            equity=self.ledger.equity(marks),
            cash=self.ledger.cash,
            position_cost={s: p.qty * p.avg_cost for s, p in self.ledger.positions.items()},
            position_marks={s: p.qty * marks.get(s, p.avg_cost) for s, p in self.ledger.positions.items()},
            open_risk=self.ledger.open_risk(),
            pending_entry_notional=pending_notional,
            pending_entry_risk=pending_risk,
        )

    def _morning_entries(self, n: int, d: date, prev_bars: dict[str, Bar]) -> None:
        intents, self.queued_intents = self.queued_intents, []
        marks = {s: b.close for s, b in prev_bars.items()}
        for intent in intents:
            t = self._ts(d, 9, 16)
            inst = self.instruments.get(intent.symbol)
            quote = self.broker.quote(intent.symbol, t)
            if quote is not None and n in self.chaos.stale_data:
                quote = quote.model_copy(update={"ts": t - timedelta(hours=3)})
            data_check = (
                DataCheck(ok=False, reason="history failed validation")
                if not self.history_ok.get(intent.symbol, False)
                else validate_quote(quote, t, timedelta(minutes=self.limits.max_data_age_minutes), self.limits.max_spread_bps)
                if quote
                else DataCheck(ok=False, reason="no quote")
            )
            ctx = GateContext(
                now=t,
                day=d,
                market_open=self.calendar.is_open(t),
                trading_day=self.calendar.is_trading_day(d),
                live_mode=self.settings.market_mode == "live",
                broker_session_valid=self.broker.session_valid(),
                reconciled=self.reconciled,
                entries_halted=tuple(sorted(self.oms.entry_halt_reasons)),
                kill_switch=self.risk_state.kill_switch() is not None,
                drawdown_lockout=self.risk_state.lockout() is not None,
                daily_freeze=self.risk_state.daily_freeze(d) is not None,
                data_check=data_check,
                quote=quote,
                prev_volume=prev_bars[intent.symbol].volume if intent.symbol in prev_bars else 0,
                instrument=inst,
            )
            decision = self.gateway.evaluate(intent, self._view(marks), ctx)
            self.store.audit(
                "risk",
                "decision",
                {
                    "intent": intent.unique_key,
                    "decision_id": intent.decision_id,
                    "symbol": intent.symbol,
                    "strategy": intent.strategy_id,
                    "approved": decision.approved,
                    "qty": decision.qty,
                    "limit": decision.limit_price,
                    "est_stop_risk": decision.est_stop_risk,
                    "est_loss_at_stop_incl_costs": decision.est_stop_risk + decision.est_round_trip_costs,
                    "est_costs": decision.est_round_trip_costs,
                    "est_edge": decision.est_gross_edge,
                    "rules": [r.model_dump() for r in decision.rules],
                },
                t,
            )
            if not decision.approved or inst is None:
                continue
            req = OrderRequest(
                client_order_id=client_order_id(intent.unique_key, Purpose.ENTRY),
                symbol=intent.symbol,
                exchange=intent.exchange,
                segment=intent.segment,
                product=Product.DELIVERY,
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                qty=decision.qty,
                limit_price=decision.limit_price,
            )
            order = self.oms.create(req, intent.unique_key, Purpose.ENTRY, t)
            self.oms.transition(order, OrderState.RISK_APPROVED, t, f"qty {decision.qty}")
            try:
                self.oms.submit(order, t)
            except NewEntriesHalted as exc:
                self.oms.transition(order, OrderState.CANCELLED, t, f"entries halted: {exc}")
            if order.state is OrderState.UNKNOWN:
                self.alerts.emit("WARN", f"{intent.symbol} entry status UNKNOWN; querying broker before any further entry", t)
                self._sync(self._ts(d, 9, 17))
            elif order.state is OrderState.REJECTED:
                self.alerts.emit("WARN", f"{intent.symbol} entry rejected: {order.history[-1][3]}", t)

    def _plan_exits(self, d: date, today: dict[str, Bar]) -> None:
        for sym, pos in self.ledger.positions.items():
            close = today[sym].close
            held = sum(1 for x in self.calendar.trading_days(pos.opened, 400) if x <= d) - 1
            if close >= pos.target_price:
                self.queued_exits.setdefault(sym, "target")
            elif close <= pos.stop_price:
                self.queued_exits.setdefault(sym, "stop breached but not filled")
                self.alerts.emit(
                    "CRITICAL", f"{sym} closed at {close} below stop {pos.stop_price}; exiting next session", self._ts(d, 15, 31)
                )
            elif held >= pos.horizon_days:
                self.queued_exits.setdefault(sym, "time exit")

    def _plan_entries(self, n: int, d: date, i: int) -> None:
        assert self.bars is not None
        t = self._ts(d, 16, 0)
        signals = []
        for inst in self.instruments.all():
            hist = self.bars[inst.symbol][: i + 1]
            for strat in APPROVED.values():
                if (sig := strat.evaluate(inst, hist)) is not None:
                    signals.append((inst, sig))
        breadth = sum(1 for s in self.bars if (m := features.sma(self.bars[s][: i + 1], 50)) and float(self.bars[s][i].close) > m)
        snapshot = AISnapshot(
            timestamp=t,
            data_as_of=self._ts(d, 15, 30),
            data_fresh=n not in self.chaos.stale_data,
            approved_strategies=[StrategyInfo(id=s.id, version=s.version, description=s.description) for s in APPROVED.values()],
            regime_factors={"breadth_above_sma50": str(round(D(breadth) / len(self.bars), 2))},
            candidates=[
                Candidate(
                    evidence_id=s.id,
                    strategy_id=s.strategy_id,
                    symbol=s.symbol,
                    exchange=inst.exchange,
                    segment=inst.segment,
                    reference_price=s.reference_price,
                    stop_price=s.stop_price,
                    target_price=s.target_price,
                    horizon_days=s.horizon_days,
                    features=s.features,
                )
                for inst, s in signals
            ],
        )
        payload = snapshot.model_dump_json()
        raw = None if n in self.chaos.ai_unavailable else self.model.propose(payload)
        if n in self.chaos.ai_malformed and raw:
            raw = raw.replace('"market_regime"', '"risk_limits": {"per_trade_risk_inr": 5000}, "market_regime"')
        result = evaluate_ai_output(raw, snapshot)
        self.store.audit(
            "ai",
            "decision",
            {
                "decision_id": result.decision_id,
                "accepted": result.accepted,
                "model": self.model.model_version,
                "snapshot_candidates": len(snapshot.candidates),
                "raw": raw,
                "rejections": list(result.rejections),
                "intents": [x.model_dump(mode="json") for x in result.intents],
            },
            t,
        )
        if not result.accepted:
            self.alerts.emit("WARN", f"AI output rejected; no AI entries tomorrow: {'; '.join(result.rejections)[:200]}", t)
            return
        held = set(self.ledger.positions)
        ranked = rank_intents([x for x in result.intents if x.symbol not in held], result.decision)
        for intent in ranked:
            self.intents[intent.unique_key] = intent
        self.queued_intents = ranked


def default_engine(db_path: Path, **kwargs: object) -> PaperEngine:
    settings = Settings(database_url=f"sqlite:///{db_path}")
    return PaperEngine(settings, **kwargs)  # type: ignore[arg-type]
