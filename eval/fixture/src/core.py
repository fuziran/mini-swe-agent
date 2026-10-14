from decimal import ROUND_HALF_UP, Decimal


def slugify(value: str) -> str:
    return "-".join("".join(char for char in word.lower() if char.isalnum()) for word in value.split())


def normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def parse_quantity(value: str) -> int:
    quantity = int(value)
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    return quantity


def parse_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("port out of range")
    return port


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ZeroDivisionError("zero denominator")
    return numerator / denominator


def page_count(total: int, size: int) -> int:
    if total < 0 or size <= 0:
        raise ValueError("invalid page size or total")
    return (total + size - 1) // size


def is_leap_year(year: int) -> bool:
    return year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)


def overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start < right_end and right_start < left_end


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def round_cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
