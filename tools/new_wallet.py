"""Create throwaway testnet wallets and write them to .env files.

Run it once per wallet::

    python tools/new_wallet.py                      # creator  -> .env
    python tools/new_wallet.py --out .env.opponent  # opponent -> .env.opponent

It prints only the **address**. The private key goes straight into the file,
which is git-ignored, so the key never has to be pasted onto a command line, into
a terminal scrollback, or into a chat window.

Fund every printed address at https://testnet-faucet.genlayer.foundation and
then run `python deploy/deploy.py`.

A war needs two wallets because the contract refuses to let the creator take
both sides of their own war.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from genlayer_py import create_account, generate_private_key

ROOT = Path(__file__).resolve().parents[1]


def write_wallet(path: Path, *, force: bool, network: str, label: str) -> int:
    if path.exists() and not force:
        print(f"{path} already exists.", file=sys.stderr)
        print(
            "Use --force to replace it, or --out <file> to create another wallet.\n"
            "Replacing a wallet abandons any testnet GEN already sent to it.",
            file=sys.stderr,
        )
        return 1

    key = generate_private_key()
    hexed = key.hex() if hasattr(key, "hex") else str(key)
    hexed = hexed[2:] if hexed.startswith("0x") else hexed
    account = create_account(account_private_key=key)

    path.write_text(
        f"# Throwaway testnet wallet ({label}). Git-ignored. Never reuse for anything real.\n"
        f"GENLAYER_PRIVATE_KEY=0x{hexed}\n"
        f"GENLAYER_NETWORK={network}\n",
        encoding="utf-8",
    )

    print(f"wrote {path} (private key not shown)")
    print()
    print(f"  {label.upper()} ADDRESS: {account.address}")
    print()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="new-wallet", description=__doc__)
    parser.add_argument(
        "--out",
        default=".env",
        help="file to write (default: .env). Use .env.opponent for the second wallet",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing file instead of refusing",
    )
    parser.add_argument("--network", default="bradbury")
    parser.add_argument("--label", default="wallet")
    args = parser.parse_args(argv)

    path = Path(args.out)
    if not path.is_absolute():
        path = ROOT / path
    if args.label == "wallet":
        args.label = "opponent" if "opponent" in path.name else "creator"

    code = write_wallet(path, force=args.force, network=args.network, label=args.label)
    if code == 0:
        print("Fund it at https://testnet-faucet.genlayer.foundation")
        print("then run:  python deploy/deploy.py")
    return code


if __name__ == "__main__":
    sys.exit(main())
