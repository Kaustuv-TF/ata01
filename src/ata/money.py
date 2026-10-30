"""Decimal helpers. All money and prices are Decimal; floats never touch P&L."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

ZERO = Decimal("0")
PAISA = Decimal("0.01")


def D(value: object) -> Decimal:
    """Convert to Decimal via str so floats don't leak binary noise."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def inr(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def floor_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def ceil_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_UP) * tick


def round_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick
