"""War lifecycle: opening, matching, staking rules and cancellation."""

from __future__ import annotations

import pytest

from helpers import (
    CHAIN,
    CONTRACT,
    STAKE,
    SYMBOL,
    T0,
    TOKEN,
    create_war,
    iso,
    join_war,
    mock_providers,
)

MATCH_WINDOW = 6 * 3600
DAY = 86400


@pytest.fixture
def court(direct_vm, direct_deploy):
    direct_vm.warp(T0)
    contract = direct_deploy(CONTRACT)
    mock_providers(direct_vm)
    return contract


def test_create_war_records_the_first_side(direct_vm, court, direct_alice):
    war_id = create_war(direct_vm, court, direct_alice)

    assert war_id.startswith("0x") and len(war_id) == 66
    war = court.get_war(war_id)
    assert war["creator"] == direct_alice.as_hex
    assert war["creator_side"] == "UP"
    assert war["stake"] == STAKE
    assert war["pot"] == STAKE  # one side in, pot not yet matched
    assert war["status"] == "OPEN"
    assert war["token_symbol"] == SYMBOL
    assert war["chain"] == CHAIN
    assert war["match_deadline"] == war["created_at"] + MATCH_WINDOW
    # The wager clock has not started: it starts when somebody takes the other
    # side and the entry price is fixed.
    assert war["matched_at"] == 0
    assert war["resolve_at"] == 0
    assert court.get_config()["total_wars"] == 1


def test_the_wager_window_starts_when_the_war_is_matched(
    direct_vm, court, direct_alice, direct_bob
):
    """A war that waited five hours for an opponent must still give both players
    the full window they signed up for."""
    war_id = create_war(direct_vm, court, direct_alice, timeframe="1h")
    created = court.get_war(war_id)["created_at"]
    assert court.get_war(war_id)["resolve_at"] == 0

    joined_at = created + 5 * 3600
    direct_vm.warp(iso(joined_at))
    join_war(direct_vm, court, direct_bob, war_id)

    war = court.get_war(war_id)
    assert war["status"] == "MATCHED"
    assert war["matched_at"] == joined_at
    assert war["resolve_at"] == joined_at + 3600
    # Starting the clock at creation would have put the expiry in the past
    # before the war had even begun.
    assert war["resolve_at"] > created + 3600


def test_a_late_join_cannot_be_resolved_immediately(
    direct_vm, court, direct_alice, direct_bob
):
    war_id = create_war(direct_vm, court, direct_alice, timeframe="1h")
    created = court.get_war(war_id)["created_at"]

    direct_vm.warp(iso(created + 5 * 3600))
    join_war(direct_vm, court, direct_bob, war_id)

    direct_vm.warp(iso(created + 5 * 3600 + 60))  # one minute after matching
    with direct_vm.expect_revert("has not expired yet"):
        court.resolve_war(war_id)


def test_joining_just_inside_the_match_window_is_still_allowed(
    direct_vm, court, direct_alice, direct_bob
):
    war_id = create_war(direct_vm, court, direct_alice, timeframe="1h")
    created = court.get_war(war_id)["created_at"]

    direct_vm.warp(iso(created + MATCH_WINDOW - 1))
    join_war(direct_vm, court, direct_bob, war_id)

    assert court.get_war(war_id)["status"] == "MATCHED"


def test_create_war_requires_a_stake(direct_vm, court, direct_alice):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("stake is required"):
        court.create_war(SYMBOL, TOKEN, CHAIN, "24h", "UP")


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"chain": "dogechain"}, "unsupported chain"),
        ({"timeframe": "3m"}, "unsupported timeframe"),
        ({"side": "SIDEWAYS"}, "side must be UP or DOWN"),
        ({"symbol": "   "}, "symbol length out of range"),
        ({"address": ""}, "token address length out of range"),
    ],
)
def test_create_war_validates_input(direct_vm, court, direct_alice, kwargs, message):
    with direct_vm.expect_revert(message):
        create_war(direct_vm, court, direct_alice, **kwargs)


def test_join_matches_the_stake_and_locks_entry_price(
    direct_vm, court, direct_alice, direct_bob
):
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    war = court.get_war(war_id)
    assert war["status"] == "MATCHED"
    assert war["opponent"] == direct_bob.as_hex
    assert war["pot"] == STAKE * 2
    assert war["entry_price"] > 0
    assert war["entry_liquidity"] > 0


def test_join_requires_an_exactly_equal_stake(direct_vm, court, direct_alice, direct_bob):
    war_id = create_war(direct_vm, court, direct_alice)

    with direct_vm.expect_revert("stake must match"):
        join_war(direct_vm, court, direct_bob, war_id, stake=STAKE + 1)
    with direct_vm.expect_revert("stake must match"):
        join_war(direct_vm, court, direct_bob, war_id, stake=STAKE - 1)


def test_creator_cannot_take_both_sides(direct_vm, court, direct_alice):
    war_id = create_war(direct_vm, court, direct_alice)

    with direct_vm.expect_revert("cannot take both sides"):
        join_war(direct_vm, court, direct_alice, war_id)


def test_war_cannot_be_joined_twice(direct_vm, court, direct_alice, direct_bob, direct_charlie):
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)

    with direct_vm.expect_revert("war is not open"):
        join_war(direct_vm, court, direct_charlie, war_id)


def test_join_closes_with_the_matching_window(direct_vm, court, direct_alice, direct_bob):
    war_id = create_war(direct_vm, court, direct_alice)
    direct_vm.warp("2026-01-01T06:00:01Z")  # one second past the window

    with direct_vm.expect_revert("matching window has closed"):
        join_war(direct_vm, court, direct_bob, war_id)


def test_unmatched_war_is_refundable_after_the_window(direct_vm, court, direct_alice):
    war_id = create_war(direct_vm, court, direct_alice)
    direct_vm.warp("2026-01-01T06:00:01Z")

    direct_vm.sender = direct_alice
    court.cancel_open_war(war_id)

    war = court.get_war(war_id)
    assert war["status"] == "VOID"
    assert war["winner"] == "VOID"
    assert court.get_claim(direct_alice.as_hex) == STAKE


def test_open_war_cannot_be_cancelled_early(direct_vm, court, direct_alice):
    war_id = create_war(direct_vm, court, direct_alice)
    direct_vm.sender = direct_alice

    with direct_vm.expect_revert("matching window is still open"):
        court.cancel_open_war(war_id)


def test_only_the_creator_can_cancel(direct_vm, court, direct_alice, direct_bob):
    war_id = create_war(direct_vm, court, direct_alice)
    direct_vm.warp("2026-01-01T06:00:01Z")

    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("only the creator can cancel"):
        court.cancel_open_war(war_id)


def test_matched_war_cannot_be_cancelled(direct_vm, court, direct_alice, direct_bob):
    war_id = create_war(direct_vm, court, direct_alice)
    join_war(direct_vm, court, direct_bob, war_id)
    direct_vm.warp("2026-01-01T06:00:01Z")

    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("war is not open"):
        court.cancel_open_war(war_id)


def test_listings_expose_open_and_owned_wars(direct_vm, court, direct_alice, direct_bob):
    first = create_war(direct_vm, court, direct_alice, side="UP")
    second = create_war(direct_vm, court, direct_bob, side="DOWN")
    join_war(direct_vm, court, direct_bob, first)

    open_ids = [war["war_id"] for war in court.list_open(10)]
    assert first not in open_ids  # matched, no longer open
    assert second in open_ids

    assert len(court.list_by_owner(direct_alice.as_hex, 10)) == 1
    assert len(court.list_by_owner(direct_bob.as_hex, 10)) == 2


def test_sources_are_chosen_by_the_contract_not_the_player(direct_vm, court):
    """A player who could name the price source could name a fake one."""
    sources = court.get_sources("base", TOKEN)
    providers = sorted(source["provider"] for source in sources)

    assert providers == ["dexscreener", "geckoterminal"]
    for source in sources:
        assert TOKEN in source["url"]
        assert source["url"].startswith("https://")

    with direct_vm.expect_revert("unsupported chain"):
        court.get_sources("dogechain", TOKEN)
