"""The oracle: how independent readings become one number, and how validators
agree on it.

This is the part of the design that a classic oracle cannot replace, so it gets
the most adversarial tests.
"""

from __future__ import annotations

import pytest

from helpers import (
    CONTRACT,
    T0,
    TOKEN,
    create_war,
    join_war,
    mock_providers,
    remock_providers,
)

PRICE = "0.00012345"
PRICE_ONE = 123450000000000


@pytest.fixture
def court(direct_vm, direct_deploy):
    direct_vm.warp(T0)
    contract = direct_deploy(CONTRACT)
    mock_providers(direct_vm, dexscreener_price=PRICE)
    return contract


def test_two_providers_average_instead_of_picking_a_side(
    direct_vm, court, direct_alice, direct_bob
):
    """With two readings the median is undefined; the midpoint is unbiased."""
    remock_providers(
        direct_vm,
        dexscreener_price="0.00010000",
        geckoterminal_price="0.00010200",
    )
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    assert court.get_war(war_id)["entry_price"] == (10 ** 14 + 102 * 10 ** 12) // 2


def test_sources_within_tolerance_are_accepted(direct_vm, court, direct_alice, direct_bob):
    """Providers quote slightly different pools; 2% apart must still settle."""
    remock_providers(
        direct_vm,
        dexscreener_price="0.00010000",
        geckoterminal_price="0.00010200",
    )
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    assert court.get_war(war_id)["status"] == "MATCHED"


def test_sources_disagreeing_beyond_tolerance_block_matching(
    direct_vm, court, direct_alice, direct_bob
):
    """A 10% spread between independent providers means one of them is wrong."""
    remock_providers(
        direct_vm,
        dexscreener_price="0.00010000",
        geckoterminal_price="0.00011000",
    )
    war_id = create_war(direct_vm, court, direct_alice)

    with direct_vm.expect_revert("providers disagree"):
        join_war(direct_vm, court, direct_bob, war_id)

    assert court.get_war(war_id)["status"] == "OPEN"


def test_thin_liquidity_is_refused(direct_vm, court, direct_alice, direct_bob):
    """Wash-traded dust pools can be pushed to any price, so they cannot settle."""
    remock_providers(
        direct_vm,
        dexscreener_price=PRICE,
        dexscreener_liquidity="100.0",
        geckoterminal_liquidity="4200000.0",
    )
    war_id = create_war(direct_vm, court, direct_alice)

    with direct_vm.expect_revert("liquidity below"):
        join_war(direct_vm, court, direct_bob, war_id)


def test_a_missing_provider_blocks_matching(direct_vm, court, direct_alice, direct_bob):
    """One provider answering is not corroboration."""
    direct_vm.clear_mocks()
    mock_providers(direct_vm, dexscreener_price=PRICE)
    direct_vm.clear_mocks()
    direct_vm.mock_web(
        r"api\.dexscreener\.com",
        {"status": 200, "body": '{"pairs":[{"priceUsd":"0.00012345","liquidity":{"usd":"4200000.0"}}]}', "method": "GET"},
    )

    war_id = create_war(direct_vm, court, direct_alice)
    with direct_vm.expect_revert("providers answered"):
        join_war(direct_vm, court, direct_bob, war_id)


def test_http_error_is_not_treated_as_a_price(direct_vm, court, direct_alice, direct_bob):
    direct_vm.clear_mocks()
    direct_vm.mock_web(r"api\.dexscreener\.com", {"status": 503, "body": "upstream down", "method": "GET"})
    mock_providers(direct_vm, dexscreener_price=PRICE)

    war_id = create_war(direct_vm, court, direct_alice)
    with direct_vm.expect_revert("providers answered"):
        join_war(direct_vm, court, direct_bob, war_id)


# --- validator behaviour -----------------------------------------------------


def test_validator_agrees_when_the_second_read_matches(
    direct_vm, court, direct_alice, direct_bob
):
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    assert direct_vm.run_validator() is True


def test_validator_agrees_within_its_tolerance_band(
    direct_vm, court, direct_alice, direct_bob
):
    """The price moves while the leader and the validator execute in turn.

    This is the whole reason a numeric tolerance exists: a byte-exact comparison
    of two live DEX pages would never agree, and the war could never settle.
    """
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    remock_providers(direct_vm, dexscreener_price="0.00012469")  # +1.0%
    assert direct_vm.run_validator() is True


def test_validator_rejects_a_price_beyond_its_tolerance(
    direct_vm, court, direct_alice, direct_bob
):
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    remock_providers(direct_vm, dexscreener_price="0.00013579")  # +10%
    assert direct_vm.run_validator() is False


def test_validator_rejects_when_it_cannot_read_the_market(
    direct_vm, court, direct_alice, direct_bob
):
    """A validator that cannot corroborate must disagree, not abstain."""
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    direct_vm.clear_mocks()
    assert direct_vm.run_validator() is False


def test_validator_rejects_a_leader_error(direct_vm, court, direct_alice, direct_bob):
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    assert direct_vm.run_validator(leader_error=ValueError("provider timeout")) is False
