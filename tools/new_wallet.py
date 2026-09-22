"""Create a throwaway testnet wallet and write it to .env.

Run it once, before deploying::

    python tools/new_wallet.py

It prints only the **address**. The private key goes straight into `.env`, which
is git-ignored, so the key never has to be pasted onto a command line, into a
terminal scrollback, or into a chat window.

Fund the printed address at https://testnet-faucet.genlayer.foundation and then
run `python deploy/deploy.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from genlayer_py import create_account, generate_private_key

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def main() -> int:
    if ENV_FILE.exists():
        print(f"{ENV_FILE} already exists - refusing to overwrite.", file=sys.stderr)
        print(
            "Delete it first if you really want a fresh wallet "
            "(any testnet GEN on the old address stays there).",
            file=sys.stderr,
        )
        return 1

    key = generate_private_key()
    hexed = key.hex() if hasattr(key, "hex") else str(key)
    hexed = hexed[2:] if hexed.startswith("0x") else hexed
    account = create_account(account_private_key=key)

    ENV_FILE.write_text(
        "# Throwaway testnet wallet. Git-ignored. Never reuse for anything real.\n"
        f"GENLAYER_PRIVATE_KEY=0x{hexed}\n"
        "GENLAYER_NETWORK=bradbury\n",
        encoding="utf-8",
    )

    print(f"wrote {ENV_FILE} (private key not shown)")
    print()
    print(f"  ADDRESS: {account.address}")
    print()
    print("Fund that address at https://testnet-faucet.genlayer.foundation")
    print("then run:  python deploy/deploy.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
