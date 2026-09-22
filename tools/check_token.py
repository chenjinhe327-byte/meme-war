"""Pre-flight check: would the contract accept a war on this token?

The contract voids a war - and refunds both sides - when the two providers
disagree by more than 500 bps, or when the thinnest pool is below $25,000. That
is the right behaviour on-chain, but it is an unpleasant way to discover that a
token was never going to settle.

This runs the *same* two providers the contract reads, from your machine, and
tells you beforehand whether the token passes:

    python tools/check_token.py --chain base --address 0x<token>
    python tools/check_token.py --chain base --address 0x<token> --json

It is a diagnostic, not a source of truth: the contract re-reads both providers
at match time. Run it immediately before opening a war.

NOTE: the policy constants below must match `contracts/meme_war.py`. That is
enforced by `tests/unit/test_policy_parity.py`, so the two cannot drift apart
silently.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# --- policy: keep in sync with contracts/meme_war.py -------------------------

MIN_LIQUIDITY_USD = 25_000
SOURCE_AGREEMENT_BPS = 500
LIQUIDITY_DECIMALS = 6

PROVIDER_TEMPLATES = (
    ("dexscreener", "https://api.dexscreener.com/latest/dex/tokens/{address}"),
    (
        "geckoterminal",
        "https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}",
    ),
)

CHAIN_NETWORK = {
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "polygon": "polygon_pos",
    "solana": "solana",
    "avax": "avax",
}

USER_AGENT = "meme-war-preflight/1.0"


# --- fetching ----------------------------------------------------------------


def fetch(url: str, timeout: int = 30, attempts: int = 4):
    """GET a provider, backing off on 429.

    DexScreener rate-limits aggressively per IP - three quick calls from one
    machine is enough to trip it. A 429 is exactly the "provider did not answer"
    case the contract retries and then voids on, so it is worth waiting out here
    rather than reporting a false negative.
    """
    delay = 2.0
    last_error = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
        except Exception as exc:
            last_error = exc
            raise
    raise last_error  # pragma: no cover - the loop always returns or raises


def parse_decimal(text, decimals: int):
    """Mirror of the contract's parser, for plain decimal strings."""
    if text is None:
        return None
    raw = str(text).strip().replace("$", "").replace(",", "").replace("_", "")
    if raw == "":
        return None
    sign = 1
    if raw[0] in "+-":
        if raw[0] == "-":
            sign = -1
        raw = raw[1:]
    exponent = 0
    for marker in ("e", "E"):
        if marker in raw:
            mantissa, _, exponent_text = raw.partition(marker)
            try:
                exponent = int(exponent_text)
            except ValueError:
                return None
            raw = mantissa
            break
    if abs(exponent) > 40:
        return None
    whole, _, fraction = raw.partition(".")
    if whole == "":
        whole = "0"
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        return None
    digits = whole + fraction
    shift = len(whole) + exponent - len(digits) + decimals
    value = int(digits)
    if shift >= 0:
        return sign * value * (10 ** shift)
    return sign * (value // (10 ** (-shift)))


def extract_dexscreener(payload: dict):
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return None
    best = None
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        price = parse_decimal(pair.get("priceUsd"), 18)
        liquidity = parse_decimal((pair.get("liquidity") or {}).get("usd"), LIQUIDITY_DECIMALS)
        if price is None or price <= 0 or liquidity is None:
            continue
        if best is None or liquidity > best["liquidity"]:
            best = {"price": price, "liquidity": liquidity, "pool": pair.get("dexId", "?")}
    return best


def extract_geckoterminal(payload: dict):
    attributes = ((payload.get("data") or {}).get("attributes")) or {}
    price = parse_decimal(attributes.get("price_usd"), 18)
    liquidity = parse_decimal(attributes.get("total_reserve_in_usd"), LIQUIDITY_DECIMALS)
    if price is None or price <= 0 or liquidity is None:
        return None
    return {"price": price, "liquidity": liquidity, "pool": "geckoterminal"}


EXTRACTORS = {
    "dexscreener": extract_dexscreener,
    "geckoterminal": extract_geckoterminal,
}


def spread_bps(a: int, b: int) -> int:
    low, high = (a, b) if a < b else (b, a)
    return ((high - low) * 10000) // low


# --- report ------------------------------------------------------------------


def check(chain: str, address: str) -> dict:
    network = CHAIN_NETWORK.get(chain)
    if network is None:
        return {"ok": False, "reason": f"unsupported chain {chain!r}"}

    samples = []
    for provider, template in PROVIDER_TEMPLATES:
        url = template.replace("{address}", address).replace("{network}", network)
        try:
            payload = fetch(url)
        except Exception as exc:
            samples.append({"provider": provider, "error": f"{type(exc).__name__}: {exc}"})
            continue
        sample = EXTRACTORS[provider](payload)
        if sample is None:
            samples.append({"provider": provider, "error": "no usable price/liquidity in response"})
            continue
        sample["provider"] = provider
        samples.append(sample)

    failed = [s for s in samples if "error" in s]
    if failed:
        return {
            "ok": False,
            "reason": "DATA_UNAVAILABLE - not every provider answered",
            "samples": samples,
            "hint": "The contract treats this as transient: it retries, then voids.",
        }

    prices = [s["price"] for s in samples]
    liquidities = [s["liquidity"] for s in samples]
    spread = spread_bps(min(prices), max(prices))
    thin = min(liquidities)

    result = {
        "chain": chain,
        "network": network,
        "address": address,
        "samples": samples,
        "spread_bps": spread,
        "min_liquidity_usd": thin / (10 ** LIQUIDITY_DECIMALS),
        "ok": True,
        "warnings": [],
    }

    if spread > SOURCE_AGREEMENT_BPS:
        result["ok"] = False
        result["reason"] = (
            f"DATA_DISAGREE - providers are {spread / 100:.2f}% apart "
            f"(limit {SOURCE_AGREEMENT_BPS / 100:.2f}%)"
        )
        result["warnings"].append("The contract voids immediately on this.")
    if thin < MIN_LIQUIDITY_USD * (10 ** LIQUIDITY_DECIMALS):
        result["ok"] = False
        result["reason"] = (
            f"DATA_THIN - thinnest pool is ${thin / (10 ** LIQUIDITY_DECIMALS):,.0f} "
            f"(floor ${MIN_LIQUIDITY_USD:,})"
        )
        result["warnings"].append("Wash-tradeable; the contract refuses to settle.")
    if result["ok"]:
        result["reason"] = "passes both integrity rules"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="check-token", description=__doc__)
    parser.add_argument("--chain", required=True, choices=sorted(CHAIN_NETWORK))
    parser.add_argument("--address", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = check(args.chain, args.address)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    for sample in result.get("samples", []):
        if "error" in sample:
            print(f"  {sample['provider']:<15} ERROR  {sample['error']}")
        else:
            print(
                f"  {sample['provider']:<15} price={sample['price'] / 10**18:.10g}  "
                f"liquidity=${sample['liquidity'] / 10**LIQUIDITY_DECIMALS:,.0f}"
            )
    if "spread_bps" in result:
        print(f"  spread          {result['spread_bps'] / 100:.2f}%")
        print(f"  thinnest pool   ${result['min_liquidity_usd']:,.0f}")

    verdict = "PASS" if result.get("ok") else "FAIL"
    print(f"\n{verdict}: {result.get('reason')}")
    for warning in result.get("warnings", []):
        print(f"  ! {warning}")
    if result.get("hint"):
        print(f"  ! {result['hint']}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
