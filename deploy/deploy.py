"""Deploy MemeWar to a GenLayer network.

Credentials come from ``deploy/.env`` (git-ignored) or the environment, so the
private key never has to be pasted onto a command line or into a chat::

    # writes .env and prints only the address
    python tools/new_wallet.py

    # fund that address at https://testnet-faucet.genlayer.foundation
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
from genlayer_py.chains import localnet, studionet, testnet_asimov, testnet_bradbury
from genlayer_py.types import TransactionStatus

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "meme_war.py"
OUT = ROOT / "deploy" / "deployment.json"
ENV_FILE = ROOT / ".env"


def load_env() -> None:
    """Read .env without overwriting anything already exported."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ENV_FILE, override=False)

NETWORKS = {
    "localnet": localnet,
    "studionet": studionet,
    # Bradbury is the production-like testnet; Asimov is for infrastructure and
    # stress testing. Use Bradbury for anything a reviewer will look at.
    "bradbury": testnet_bradbury,
    "testnet_bradbury": testnet_bradbury,
    "asimov": testnet_asimov,
    "testnet_asimov": testnet_asimov,
}

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "meme_war.py"
OUT = ROOT / "deploy" / "deployment.json"


def main() -> int:
    load_env()

    private_key = os.environ.get("GENLAYER_PRIVATE_KEY")
    if not private_key:
        print(
            "GENLAYER_PRIVATE_KEY is not set. Run `python tools/new_wallet.py` "
            "to create .env, then fund the address it prints.",
            file=sys.stderr,
        )
        return 2

    network = os.environ.get("GENLAYER_NETWORK", "bradbury")
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
