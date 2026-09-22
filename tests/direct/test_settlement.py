"""Settlement: who gets paid, who gets refunded, and what happens when the
market data cannot be trusted."""

from __future__ import annotations

import pytest

from helpers import (
    CONTRACT,
    STAKE,
    T0,
    TOKEN,
    create_war,
    join_war,
    mock_providers,
    remock_providers,
)

ENTRY = "0.00012345"
DAY = 86400
AFTER_EXPIRY = "2026-01-02T00:00:01Z"


@pytest.fixture
def matched(direct_vm, direct_deploy, direct_alice, direct_bob):
    """A matched war: alice took UP, bob took DOWN."""
    direct_vm.warp(T0)
    contract = direct_deploy(CONTRACT)
    mock_providers(direct_vm, dexscreener_price=ENTRY)
    war_id = create_war(direct_vm, contract, direct_alice, side="UP")
    join_war(direct_vm, contract, direct_bob, war_id)
    return contract, war_id


def test_cannot_resolve_before_expiry(direct_vm, matched, direct_alice):
    contract, war_id = matched
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("has not expired yet"):
        contract.resolve_war(war_id)


def test_unmatched_war_cannot_be_resolved(direct_vm, direct_deploy, direct_alice):
    """An open war has no counterparty and no entry price, so it can never settle."""
    direct_vm.warp(T0)
    contract = direct_deploy(CONTRACT)
    mock_providers(direct_vm, dexscreener_price=ENTRY)
    war_id = create_war(direct_vm, contract, direct_alice, side="UP")

    direct_vm.warp(AFTER_EXPIRY)
    with direct_vm.expect_revert("not awaiting settlement"):
        contract.resolve_war(war_id)


def test_up_side_wins_the_whole_pot(direct_vm, matched, direct_alice, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(direct_vm, dexscreener_price="0.00020000")

    direct_vm.sender = direct_bob  # settlement is permissionless
    assert contract.resolve_war(war_id) == "UP"

    war = contract.get_war(war_id)
    assert war["status"] == "RESOLVED"
    assert war["winner"] == "UP"
    assert war["exit_price"] == 200000000000000
    assert contract.get_claim(direct_alice.as_hex) == STAKE * 2
    assert contract.get_claim(direct_bob.as_hex) == 0


def test_down_side_wins_when_the_price_falls(direct_vm, matched, direct_alice, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(direct_vm, dexscreener_price="0.00005000")

    assert contract.resolve_war(war_id) == "DOWN"

    assert contract.get_claim(direct_bob.as_hex) == STAKE * 2
    assert contract.get_claim(direct_alice.as_hex) == 0


def test_a_flat_price_voids_rather_than_picking_a_side(direct_vm, matched, direct_alice, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(direct_vm, dexscreener_price=ENTRY)

    assert contract.resolve_war(war_id) == "VOID"
    assert contract.get_war(war_id)["status"] == "VOID"
    assert contract.get_claim(direct_alice.as_hex) == STAKE
    assert contract.get_claim(direct_bob.as_hex) == STAKE


def test_unreliable_data_voids_and_refunds_both_sides(
    direct_vm, matched, direct_alice, direct_bob
):
    """Refusing to pay always beats paying the wrong side."""
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(
        direct_vm,
        dexscreener_price="0.00020000",
        geckoterminal_price="0.00026000",  # 30% away
    )

    assert contract.resolve_war(war_id) == "VOID"
    assert contract.get_claim(direct_alice.as_hex) == STAKE
    assert contract.get_claim(direct_bob.as_hex) == STAKE


def test_thin_liquidity_at_expiry_voids(direct_vm, matched, direct_alice, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(
        direct_vm,
        dexscreener_price="0.00020000",
        dexscreener_liquidity="50.0",
        geckoterminal_liquidity="4200000.0",
    )

    assert contract.resolve_war(war_id) == "VOID"
    assert contract.get_claim(direct_alice.as_hex) == STAKE


def test_repeated_data_failures_void_the_war(direct_vm, matched, direct_alice, direct_bob):
    """A transient outage must not strand the stakes forever."""
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    direct_vm.clear_mocks()

    assert contract.resolve_war(war_id) == "VOID"
    assert contract.get_war(war_id)["status"] == "MATCHED"  # retryable
    assert contract.get_war(war_id)["attempts"] == 1
    assert contract.get_claim(direct_alice.as_hex) == 0

    contract.resolve_war(war_id)
    assert contract.get_war(war_id)["attempts"] == 2

    contract.resolve_war(war_id)
    war = contract.get_war(war_id)
    assert war["attempts"] == 3
    assert war["status"] == "VOID"
    assert contract.get_claim(direct_alice.as_hex) == STAKE
    assert contract.get_claim(direct_bob.as_hex) == STAKE


def test_war_cannot_be_settled_twice(direct_vm, matched, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(direct_vm, dexscreener_price="0.00020000")

    contract.resolve_war(war_id)
    with direct_vm.expect_revert("not awaiting settlement"):
        contract.resolve_war(war_id)


def test_unknown_war_is_rejected(direct_vm, matched):
    contract, _ = matched
    direct_vm.warp(AFTER_EXPIRY)

    with direct_vm.expect_revert("unknown war"):
        contract.resolve_war("0x" + "00" * 32)


def test_claim_requires_a_credit(direct_vm, matched, direct_bob):
    contract, _ = matched
    direct_vm.sender = direct_bob

    with direct_vm.expect_revert("nothing to claim"):
        contract.claim()


def test_void_refunds_do_not_stack_on_repeat_resolution(
    direct_vm, matched, direct_alice, direct_bob
):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(direct_vm, dexscreener_price=ENTRY)

    contract.resolve_war(war_id)
    with direct_vm.expect_revert("not awaiting settlement"):
        contract.resolve_war(war_id)

    assert contract.get_claim(direct_alice.as_hex) == STAKE
    assert contract.get_claim(direct_bob.as_hex) == STAKE


def test_total_settled_counter_advances(direct_vm, matched, direct_bob):
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    remock_providers(direct_vm, dexscreener_price="0.00020000")

    contract.resolve_war(war_id)
    assert contract.get_config()["total_settled"] == 1
