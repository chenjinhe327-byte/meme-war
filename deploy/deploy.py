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
RECEIPT_OUT = ROOT / "deploy" / "last_receipt.json"
ENV_FILE = ROOT / ".env"

_HEX_ADDRESS_KEYS = ("contract_address", "recipient", "to_address", "to", "address")


def find_contract_address(receipt, sender=None, ignore=None):
    """Pull the new contract's address out of a deploy receipt.

    The exact nesting is not worth guessing at: `recipient` is documented as the
    deployed address for a deploy, but the RPC sometimes omits the fields that
    make that unambiguous (`tx_data_decoded` came back null on the first
    successful deploy here). So walk the whole structure and take the first
    address-shaped value under an address-shaped key that is neither the sender
    nor the consensus contract.
    """
    skip = set()
    for value in (sender, ignore):
        if isinstance(value, str) and value:
            skip.add(value.lower())

    def looks_like_address(value):
        return (
            isinstance(value, str)
            and value.startswith("0x")
            and len(value) == 42
            and all(c in "0123456789abcdefABCDEF" for c in value[2:])
        )

    found = []

    def walk(node, key_hint=None):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and looks_like_address(value):
                    if str(key).lower() in _HEX_ADDRESS_KEYS and value.lower() not in skip:
                        found.append(value)
                else:
                    walk(value, str(key))
        elif isinstance(node, list):
            for item in node:
                walk(item, key_hint)

    walk(receipt)
    return found[0] if found else None


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

    sys.path.insert(0, str(ROOT))
    from tools.minify import shrink
    from tools.net import install_all

    install_all()

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
    if os.environ.get("MEMEWAR_NO_MINIFY") != "1":
        code, before, after, gas = shrink(code)
        print(
            f"source {before} B -> {after} B (comments and docstrings stripped); "
            f"estimated deploy gas ~{gas:,}"
        )
    account = create_account(account_private_key=private_key)
    client = create_client(chain=chain, account=account)
    print(f"deploying to {network} as {account.address}")

    tx_hash = client.deploy_contract(code=code, args=[])
    print(f"deploy tx: {tx_hash}")

    receipt = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=TransactionStatus.ACCEPTED,
        full_transaction=True,
    )

    # Always keep the raw receipt: when address extraction goes wrong, the
    # receipt is the only record of what actually happened on chain.
    RECEIPT_OUT.write_text(
        json.dumps(receipt, indent=2, default=str) + "\n", encoding="utf-8"
    )

    address = find_contract_address(
        receipt, sender=account.address, ignore=(chain.consensus_main_contract or {}).get("address")
    )
    if not address:
        print(
            "deploy finished but no contract address was found in the receipt.",
            file=sys.stderr,
        )
        print(f"raw receipt written to {RECEIPT_OUT}", file=sys.stderr)
        top = sorted(receipt.keys()) if isinstance(receipt, dict) else type(receipt).__name__
        print(f"top-level receipt keys: {top}", file=sys.stderr)
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

    # The frontend is served from its own directory, so it cannot reach
    # ../deploy/. Drop a copy next to index.html and the page configures itself.
    frontend_record = ROOT / "frontend" / "deployment.json"
    frontend_record.write_text(
        json.dumps(
            {
                "network": network,
                "address": address,
                "deploy_tx": tx_hash,
                "explorer": f"https://explorer-{network}.genlayer.com/address/{address}",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"MemeWar deployed at {address}")
    print(f"written to {OUT} and {frontend_record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
