from decimal import Decimal

import pytest
from src.accounts import require_user
from src.collections import stable_unique
from src.core import (
    clamp,
    is_leap_year,
    normalize_name,
    overlaps,
    page_count,
    parse_port,
    parse_quantity,
    round_cents,
    safe_ratio,
    slugify,
)
from src.invoice import invoice_total
from src.report import can_read, display_name
from src.security import safe_child


def test_slugify() -> None:
    assert slugify("Hello, WORLD!") == "hello-world"
    assert slugify("two   words") == "two-words"


def test_normalize_name() -> None:
    assert normalize_name("  Straße  Map  ") == "strasse map"
    assert normalize_name("Ada Lovelace") == "ada lovelace"


def test_parse_quantity() -> None:
    assert parse_quantity("3") == 3
    with pytest.raises(ValueError):
        parse_quantity("0")


def test_parse_port() -> None:
    assert parse_port("65535") == 65535
    with pytest.raises(ValueError):
        parse_port("65536")


def test_require_user() -> None:
    assert require_user({"a": {"name": "Ada"}}, "a") == {"name": "Ada"}
    with pytest.raises(KeyError):
        require_user({}, "missing")


def test_safe_ratio() -> None:
    assert safe_ratio(3, 2) == 1.5
    with pytest.raises(ZeroDivisionError):
        safe_ratio(1, 0)


def test_page_count() -> None:
    assert page_count(0, 10) == 0
    assert page_count(11, 10) == 2
    assert page_count(20, 10) == 2


def test_is_leap_year() -> None:
    assert is_leap_year(2000)
    assert not is_leap_year(1900)
    assert is_leap_year(2024)


def test_overlaps() -> None:
    assert overlaps(0, 5, 4, 8)
    assert not overlaps(0, 5, 5, 8)
    assert not overlaps(5, 8, 0, 5)


def test_display_name() -> None:
    assert display_name({"a": {"name": "  ADA   Lovelace "}}, "a") == "Ada Lovelace"


def test_invoice_total() -> None:
    assert invoice_total([(Decimal("2.50"), 3), (Decimal("1.00"), 2)]) == Decimal("9.50")
    assert invoice_total([]) == Decimal("0.00")


def test_can_read() -> None:
    assert can_read({"a": {"roles": ["reader"]}}, "a", "reader")
    assert not can_read({"a": {"roles": ["reader"]}}, "a", "editor")
    with pytest.raises(KeyError):
        can_read({}, "missing", "reader")


def test_clamp() -> None:
    assert clamp(-2, 0, 10) == 0
    assert clamp(5, 0, 10) == 5
    assert clamp(12, 0, 10) == 10


def test_stable_unique() -> None:
    assert stable_unique(["z", "a", "z", "b"]) == ["z", "a", "b"]
    assert stable_unique([]) == []


def test_round_cents() -> None:
    assert round_cents(Decimal("1.005")) == Decimal("1.01")
    assert round_cents(Decimal("1.004")) == Decimal("1.00")


def test_safe_child(tmp_path) -> None:
    assert safe_child(tmp_path, "inside.txt") == tmp_path / "inside.txt"
    with pytest.raises(ValueError):
        safe_child(tmp_path, f"../{tmp_path.name}-evil/file.txt")
