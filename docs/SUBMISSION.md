# Submission — GenLayer Portal, **Projects**

> Copy the fields below into the contribution form. Fill the two `<…>`
> placeholders after you deploy, and attach the links listed under *Showcase*.

---

## Title

```
Meme War — trustless PvP settlement for assets that have no oracle
```

## Showcase (links)

| Type | Value |
| --- | --- |
| Other | `https://github.com/chenjinhe327-byte/meme-war` *(push this repo first)* |
| Other | `<GenLayer explorer link to the deployed MemeWar contract>` |
| Other | `<frontend URL, or the explorer link to a settled war transaction>` |

## Notes / description

```
Meme War is a two-sided betting app for long-tail tokens. Two players lock the
same stake on opposite sides of a token's direction over a fixed window (1h /
24h / 7d). The contract observes an entry price when the war is matched and an
exit price when it expires; the winner takes the pot. Nobody custodies the
stakes, and neither player chooses the price source.

GenLayer is not decoration here — the product cannot exist without it. The
tokens Meme War settles are exactly the ones no oracle covers: no Chainlink
feed, no API contract, no listing. Their price exists only as a number on a DEX
aggregator page, and the pool may be thin enough to push anywhere.

The real problem is not fetching a price, it is agreeing on one. The obvious
GenLayer pattern — render a page, ask an LLM for the price, compare the answers
with gl.eq_principle.strict_eq — can never settle this: two validators execute
at different moments against a market that is still trading, so one reads
0.00012345 and the other 0.00012346, the exact comparison fails, and the stake
can never be released. Byte equality is the wrong equivalence relation for a
numeric fact.

Meme War replaces it with a numeric tolerance consensus. Validators re-derive
the price independently and agree when the two numbers are within 200 bps.
Cross-source agreement is enforced separately at 500 bps across two independent
providers (DexScreener and GeckoTerminal), and a $25,000 liquidity floor
rejects wash-tradeable dust pools. Every value that can move money is parsed
into scaled integers digit-by-digit without float, because a binary float is not
the same number on every platform and this number decides who gets paid. When
the data cannot be trusted the war voids and refunds both sides: refusing to pay
is always safer than paying the wrong player. Transient failures (a provider
that did not answer) retry up to three times, while structural failures
(providers that contradict each other, or a pool below the floor) void
immediately.

What is built: the Intelligent Contract (contracts/meme_war.py, single file);
65 direct-mode tests that run offline in ~4s, including the two that pin the
equivalence relation down — a validator reading 1% away must agree and one
reading 10% away must disagree; a command line client and deployment script on
genlayer-py; and a frontend that reads the live oracle policy from the contract
and drives open / join / resolve / claim through the injected wallet. The
numeric contract between "what a DEX page says" and "what the contract stores"
is exposed publicly as preview_sample() so anyone relying on a war can inspect
it rather than read the source.
```

---

## Pre-submit checklist

- [ ] Push this repository publicly.
- [ ] `pip install -r requirements.txt && pytest` → **94 passed**.
- [ ] Get testnet GEN from <https://testnet-faucet.genlayer.foundation>.
- [ ] `GENLAYER_NETWORK=bradbury python deploy/deploy.py` → note the address.
      Bradbury is the production-like testnet (`explorer-bradbury.genlayer.com`);
      Asimov is for infrastructure and stress testing.
- [ ] Pre-flight the token, then open one war and match it, so the settlement
      path is exercised on a real validator set (the two-provider read is the
      part that only a real network proves).
- [ ] Record the deploy tx and at least one `resolve_war` tx for the Showcase.
- [ ] Serve `frontend/` and pass `?address=0x<deployed>`; confirm reads load.
- [ ] Paste the deployed address into this file and the README.

### A token that is known to pass

Verified with `tools/check_token.py` against the live providers:

```
BRETT on Base   0x532f27101965dd16442E59d40670FaF5eBB142E4
  dexscreener     price=0.005807        liquidity=$1,319,340
  geckoterminal   price=0.005804027131  liquidity=$1,240,284
  spread 0.05%    thinnest pool $1,240,284   -> PASS
```

Use a 1h window so the round trip can be completed in one sitting.

## Honest status — read before submitting

- The **frontend has not been run against a deployed contract**. It has no build
  step and its syntax is checked, but the first testnet run is its smoke test.
  Do that before submitting, and fix or drop the link if it misbehaves.
- The **integration tests are skipped** unless `MEMEWAR_LIVE=1`; they spend
  testnet funds.
- **Provider rate limits can void a war.** DexScreener returns HTTP 429
  aggressively per IP — three quick calls from one machine is enough to trip it —
  and a 429 is indistinguishable from "provider down", so the war retries and can
  void. This was observed directly while validating the pre-flight tool. It is
  the same root cause as the single-provider dependency below.
- Two providers means **one provider outage blocks matching**. That is a known
  limitation with a documented fix (a third provider, which also upgrades the
  reduction from midpoint to an outlier-rejecting median). Do not describe it as
  solved.
- The contract pins the `py-genlayer` runner hash rather than `latest`, so the
  deployed artifact is the one the 94 tests were executed against.

## Why this is a Project and not an Intelligent Contract submission

The portal asks for Projects to be *complete apps*, with app logic that actually
interacts with GenLayer. This repository ships an end-to-end product: contract,
client, deployment, frontend. The oracle primitive is the technical centre of
it, but on its own it would be an Intelligent Contract submission — submit it
here, as the app it is.
