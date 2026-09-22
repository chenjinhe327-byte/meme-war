"""The CLI's own logic, tested without a network.

`cli/meme_war.py` is a real client of the contract, so most of it can only be
exercised against a deployed address. These tests cover the parts that are pure
logic and would otherwise ship unverified: stake parsing, which decides how much
money is actually sent, and the argument surface itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import meme_war

ONE_GEN = 10 ** 18


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    """Never read the developer's real .env.

    Once `tools/new_wallet.py` has been run there *is* a real .env on the
    machine, and a test that quietly depends on it either passes for the wrong
    reason or fails for one. Each test opts into the variables it wants.
    """
    monkeypatch.setattr(meme_war, "ENV_FILE", Path("does-not-exist.env"))
    for name in ("GENLAYER_PRIVATE_KEY", "GENLAYER_NETWORK", "MEMEWAR_ADDRESS"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "text,expected",
    [
        # An integer is GEN, not wei. `--stake 1` sends one GEN.
        ("1", ONE_GEN),
        ("12", 12 * ONE_GEN),
        ("0.01", 10 ** 16),
        ("0.000000000000000001", 1),
        ("12.5", 12 * ONE_GEN + 5 * 10 ** 17),
        (".5", 5 * 10 ** 17),
        ("0", 0),
        ("0.0", 0),
        (" 0.25 ", 25 * 10 ** 16),
        # More precision than wei can express: truncate, never round up.
        ("0.0000000000000000009", 0),
    ],
)
def test_stake_is_always_parsed_as_gen(text, expected):
    assert meme_war._stake_to_wei(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "1.2.3", "-1", "1e3", "1,000"])
def test_stake_parsing_rejects_nonsense(text):
    with pytest.raises(ValueError):
        meme_war._stake_to_wei(text)


class _FakeDeployment:
    """Stand-in for deploy/deployment.json.

    A stub rather than a real file: `_address()` only needs `is_file` and
    `read_text`, and pytest's `tmp_path` fixture is unusable under the sandbox
    this project was developed in.
    """

    def __init__(self, address: str | None):
        self._address = address

    def is_file(self) -> bool:
        return self._address is not None

    def read_text(self, encoding: str = "utf-8") -> str:
        assert self._address is not None
        return json.dumps({"address": self._address})


def test_address_prefers_the_environment_override(monkeypatch):
    monkeypatch.setenv("MEMEWAR_ADDRESS", "0x" + "11" * 20)
    monkeypatch.setattr(meme_war, "DEPLOYMENT", _FakeDeployment("0x" + "22" * 20))

    assert meme_war._address() == "0x" + "11" * 20


def test_address_falls_back_to_the_deployment_file(monkeypatch):
    monkeypatch.delenv("MEMEWAR_ADDRESS", raising=False)
    monkeypatch.setattr(meme_war, "DEPLOYMENT", _FakeDeployment("0x" + "22" * 20))

    assert meme_war._address() == "0x" + "22" * 20


def test_address_is_required_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("MEMEWAR_ADDRESS", raising=False)
    monkeypatch.setattr(meme_war, "DEPLOYMENT", _FakeDeployment(None))

    with pytest.raises(SystemExit):
        meme_war._address()


def test_every_subcommand_is_wired_to_a_handler():
    parser = meme_war.build_parser()
    command_action = next(a for a in parser._actions if a.dest == "command")
    names = set(command_action.choices)

    assert names == {
        "config",
        "sources",
        "parse",
        "list",
        "show",
        "open",
        "join",
        "resolve",
        "cancel",
        "claim",
    }
    for name in names:
        parsed = parser.parse_args([name] + _required_args(name))
        assert callable(parsed.func), f"{name} has no handler"


def _required_args(name: str) -> list:
    """Minimal arguments so each subcommand parses."""
    return {
        "sources": ["--chain", "base", "--address", TOKEN],
        "parse": ["$1.23"],
        "show": ["--war", WAR],
        "open": ["--symbol", "PEPE", "--address", TOKEN, "--chain", "base"],
        "join": ["--war", WAR, "--stake", "0.01"],
        "resolve": ["--war", WAR],
        "cancel": ["--war", WAR],
    }.get(name, [])


TOKEN = "0x" + "ab" * 20
WAR = "0x" + "00" * 32


def test_open_defaults_are_values_the_contract_accepts():
    parser = meme_war.build_parser()
    parsed = parser.parse_args(
        ["open", "--symbol", "PEPE", "--address", TOKEN, "--chain", "base"]
    )

    assert parsed.side in ("UP", "DOWN")
    assert parsed.timeframe in ("1h", "24h", "7d")
    # The default stake must round-trip through the contract's own rule that a
    # stake is required (i.e. it must not be zero).
    assert meme_war._stake_to_wei(parsed.stake) > 0


def test_unknown_network_is_rejected(monkeypatch):
    monkeypatch.setenv("GENLAYER_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("GENLAYER_NETWORK", "not-a-network")

    with pytest.raises(SystemExit):
        meme_war._client()


def test_bradbury_is_available():
    """Bradbury is the production-like testnet a reviewer will look at; shipping
    with only Asimov would have pointed the demo at the wrong network."""
    assert "bradbury" in meme_war.NETWORKS
    assert meme_war.NETWORKS["bradbury"].id == 4221


def test_missing_private_key_is_reported(monkeypatch):
    monkeypatch.delenv("GENLAYER_PRIVATE_KEY", raising=False)

    with pytest.raises(SystemExit):
        meme_war._client()
