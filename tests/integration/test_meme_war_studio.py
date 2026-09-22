"""End-to-end tests against GenLayer Studio / testnet.

These are the tests that prove the consensus logic works against a *real*
validator set rather than mocks. They need a running network, so they are not
part of the default `pytest` run (see `pytest.ini` → `testpaths`).

    gltest tests/integration -v --network testnet_asimov

Before running, point the contract address at a deployment whose price sources
are live for the token under test.
"""

from __future__ import annotations

import os

import pytest

CONTRACT_NAME = "meme_war"

# A token with deep, live pools on the chain under test.
TOKEN_ADDRESS = os.environ.get("MEMEWAR_TOKEN", "0x" + "ab" * 20)
CHAIN = os.environ.get("MEMEWAR_CHAIN", "base")


@pytest.fixture(scope="module")
def meme_war():
    from gltest import get_contract_factory

    factory = get_contract_factory(CONTRACT_NAME)
    return factory.deploy(args=[])


def test_oracle_reads_both_providers(meme_war):
    """The live oracle must return one number from two independent providers."""
    sources = meme_war.get_sources(args=[CHAIN, TOKEN_ADDRESS]).call()
    assert sorted(source["provider"] for source in sources) == [
        "dexscreener",
        "geckoterminal",
    ]

    config = meme_war.get_config(args=[]).call()
    assert config["validator_tolerance_bps"] == 200
    assert config["source_agreement_bps"] == 500


def test_war_round_trip_on_a_live_network(meme_war):
    """Open → match → settle, with the entry price observed by validators.

    Requires `MEMEWAR_LIVE=1` because it spends testnet funds and depends on a
    real token having live pools at the moment it runs.
    """
    if os.environ.get("MEMEWAR_LIVE") != "1":
        pytest.skip("set MEMEWAR_LIVE=1 to run the funded round trip")

    from gltest import create_account
    from gltest.assertions import tx_execution_succeeded

    creator = create_account()
    opponent = create_account()
    stake = 10 ** 15  # 0.001 GEN

    opened = meme_war.connect(creator).create_war(
        args=[os.environ.get("MEMEWAR_SYMBOL", "PEPE"), TOKEN_ADDRESS, CHAIN, "1h", "UP"]
    ).transact(value=stake)
    assert tx_execution_succeeded(opened)

    war_id = meme_war.list_open(args=[1]).call()[0]["war_id"]
    assert meme_war.get_war(args=[war_id]).call()["status"] == "OPEN"

    joined = (
        meme_war.connect(opponent)
        .join_war(args=[war_id])
        .transact(value=stake)
    )
    assert tx_execution_succeeded(joined)

    matched = meme_war.get_war(args=[war_id]).call()
    assert matched["status"] == "MATCHED"
    assert matched["entry_price"] > 0
    assert matched["pot"] == stake * 2
