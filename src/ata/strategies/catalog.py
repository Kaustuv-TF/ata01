"""Approved strategy catalog. Only strategies listed here can be weighted by the AI layer.
Adding one is a code change through review, never a runtime action."""

from __future__ import annotations

from ata.strategies.base import Strategy
from ata.strategies.breakout import VolatilityBreakout
from ata.strategies.etf_rotation import EtfTrend
from ata.strategies.mean_reversion import MeanReversion
from ata.strategies.momentum import Momentum

APPROVED: dict[str, Strategy] = {s.id: s for s in (Momentum(), MeanReversion(), VolatilityBreakout(), EtfTrend())}
