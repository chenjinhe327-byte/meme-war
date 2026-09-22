"""Shared fixtures for the direct-mode suite."""

from __future__ import annotations

import json

CONTRACT = "contracts/meme_war.py"

T0 = "2026-01-01T00:00:00Z"
T0_EPOCH = 1767225600

HOUR = 3600
DAY = 86400

TOKEN = "0x" + "ab" * 20
CHAIN = "base"
SYMBOL = "PEPE"
STAKE = 100

# Comfortably above MIN_LIQUIDITY_USD (25_000).
DEEP_LIQUIDITY = "4200000.0"


def dexscreener_body(price: str, liquidity_usd: str = DEEP_LIQUIDITY) -> str:
    return json.dumps(
        {
            "pairs": [
                {
                    "priceUsd": price,
                    "liquidity": {"usd": liquidity_usd},
                    "dexId": "uniswap",
                },
                {
                    # A dust pool must never win the "deepest pool" pick, no
                    # matter how thin the main pool is asked to be.
                    "priceUsd": "0.0001",
                    "liquidity": {"usd": "1.0"},
                    "dexId": "dust",
                },
            ]
        }
    )


def geckoterminal_body(price: str, liquidity_usd: str = DEEP_LIQUIDITY) -> str:
    return json.dumps(
        {
            "data": {
                "attributes": {
                    "price_usd": price,
                    "total_reserve_in_usd": liquidity_usd,
                }
            }
        }
    )


def mock_providers(
    vm,
    *,
    dexscreener_price: str = "0.00012345",
    geckoterminal_price: str | None = None,
    dexscreener_liquidity: str = DEEP_LIQUIDITY,
    geckoterminal_liquidity: str = DEEP_LIQUIDITY,
) -> None:
    """Register both canonical providers for the default token."""
    if geckoterminal_price is None:
        geckoterminal_price = dexscreener_price
    vm.mock_web(
        r"api\.dexscreener\.com",
        {
            "status": 200,
            "body": dexscreener_body(dexscreener_price, dexscreener_liquidity),
            "method": "GET",
        },
    )
    vm.mock_web(
        r"api\.geckoterminal\.com",
        {
            "status": 200,
            "body": geckoterminal_body(geckoterminal_price, geckoterminal_liquidity),
            "method": "GET",
        },
    )


def remock_providers(vm, **kwargs) -> None:
    vm.clear_mocks()
    mock_providers(vm, **kwargs)


def create_war(
    vm,
    contract,
    sender,
    *,
    stake: int = STAKE,
    symbol: str = SYMBOL,
    address: str = TOKEN,
    chain: str = CHAIN,
    timeframe: str = "24h",
    side: str = "UP",
) -> str:
    vm.sender = sender
    vm.value = stake
    try:
        return contract.create_war(symbol, address, chain, timeframe, side)
    finally:
        vm.value = 0


def join_war(vm, contract, sender, war_id: str, *, stake: int = STAKE) -> None:
    vm.sender = sender
    vm.value = stake
    try:
        contract.join_war(war_id)
    finally:
        vm.value = 0
