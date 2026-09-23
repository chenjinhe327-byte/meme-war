"""Validators must agree on the winner, not merely on the price.

Agreeing on a number is not the same as agreeing on who gets paid. Two readings
can sit comfortably inside the price tolerance band and still fall on opposite
sides of the entry price, in which case a price-only check accepts the pair and
the war settles on whichever node happened to lead.

These tests pin that down: the readings below are deliberately 20 bps apart,
which is *inside* the 200 bps band, so a price-only check would accept them.
"""

from __future__ import annotations

import pytest

from helpers import (
    CONTRACT,
    T0,
    T0_EPOCH,
    create_war,
    iso,
    join_war,
    mock_providers,
    remock_providers,
)

ENTRY = "1"
HOUR = 3600
AFTER_EXPIRY = iso(T0_EPOCH + HOUR + 1)


@pytest.fixture
def matched(direct_vm, direct_deploy, direct_alice, direct_bob):
    """Alice backs UP, bob backs DOWN, entry price fixed at 1.0."""
    direct_vm.warp(T0)
    contract = direct_deploy(CONTRACT)
    mock_providers(direct_vm, dexscreener_price=ENTRY)
    war_id = create_war(direct_vm, contract, direct_alice, side="UP", timeframe="1h")
    join_war(direct_vm, contract, direct_bob, war_id)
    return contract, war_id


def test_entry_price_is_one_whole_unit(matched):
    contract, war_id = matched
    assert contract.get_war(war_id)["entry_price"] == 10 ** 18


def test_validator_rejects_readings_that_straddle_the_entry_price(
    direct_vm, matched, direct_bob
):
    """The regression this whole check exists for.

    Leader reads 1.001 (+10 bps, UP). A validator reading 0.999 (-10 bps, DOWN)
    is 20 bps away from the leader - well inside the 200 bps price tolerance - yet
    implies the opposite winner. Consensus must fail.
    """
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price="1.001")
    assert contract.resolve_war(war_id) == "UP"

    remock_providers(direct_vm, dexscreener_price="0.999")
    assert direct_vm.run_validator() is False


def test_validator_agrees_when_both_readings_pick_the_same_side(
    direct_vm, matched, direct_bob
):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price="1.001")
    assert contract.resolve_war(war_id) == "UP"

    remock_providers(direct_vm, dexscreener_price="1.002")  # also UP, within band
    assert direct_vm.run_validator() is True


def test_validator_rejects_a_reading_that_flips_down_to_up(
    direct_vm, matched, direct_bob
):
    """The mirror image: leader DOWN, validator UP."""
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price="0.999")
    assert contract.resolve_war(war_id) == "DOWN"

    remock_providers(direct_vm, dexscreener_price="1.001")
    assert direct_vm.run_validator() is False


def test_validator_agrees_that_a_flat_price_is_a_void(direct_vm, matched):
    """A price that did not move has no winner; both sides must see that."""
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price=ENTRY)
    assert contract.resolve_war(war_id) == "VOID"

    assert direct_vm.run_validator() is True


def test_a_flat_price_refunds_both_sides(direct_vm, matched, direct_alice, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price=ENTRY)
    assert contract.resolve_war(war_id) == "VOID"

    war = contract.get_war(war_id)
    assert war["status"] == "VOID"
    assert war["winner"] == "VOID"
    assert contract.get_claim(direct_alice.as_hex) == war["stake"]
    assert contract.get_claim(direct_bob.as_hex) == war["stake"]


def test_validator_still_enforces_the_price_band_as_well(
    direct_vm, matched, direct_bob
):
    """Same winner is necessary but not sufficient: a wildly different number is
    still rejected, because it means one of them read the wrong market."""
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price="1.001")
    assert contract.resolve_war(war_id) == "UP"

    remock_providers(direct_vm, dexscreener_price="1.500")  # also UP, 50% away
    assert direct_vm.run_validator() is False


def test_the_recorded_winner_matches_the_recorded_prices(direct_vm, matched):
    """The winner stored on chain must follow from the entry/exit pair stored
    beside it — a reader should never have to trust the label alone."""
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)

    remock_providers(direct_vm, dexscreener_price="1.500")
    assert contract.resolve_war(war_id) == "UP"

    war = contract.get_war(war_id)
    assert war["exit_price"] > war["entry_price"]
    assert war["winner"] == "UP"

    # and the loser's side is the other one
    assert war["creator_side"] == "UP"
