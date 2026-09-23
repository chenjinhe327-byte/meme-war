"""Settlement: who gets paid, who gets refunded, and what happens when the
market data cannot be trusted."""

from __future__ import annotations

import pytest

from helpers import (
    ATTEMPT_COOLDOWN,
    CONTRACT,
    STAKE,
    T0,
    TOKEN,
    create_war,
    iso,
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
    """A sustained outage must not strand the stakes forever.

    Note the warps: without them the second and third calls are refused by the
    retry cooldown, which is the point of the next test.
    """
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    direct_vm.clear_mocks()

    assert contract.resolve_war(war_id) == "VOID"
    war = contract.get_war(war_id)
    assert war["status"] == "MATCHED"  # retryable
    assert war["attempts"] == 1
    assert contract.get_claim(direct_alice.as_hex) == 0

    direct_vm.warp(iso(war["last_attempt_at"] + ATTEMPT_COOLDOWN + 1))
    contract.resolve_war(war_id)
    assert contract.get_war(war_id)["attempts"] == 2

    war = contract.get_war(war_id)
    direct_vm.warp(iso(war["last_attempt_at"] + ATTEMPT_COOLDOWN + 1))
    contract.resolve_war(war_id)

    war = contract.get_war(war_id)
    assert war["attempts"] == 3
    assert war["status"] == "VOID"
    assert contract.get_claim(direct_alice.as_hex) == STAKE
    assert contract.get_claim(direct_bob.as_hex) == STAKE


def test_rapid_retries_cannot_force_a_refund(direct_vm, matched, direct_alice, direct_bob):
    """Resolution is permissionless, which is exactly why it is rate limited.

    Anyone can drive settlement, so without a cooldown a griefer could call it
    three times in a row during a thirty-second provider outage and force the war
    to void - robbing a winner who did nothing wrong.
    """
    contract, war_id = matched
    direct_vm.warp(AFTER_EXPIRY)
    direct_vm.clear_mocks()

    assert contract.resolve_war(war_id) == "VOID"  # first attempt is allowed

    for _ in range(5):
        with direct_vm.expect_revert("cooldown"):
            contract.resolve_war(war_id)

    war = contract.get_war(war_id)
    assert war["attempts"] == 1  # not MAX_ATTEMPTS
    assert war["status"] == "MATCHED"  # no refund forced
    assert contract.get_claim(direct_alice.as_hex) == 0
    assert contract.get_claim(direct_bob.as_hex) == 0


def test_a_refund_cannot_happen_faster_than_the_cooldown_allows(
    direct_vm, matched, direct_alice
):
    """The minimum time from first attempt to refund is bounded by the config."""
    contract, war_id = matched
    config = contract.get_config()

    assert config["attempt_cooldown_seconds"] == ATTEMPT_COOLDOWN
    assert config["min_seconds_to_void"] == (config["max_attempts"] - 1) * ATTEMPT_COOLDOWN
    assert config["min_seconds_to_void"] >= 1200

    direct_vm.warp(AFTER_EXPIRY)
    direct_vm.clear_mocks()

    contract.resolve_war(war_id)
    war = contract.get_war(war_id)

    # One second short of the cooldown is still refused.
    direct_vm.warp(iso(war["last_attempt_at"] + ATTEMPT_COOLDOWN - 1))
    with direct_vm.expect_revert("cooldown"):
        contract.resolve_war(war_id)


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
