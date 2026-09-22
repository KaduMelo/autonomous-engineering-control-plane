import pytest

from calculator.pricing import apply_discount, running_total, subtotal


def test_subtotal_multiplies():
    assert subtotal(2.5, 4) == 10.0


def test_running_total_accumulates():
    assert running_total([1.0, 2.0, 3.0]) == [1.0, 3.0, 6.0]


def test_running_total_empty():
    assert running_total([]) == []


@pytest.mark.parametrize(
    ("price", "percent", "expected"),
    [
        (200.0, 10.0, 180.0),
        (200.0, 0.0, 200.0),
        (200.0, 100.0, 0.0),
        (50.0, 25.0, 37.5),
    ],
)
def test_apply_discount_treats_percent_as_a_percentage(price, percent, expected):
    assert apply_discount(price, percent) == pytest.approx(expected)
