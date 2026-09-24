# Submission — GenLayer Portal, **Projects**

> Copy the fields below into the contribution form. The Showcase links and the
> evidence table are already filled in from the live Bradbury deployment.

---

## Title

```
Meme War — trustless PvP settlement for assets that have no oracle
```

## Showcase (links)

| Type | Value |
| --- | --- |
| Other | `https://github.com/chenjinhe327-byte/meme-war` |
| Other | `https://chenjinhe327-byte.github.io/meme-war/` |
| Other | `https://explorer-bradbury.genlayer.com/address/0xa3b14b98c6D6D74A344463a3604Db804700a638A` |
| Other | `https://explorer-bradbury.genlayer.com/tx/0x0b8e252964fa6e13db9a893950571480c244119cf45946cc01fc95c42ffe6021` |

**Website field:** `https://chenjinhe327-byte.github.io/meme-war/`

Published automatically from `frontend/` by `.github/workflows/pages.yml` on
every push, so the link cannot go stale. The first hand-made deploy silently
shipped only `index.html`, leaving `app.js` and `styles.css` as 404s — no
JavaScript, so every button was dead. That is what the reviewer hit, and it is
why the deploy is automated rather than drag-and-drop.

## Deployment history

The contract was revised after review. Both revisions are on chain, and the
evidence below is labelled by revision rather than blurred together.

| Revision | Contract | Deploy tx |
| --- | --- | --- |
| v1 — as submitted | `0x8F47f49A140a5e898E4eA0C4F473AFcBCBD6Af1f` | `0x69b3197e600b7ccd67aae7d4683d16452f8bc572c5ff8a3ea132cf2db4b476c9` |
| v2 — after review | `0xa3b14b98c6D6D74A344463a3604Db804700a638A` | `0xfc541adb4999aab83cf605fdae250ab634710e3692e14217fb07b5c15f6e5768` |

## v1: the full round trip, on chain

Every row is a real transaction on GenLayer Bradbury (chain 4221); nothing was
simulated. These ran against **v1**.

| Step | Transaction |
| --- | --- |
| Open a war — creator stakes 0.01 GEN on BRETT going UP, 1h | war `0xb1065e9d0adb2bd0295940164ecc533bbdcaf6609b9273f08ba44f1e3a93b0bb` |
| Match it — opponent stakes 0.01 GEN, **oracle reads the entry price** | `0x4555b693a4819052964bf2dcf4c8ee934c189dd6a104bb2808308ed9c95b730e` |
| Settle after expiry — **oracle reads the exit price** | `0x0b8e252964fa6e13db9a893950571480c244119cf45946cc01fc95c42ffe6021` |
| Winner withdraws the pot | `0xd1169d4d1020faafaeefbc2ca4d6dda47e0b02abaf21fb5f8b088d5d30a10bda` |

What the contract observed and stored, on chain:

```
entry_price      0.00580086753      entry_liquidity  $1,219,802
exit_price       0.005774663406     move             -0.45%
winner           DOWN               pot              0.02 GEN
final claimable  0 (the winner drained it)   total_wars 1, total_settled 1
```

`entry_price` and `exit_price` were produced **by the contract**, reading
DexScreener and GeckoTerminal inside a GenLayer validator set and agreeing on the
number within 200 bps. That is the claim no local test can make, and it is the
whole reason the project exists.

## v2: what changed after review

Three defects, all fixed in v2, plus 13 regression tests (94 → 107, all passing
offline).

**1. Validators now agree on the *winner*, not just on a nearby number.**
A tolerance on the price is not sufficient. Two readings can sit inside the
200 bps band and still fall on opposite sides of the entry price — the leader
reads `1.0010` against an entry of `1.0000` (UP) while the validator reads
`0.9990` (DOWN), 20 bps apart. A price-only check accepts that pair and the war
settles on whichever node happened to lead. The validator now derives its own
winner from its own reading and must match the leader's; when a price genuinely
straddles the entry, consensus fails and the war retries instead of settling by
coin flip. `test_winner_consensus.py` drives exactly that pair and asserts
consensus fails.

**2. The wager window starts when the war is matched, not when it was created.**
Previously `resolve_at` was fixed at creation, so a war that waited five hours
inside the six-hour matching window could arrive already expired and its "1h"
would mean nothing. The entry price is fixed at the same moment, so both sides
trade the same observation.

**3. Permissionless retries are rate limited.**
Anyone may push an expired war to settlement — that is the point. But a retry
counter plus permissionless access is a griefing vector: three calls in a row
during a thirty-second provider outage would hit `MAX_ATTEMPTS` and force a void,
taking the pot from a winner who did nothing wrong. Attempts are now spaced by
`ATTEMPT_COOLDOWN_SECONDS` (10 min), so a refund needs the data to stay unusable
for at least 20 minutes. `min_seconds_to_void` is published in `get_config()`.

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
94 tests that run offline in seconds — 65 direct-mode contract tests, including
the two that pin the equivalence relation down (a validator reading 1% away must
agree and one reading 10% away must disagree), plus 29 more covering the CLI and
the pre-flight tool; a command line client and deployment script on genlayer-py;
and a frontend that reads the live oracle policy from the contract and drives
open / join / resolve / claim through the injected wallet. The numeric contract
between "what a DEX page says" and "what the contract stores" is exposed publicly
as preview_sample() so anyone relying on a war can inspect it rather than read
the source.

It has been run end to end on GenLayer Bradbury: deployed, opened, matched,
settled and paid out, with the entry and exit prices produced by the contract
reading two providers on chain. The full transaction chain is in the README.
```

---

## Pre-submit checklist

All of the following is done; it is kept so the claims in this file can be
re-checked rather than taken on trust.

- [x] Repository is public: <https://github.com/chenjinhe327-byte/meme-war>
- [x] `pytest` → **94 passed**
- [x] Testnet GEN from <https://testnet-faucet.genlayer.foundation>
- [x] Deployed to Bradbury → `0xa3b14b98c6D6D74A344463a3604Db804700a638A`
- [x] Pre-flighted the token, opened a war and matched it on a real validator set
- [x] Settled it and withdrew the pot (deploy / match / resolve / claim txs above)
- [ ] Serve `frontend/` and confirm reads load — **not done; no browser available
      where this was built.** Run it before submitting.

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
