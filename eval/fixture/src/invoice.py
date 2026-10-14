from decimal import Decimal

from src.core import round_cents


def invoice_total(lines: list[tuple[Decimal, int]]) -> Decimal:
    return sum((round_cents(price * quantity) for price, quantity in lines), Decimal("0.00"))
