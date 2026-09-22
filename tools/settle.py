"""Settle an expired war.

Wars are not settled automatically - a window has to actually pass first, so
someone (or something) has to push the result. Resolution is permissionless:
anyone may call it, because the outcome comes from validators rather than from
the caller. That makes settling a public good, and this script is the keeper.

    python tools/settle.py --war 0x<id> --wait          # wait for expiry, then settle
    python tools/settle.py --war 0x<id>                 # settle now (fails if not expired)
    python tools/settle.py --war 0x<id> --wait --claim  # also withdraw the payout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.net import install_all  # noqa: E402

ONE_GEN = 10 ** 18


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def _client():
    _load_env()
    install_all()

    from genlayer_py import create_account, create_client
    from genlayer_py.chains import localnet, studionet, testnet_asimov, testnet_bradbury
    from genlayer_py.types import TransactionStatus

    networks = {
        "localnet": localnet,
        "studionet": studionet,
        "bradbury": testnet_bradbury,
        "testnet_bradbury": testnet_bradbury,
        "asimov": testnet_asimov,
        "testnet_asimov": testnet_asimov,
    }
    network = os.environ.get("GENLAYER_NETWORK", "bradbury")
    chain = networks.get(network)
    if chain is None:
        raise SystemExit(f"unknown network {network!r}")

    private_key = os.environ.get("GENLAYER_PRIVATE_KEY")
    if not private_key:
        raise SystemExit("GENLAYER_PRIVATE_KEY is not set")

    address = os.environ.get("MEMEWAR_ADDRESS")
    if not address:
        deployment = ROOT / "deploy" / "deployment.json"
        if not deployment.is_file():
            raise SystemExit("no contract address: deploy first or set MEMEWAR_ADDRESS")
        address = json.loads(deployment.read_text(encoding="utf-8"))["address"]

    account = create_account(account_private_key=private_key)
    return create_client(chain=chain, account=account), address, account, TransactionStatus


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="settle", description=__doc__)
    parser.add_argument("--war", required=True)
    parser.add_argument("--wait", action="store_true", help="sleep until the war expires")
    parser.add_argument("--claim", action="store_true", help="claim after settling")
    parser.add_argument("--margin", type=int, default=20, help="seconds past expiry to settle")
    args = parser.parse_args(argv)

    client, address, account, status = _client()

    war = client.read_contract(address=address, function_name="get_war", args=[args.war])
    if not war:
        print(f"no such war: {args.war}", file=sys.stderr)
        return 2

    print(f"war {args.war}")
    print(f"  {war['token_symbol']} / {war['chain']} / {war['timeframe']} / {war['status']}")

    if war["status"] == "OPEN":
        print("  not matched yet - nothing to settle", file=sys.stderr)
        return 2
    if war["status"] not in ("MATCHED",):
        print(f"  already closed as {war['status']} (winner {war['winner'] or '-'})")
        return 0

    now = client.w3.eth.get_block("latest")["timestamp"]
    target = int(war["resolve_at"]) + args.margin

    if now < target:
        remaining = target - now
        if not args.wait:
            print(
                f"  expires in {remaining}s; re-run with --wait to sit it out",
                file=sys.stderr,
            )
            return 2
        print(f"  waiting {remaining}s for expiry...", flush=True)
        time.sleep(remaining)

    tx = client.write_contract(address=address, function_name="resolve_war", args=[args.war])
    print(f"  resolve tx {tx}")

    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx, status=status.ACCEPTED
    )
    settled = client.read_contract(address=address, function_name="get_war", args=[args.war])

    print(f"  status {settled['status']}  winner {settled['winner'] or '-'}")
    if settled["exit_price"]:
        ratio = settled["exit_price"] / (settled["entry_price"] or 1)
        print(
            f"  entry {settled['entry_price'] / ONE_GEN:.10g} -> "
            f"exit {settled['exit_price'] / ONE_GEN:.10g}  ({ratio - 1:+.2%})"
        )
    if settled["status"] == "VOID":
        print("  voided: stakes refunded to both sides")

    if args.claim:
        claimable = int(
            client.read_contract(
                address=address, function_name="get_claim", args=[account.address]
            )
        )
        if claimable <= 0:
            print("  nothing to claim for this account")
        else:
            print(f"  claiming {claimable / ONE_GEN} GEN")
            claim_tx = client.write_contract(address=address, function_name="claim", args=[])
            client.wait_for_transaction_receipt(
                transaction_hash=claim_tx, status=status.ACCEPTED
            )
            print(f"  claim tx {claim_tx}")

    return 0 if settled["status"] != "VOID" else 0


if __name__ == "__main__":
    sys.exit(main())
