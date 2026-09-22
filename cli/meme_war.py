"""MemeWar command line client - the app's backend entry point.

Every command goes through GenLayer, so this is a real client of the contract
rather than a wrapper around the test suite::

    export GENLAYER_PRIVATE_KEY=0x...
    python -m cli.meme_war config
    python -m cli.meme_war open --symbol PEPE --address 0x... --chain base --side UP --stake 0.01
    python -m cli.meme_war list
    python -m cli.meme_war join --war 0x... --stake 0.01
    python -m cli.meme_war resolve --war 0x...
    python -m cli.meme_war claim
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from genlayer_py import create_account, create_client
from genlayer_py.chains import localnet, studionet, testnet_asimov
from genlayer_py.types import TransactionStatus

NETWORKS = {
    "localnet": localnet,
    "studionet": studionet,
    "asimov": testnet_asimov,
    "testnet_asimov": testnet_asimov,
}

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deploy" / "deployment.json"

ONE_GEN = 10 ** 18


def _client():
    private_key = os.environ.get("GENLAYER_PRIVATE_KEY")
    if not private_key:
        raise SystemExit("GENLAYER_PRIVATE_KEY is not set")
    network = os.environ.get("GENLAYER_NETWORK", "studionet")
    chain = NETWORKS.get(network)
    if chain is None:
        raise SystemExit(f"unknown network {network!r}")
    account = create_account(account_private_key=private_key)
    return create_client(chain=chain, account=account), network


def _address() -> str:
    override = os.environ.get("MEMEWAR_ADDRESS")
    if override:
        return override
    if DEPLOYMENT.is_file():
        return json.loads(DEPLOYMENT.read_text(encoding="utf-8"))["address"]
    raise SystemExit("no contract address: deploy first or set MEMEWAR_ADDRESS")


def _stake_to_wei(text: str) -> int:
    """Parse a GEN amount such as ``0.01`` or ``1`` into wei.

    Always GEN. An integer is GEN too, so ``--stake 1`` is one GEN and never one
    wei: the flag is documented in GEN, and silently reinterpreting a bare
    integer as wei would send a thousandth of a percent of what the user typed.
    Use a decimal like ``0.000000000000000001`` when you really do mean wei.
    """
    cleaned = text.strip()
    if cleaned == "" or cleaned.count(".") > 1:
        raise ValueError(f"not a GEN amount: {text!r}")
    whole, _, fraction = cleaned.partition(".")
    if whole == "":
        whole = "0"
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        raise ValueError(f"not a GEN amount: {text!r}")
    # Truncate rather than round, so a user is never charged more than typed.
    padded = (fraction + "0" * 18)[:18]
    return int(whole) * ONE_GEN + int(padded)


def _finish(client, tx_hash, as_json: bool):
    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=TransactionStatus.FINALIZED,
    )
    if as_json:
        print(json.dumps({"tx": str(tx_hash), "receipt": receipt}, indent=2, default=str))
    else:
        print(f"tx {tx_hash}")
    return receipt


def cmd_config(args) -> int:
    client, network = _client()
    config = client.read_contract(address=_address(), function_name="get_config", args=[])
    print(json.dumps({"network": network, "config": config}, indent=2, default=str))
    return 0


def cmd_sources(args) -> int:
    client, _ = _client()
    sources = client.read_contract(
        address=_address(),
        function_name="get_sources",
        args=[args.chain, args.address],
    )
    print(json.dumps(sources, indent=2, default=str))
    return 0


def cmd_parse(args) -> int:
    """Ask the contract how it reads a raw provider value, without spending gas."""
    client, _ = _client()
    sample = client.read_contract(
        address=_address(), function_name="preview_sample", args=[args.value]
    )
    print(json.dumps(sample, indent=2, default=str))
    return 0


def cmd_list(args) -> int:
    client, network = _client()
    function = "list_by_owner" if args.owner else "list_open"
    call_args = [args.owner, args.limit] if args.owner else [args.limit]
    wars = client.read_contract(address=_address(), function_name=function, args=call_args)
    print(json.dumps({"network": network, "wars": wars}, indent=2, default=str))
    return 0


def cmd_show(args) -> int:
    client, _ = _client()
    war = client.read_contract(address=_address(), function_name="get_war", args=[args.war])
    print(json.dumps(war, indent=2, default=str))
    return 0


def cmd_open(args) -> int:
    client, _ = _client()
    tx = client.write_contract(
        address=_address(),
        function_name="create_war",
        args=[args.symbol, args.address, args.chain, args.timeframe, args.side],
        value=_stake_to_wei(args.stake),
    )
    _finish(client, tx, args.json)
    return 0


def cmd_join(args) -> int:
    client, _ = _client()
    tx = client.write_contract(
        address=_address(),
        function_name="join_war",
        args=[args.war],
        value=_stake_to_wei(args.stake),
    )
    _finish(client, tx, args.json)
    return 0


def cmd_resolve(args) -> int:
    client, _ = _client()
    tx = client.write_contract(
        address=_address(), function_name="resolve_war", args=[args.war]
    )
    _finish(client, tx, args.json)
    return 0


def cmd_cancel(args) -> int:
    client, _ = _client()
    tx = client.write_contract(
        address=_address(), function_name="cancel_open_war", args=[args.war]
    )
    _finish(client, tx, args.json)
    return 0


def cmd_claim(args) -> int:
    client, account = _client()
    claimable = client.read_contract(
        address=_address(), function_name="get_claim", args=[account.address]
    )
    print(f"claimable: {int(claimable) / ONE_GEN} GEN")
    if int(claimable) <= 0:
        return 0
    tx = client.write_contract(address=_address(), function_name="claim", args=[])
    _finish(client, tx, args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meme-war", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("config", help="show the live oracle and market policy")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("sources", help="show the canonical price sources for a token")
    p.add_argument("--chain", required=True)
    p.add_argument("--address", required=True)
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("parse", help="show how the oracle reads a raw provider value")
    p.add_argument("value")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("list", help="list open wars, or one owner's wars")
    p.add_argument("--owner")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one war")
    p.add_argument("--war", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("open", help="open a war and lock your stake")
    p.add_argument("--symbol", required=True)
    p.add_argument("--address", required=True, help="token contract address")
    p.add_argument("--chain", required=True)
    p.add_argument("--timeframe", default="24h", choices=["1h", "24h", "7d"])
    p.add_argument("--side", default="UP", choices=["UP", "DOWN"])
    p.add_argument("--stake", default="0.01", help="stake in GEN, e.g. 0.01")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("join", help="take the other side of an open war")
    p.add_argument("--war", required=True)
    p.add_argument("--stake", required=True, help="must equal the creator's stake")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("resolve", help="settle an expired war (anyone may call)")
    p.add_argument("--war", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("cancel", help="refund an unmatched war after the window")
    p.add_argument("--war", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("claim", help="withdraw everything credited to you")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_claim)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
