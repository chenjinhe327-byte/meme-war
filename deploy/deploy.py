"""Deploy MemeWar to a GenLayer network.

Usage::

    export GENLAYER_PRIVATE_KEY=0x...
    export GENLAYER_NETWORK=studionet        # studionet | asimov | localnet
    python deploy/deploy.py

The deployed address is printed and written to ``deploy/deployment.json`` so the
frontend and the CLI can pick it up without copy-pasting.
"""

from __future__ import annotations

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
CONTRACT = ROOT / "contracts" / "meme_war.py"
OUT = ROOT / "deploy" / "deployment.json"


def main() -> int:
    private_key = os.environ.get("GENLAYER_PRIVATE_KEY")
    if not private_key:
        print("GENLAYER_PRIVATE_KEY is not set", file=sys.stderr)
        return 2

    network = os.environ.get("GENLAYER_NETWORK", "studionet")
    chain = NETWORKS.get(network)
    if chain is None:
        print(f"unknown network {network!r}; expected one of {sorted(NETWORKS)}", file=sys.stderr)
        return 2

    code = CONTRACT.read_text(encoding="utf-8")
    account = create_account(account_private_key=private_key)
    client = create_client(chain=chain, account=account)
    print(f"deploying to {network} as {account.address}")

    tx_hash = client.deploy_contract(code=code, args=[])
    print(f"deploy tx: {tx_hash}")

    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=TransactionStatus.FINALIZED,
        full_transaction=True,
    )

    address = None
    if isinstance(receipt, dict):
        address = receipt.get("recipient") or receipt.get("to") or receipt.get("contract_address")
    if not address:
        print("deploy finished but no address was found in the receipt:", file=sys.stderr)
        print(json.dumps(receipt, default=str)[:2000], file=sys.stderr)
        return 1

    OUT.write_text(
        json.dumps(
            {
                "network": network,
                "contract": "MemeWar",
                "address": address,
                "deploy_tx": tx_hash,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"MemeWar deployed at {address}")
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
