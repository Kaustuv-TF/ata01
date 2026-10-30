"""Runtime settings. Defaults are paper-only; live trading cannot be enabled in this build."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ata.brokers.base import LiveTradingDisabled

ROOT = Path(__file__).resolve().parents[2]


def _flag(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes"}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_mode: Literal["paper", "live"] = "paper"
    live_trading_enabled: bool = False
    broker_compliance_gate: bool = False
    database_url: str = f"sqlite:///{ROOT / 'var' / 'ata.db'}"
    config_dir: Path = ROOT / "config"
    report_dir: Path = ROOT / "var" / "reports"
    review_cycle_days: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.environ.get("ATA_MARKET_MODE", "paper")
        if mode not in {"paper", "live"}:
            raise ValueError(f"ATA_MARKET_MODE must be paper or live, got {mode!r}")
        kwargs: dict[str, object] = {
            "market_mode": mode,
            "live_trading_enabled": _flag("ATA_LIVE_TRADING_ENABLED"),
            "broker_compliance_gate": _flag("ATA_BROKER_COMPLIANCE_GATE"),
        }
        for env, key in (("ATA_DATABASE_URL", "database_url"), ("ATA_CONFIG_DIR", "config_dir"), ("ATA_REPORT_DIR", "report_dir")):
            if env in os.environ:
                kwargs[key] = os.environ[env] if key == "database_url" else Path(os.environ[env])
        return cls.model_validate(kwargs)

    def assert_paper_only(self) -> None:
        """Live requires: owner activation, compliance gate, verified broker adapter. None exist yet."""
        if self.market_mode != "paper" or self.live_trading_enabled or self.broker_compliance_gate:
            raise LiveTradingDisabled(
                "Live trading is not available in this build. It requires broker onboarding proof, "
                "a verified Paytm Money adapter, Gates 0-3 passed, and a separate owner activation."
            )
