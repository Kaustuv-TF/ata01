"""INR 150 daily freeze, INR 500 persistent drawdown lockout, kill switch, owner-only clearing."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from ata.data.calendar import IST
from ata.money import D
from ata.ops.store import Store
from ata.risk.limits import RiskLimits
from ata.risk.state import RiskState

D1, D2 = date(2026, 11, 3), date(2026, 11, 4)
T1 = datetime(2026, 11, 3, 15, 30, tzinfo=IST)
T2 = datetime(2026, 11, 4, 15, 30, tzinfo=IST)


@pytest.fixture
def state(store: Store, limits: RiskLimits) -> RiskState:
    return RiskState(store, limits)


def test_daily_freeze_at_inr_150(state: RiskState) -> None:
    state.start_day(D1, D(10000))
    assert state.on_equity(D1, D("9850.01"), T1) == []
    assert state.daily_freeze(D1) is None
    assert state.on_equity(D1, D(9850), T1) == ["daily_freeze"]
    assert state.daily_freeze(D1) is not None


def test_daily_freeze_resets_next_trading_day(state: RiskState) -> None:
    state.start_day(D1, D(10000))
    state.on_equity(D1, D(9800), T1)
    state.start_day(D2, D(9800))
    assert state.daily_freeze(D2) is None
    assert state.day_start_equity(D2) == D(9800)


def test_daily_threshold_follows_current_equity(state: RiskState, limits: RiskLimits) -> None:
    assert limits.daily_loss_freeze(D(9000)) == D(135)
    state.start_day(D1, D(9000))
    assert state.on_equity(D1, D(8865), T1) == ["daily_freeze", "drawdown_lockout"]


def test_start_day_is_idempotent(state: RiskState) -> None:
    state.start_day(D1, D(10000))
    state.start_day(D1, D(9000))  # a restart mid-day must not rebase the day's start
    assert state.day_start_equity(D1) == D(10000)


def test_drawdown_lockout_at_inr_500_from_high_water_mark(state: RiskState) -> None:
    state.on_equity(D1, D(10300), T1)
    assert state.high_water_mark == D(10300)
    assert state.on_equity(D2, D("9800.01"), T2) == []
    assert state.on_equity(D2, D(9800), T2) == ["drawdown_lockout"]
    assert state.lockout() is not None


def test_lockout_survives_restart(tmp_path: Path, limits: RiskLimits) -> None:
    url = f"sqlite:///{tmp_path / 'persist.db'}"
    RiskState(Store(url), limits).on_equity(D1, D(9400), T1)
    restarted = RiskState(Store(url), limits)
    assert restarted.lockout() is not None
    assert restarted.high_water_mark == D(10000)
    restarted.on_equity(D2, D(11000), T2)  # recovery does not auto-clear
    assert restarted.lockout() is not None


def test_kill_switch_survives_restart(tmp_path: Path, limits: RiskLimits) -> None:
    url = f"sqlite:///{tmp_path / 'ks.db'}"
    RiskState(Store(url), limits).engage_kill_switch("owner", "manual halt", T1)
    assert RiskState(Store(url), limits).kill_switch() is not None


@pytest.mark.parametrize("author", ["ai", "LLM", "model", "agent", "system", " "])
def test_non_human_cannot_clear_lockout(state: RiskState, author: str) -> None:
    state.on_equity(D1, D(9400), T1)
    with pytest.raises(PermissionError):
        state.owner_clear("drawdown_lockout", author, "because the model said so", T2, D(9400))
    assert state.lockout() is not None


def test_clear_requires_reason(state: RiskState) -> None:
    state.on_equity(D1, D(9400), T1)
    with pytest.raises(PermissionError):
        state.owner_clear("drawdown_lockout", "Sourav", "ok", T2, D(9400))


def test_owner_clear_rebases_hwm_and_is_recorded(state: RiskState, store: Store) -> None:
    state.on_equity(D1, D(9400), T1)
    state.owner_clear("drawdown_lockout", "Sourav Patra", "30-day review complete; resume paper", T2, D(9400))
    assert state.lockout() is None
    assert state.high_water_mark == D(9400)
    assert [e["type"] for e in store.control_events()] == ["drawdown_lockout", "drawdown_lockout_cleared"]


def test_only_known_blocks_are_clearable(state: RiskState) -> None:
    with pytest.raises(ValueError):
        state.owner_clear("high_water_mark", "Sourav", "reset everything please", T2, D(10000))
