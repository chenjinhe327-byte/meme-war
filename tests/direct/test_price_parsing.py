"""The numeric parser in front of the oracle.

Every value that can move money is converted by this code, so it is tested
against the formats providers actually emit rather than a happy path.
"""

from __future__ import annotations

import pytest

CONTRACT = "contracts/meme_war.py"


@pytest.fixture
def court(direct_vm, direct_deploy):
    direct_vm.warp("2026-01-01T00:00:00Z")
    return direct_deploy(CONTRACT)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.00012345", 123450000000000),
        ("1", 10 ** 18),
        ("1.0", 10 ** 18),
        ("$1,234.56", 1234560000000000000000),
        ("  42  ", 42 * 10 ** 18),
        ("1.2e-7", 120000000000),
        ("2e3", 2000 * 10 ** 18),
        ("0", 0),
        ("-1.5", -(15 * 10 ** 17)),
        ("0.1", 10 ** 17),
    ],
)
def test_parses_provider_number_formats(court, raw, expected):
    assert court.preview_sample(raw)["price_scaled"] == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "abc", "1.2.3", "0x10", "1,2,3.4.5", "--1", "1e", "NaN", "Infinity"],
)
def test_rejects_unparseable_values(court, raw):
    """Garbage must become None, never a number that could settle a war."""
    assert court.preview_sample(raw)["price_scaled"] is None


def test_usd_scale_is_six_decimals(court):
    """Liquidity uses a different scale to price; mixing them would be a bug."""
    sample = court.preview_sample("4200000.0")
    assert sample["usd_scaled"] == 4200000 * 10 ** 6
    assert sample["price_scaled"] == 4200000 * 10 ** 18


def test_exponent_bomb_is_rejected(court):
    assert court.preview_sample("1e400")["price_scaled"] is None


def test_no_float_rounding_drift(court):
    """0.1 + 0.2 style drift must not exist: parsing is pure integer math."""
    assert court.preview_sample("0.30000000000000004")["price_scaled"] == 300000000000000040
    assert court.preview_sample("0.3")["price_scaled"] == 300000000000000000
