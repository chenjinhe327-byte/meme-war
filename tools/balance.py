"""Check the testnet balance of the wallets in your .env files.

    python tools/balance.py                       # .env and .env.opponent
    python tools/balance.py --env .env            # just one
    python tools/balance.py --address 0x...       # any address, no key needed

Run this after using the faucet and before deploying: a deploy with an empty
account fails deep inside the RPC rather than with a clear message, and it is
much easier to check here first.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The GenLayer RPC handles gen_* plus pass-through eth_*; the chain RPC is the
# underlying L2. Both answer eth_getBalance, so try both before giving up.
RPCS = (
    "https://rpc.testnet-chain.genlayer.com",
    "https://rpc-bradbury.genlayer.com",
    "https://rpc-asimov.genlayer.com",
)

USER_AGENT = "meme-war-balance/1.0"


def read_env_addresses(paths) -> list:
    found = []
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != "GENLAYER_PRIVATE_KEY":
                continue
            value = value.strip().strip('"').strip("'")
            if not value:
                continue
            try:
                from genlayer_py import create_account

                address = create_account(account_private_key=value).address
            except Exception as exc:
                print(f"  {path.name}: cannot derive address ({exc})", file=sys.stderr)
                continue
            found.append((path.name, address))
    return found


def fetch_balance(address: str, timeout: int = 20):
    last_error = None
    for rpc in RPCS:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [address, "latest"]}
        ).encode()
        request = urllib.request.Request(
            rpc, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = payload.get("result")
            if result:
                return int(result, 16), rpc
        except Exception as exc:  # try the next endpoint
            last_error = exc
    return None, last_error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="balance", description=__doc__)
    parser.add_argument("--env", action="append", help="env file to read (repeatable)")
    parser.add_argument("--address", action="append", help="raw address to check")
    args = parser.parse_args(argv)

    targets = []
    if args.address:
        targets = [("-", a) for a in args.address]
    else:
        env_files = args.env or [".env", ".env.opponent"]
        paths = [Path(p) if Path(p).is_absolute() else ROOT / p for p in env_files]
        targets = read_env_addresses(paths)
        if not targets:
            print("No wallets found. Run `python tools/new_wallet.py` first.", file=sys.stderr)
            return 2

    short = 0
    for label, address in targets:
        balance, source = fetch_balance(address)
        if balance is None:
            print(f"  {address}  <unreachable: {source}>")
            continue
        gen = balance / 10 ** 18
        print(f"  {address}  {gen:,.6f} GEN   ({label})")
        if gen == 0:
            short += 1

    print()
    if short:
        print("Some wallets are empty.")
        print("Fund them at https://testnet-faucet.genlayer.foundation")
        return 1
    print("All wallets funded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
