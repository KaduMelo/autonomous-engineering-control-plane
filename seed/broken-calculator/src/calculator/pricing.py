"""Pricing helpers for the seed repository.

One function here is wrong on purpose. The failing test says exactly how.
"""


def subtotal(unit_price: float, quantity: int) -> float:
    """Total for `quantity` units at `unit_price`."""
    return unit_price * quantity


def running_total(values: list[float]) -> list[float]:
    """Cumulative totals, one entry per input value."""
    totals: list[float] = []
    total = 0.0
    for value in values:
        total += value
        totals.append(total)
    return totals


def apply_discount(price: float, percent: float) -> float:
    """Price after a `percent` discount.

    `percent` is a percentage (10 means ten percent), not a fraction.
    """
    return price - percent
