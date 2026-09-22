"""The pre-flight tool and the contract must agree on policy.

`tools/check_token.py` exists so a token can be checked *before* a war is opened
on it. If its thresholds drifted from the contract's, it would cheerfully pass
tokens the contract then voids - which is worse than having no tool at all.

The contract cannot be imported outside GenVM (`from genlayer import *`), so the
contract's constants are read out of its source with `ast` instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tools import check_token

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "meme_war.py"

# Policy values that must be identical on both sides.
SHARED_SCALARS = (
    "MIN_LIQUIDITY_USD",
    "SOURCE_AGREEMENT_BPS",
    "LIQUIDITY_DECIMALS",
)


def _contract_constants() -> dict:
    tree = ast.parse(CONTRACT.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    values[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return values


def test_policy_scalars_match():
    contract = _contract_constants()

    for name in SHARED_SCALARS:
        assert name in contract, f"{name} is missing from the contract"
        assert getattr(check_token, name) == contract[name], (
            f"{name} drifted: contract={contract[name]} tool={getattr(check_token, name)}"
        )


def test_chain_network_table_matches():
    """A chain the contract supports but the tool does not (or vice versa) would
    make pre-flight silently unusable for that chain."""
    assert check_token.CHAIN_NETWORK == _contract_constants()["CHAIN_NETWORK"]


def test_provider_templates_match():
    contract = dict(_contract_constants()["PROVIDER_TEMPLATES"])
    tool = dict(check_token.PROVIDER_TEMPLATES)

    assert contract == tool


def test_tool_reduces_two_readings_the_way_the_contract_does():
    """Two readings use the midpoint, not the lower one."""
    assert check_token.spread_bps(100, 105) == 500
    assert check_token.spread_bps(105, 100) == 500
    assert check_token.spread_bps(100, 100) == 0


def test_tool_parses_the_same_formats_as_the_contract():
    assert check_token.parse_decimal("0.00012345", 18) == 123450000000000
    assert check_token.parse_decimal("$1,234.56", 18) == 1234560000000000000000
    assert check_token.parse_decimal("1.2e-7", 18) == 120000000000
    assert check_token.parse_decimal("abc", 18) is None
    assert check_token.parse_decimal("1e400", 18) is None
