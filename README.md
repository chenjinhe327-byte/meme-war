# Meme War

**Trustless PvP settlement for assets that have no oracle.**

Two players stake the same amount on opposite sides of a long-tail token's
direction over a fixed window. The contract reads an entry price when the war is
matched and an exit price when it expires; the winner takes the pot. Nobody
custodies the stakes, and no player chooses the price source.

Built on [GenLayer](https://genlayer.com) Intelligent Contracts.

---

## The problem

Prediction markets and on-chain betting only work for assets somebody paid to
list. Chainlink-style feeds exist for BTC, ETH and a few hundred tokens — not
for the token that is actually moving this week.

Long-tail tokens are different in kind, not just in degree:

- there is **no feed** and no API contract to subscribe to;
- the price only exists as a number rendered on a DEX aggregator page;
- the pool may be thin enough that an attacker can push it anywhere they like.

So the interesting question is not "how do I fetch a price". It is **"how do two
independent validators agree on a number they each read from a live page that
has moved since"**. That is what this contract is actually about.

## Why a plain `strict_eq` contract cannot do this

The obvious GenLayer pattern — render a page, ask an LLM for the price, compare
the two answers with `gl.eq_principle.strict_eq` — never reaches consensus here.
Two validators execute at different moments against a page that trades
continuously. One reads `0.00012345`, the other `0.00012346`, and the comparison
fails. The transaction cannot settle, so the money stays stuck. Byte equality is
the wrong equivalence relation for a numeric fact.

`MemeWar` replaces it with a **numeric tolerance consensus**:

```python
def validator_fn(result) -> bool:
    leader = json.loads(result.calldata)          # the leader's number
    mine = observe_price(sources)                 # re-derive it independently
    return relative_spread_bps(mine["price"], leader["price"]) <= 200   # 2%
```

Validators agree when the two numbers are within 2% of each other. That absorbs
price movement between executions (and ordinary provider latency) while still
catching a validator that read a genuinely different market.

Full design notes: [`docs/ORACLE.md`](docs/ORACLE.md).

## The three integrity rules

| Rule | Value | What it prevents |
| --- | --- | --- |
| **Validator tolerance** | 200 bps | Consensus failure on live data (the contract could never settle) |
| **Cross-source agreement** | 500 bps | Settling on one provider that is wrong, stale or manipulated |
| **Liquidity floor** | $25,000 | Wash-traded dust pools being pushed to any price |

Everything that touches money is integer arithmetic. Provider output is untrusted
text and is converted by [`parse_decimal`](contracts/meme_war.py) before it can
influence a payout — deliberately without `float`, because a binary float is not
the same number on every platform and this number decides who gets paid.

When the data cannot be trusted, the war **voids and refunds both sides**. A
refund is always better than paying the wrong player.

---

## Repository layout

```
contracts/meme_war.py     the Intelligent Contract (single file, no boilerplate)
tests/direct/             65 in-memory contract tests (parsing, market rules,
                          oracle, settlement)
tests/unit/               23 tests for the CLI's own logic
tests/integration/        Studio / testnet end-to-end tests
cli/meme_war.py           command line client (genlayer-py)
frontend/                 static dApp, no build step (genlayer-js)
deploy/deploy.py          deployment script
docs/ORACLE.md            consensus design notes
docs/SUBMISSION.md        the write-up submitted with the project
```

## Running it

### Tests

The direct runner executes contracts in-process with web and LLM mocks, so the
whole suite runs offline in about four seconds:

```bash
pip install -r requirements.txt
pytest                       # 88 passed
```

```
tests/direct/test_price_parsing.py      23 tests   numeric contract of the oracle
tests/direct/test_market.py             18 tests   open / match / cancel rules
tests/direct/test_settlement.py         13 tests   payout, void, refunds
tests/direct/test_oracle_consensus.py   11 tests   reduction + validator agreement
tests/unit/test_cli.py                  23 tests   stake parsing, argument surface
```

Highlights:

- `test_validator_agrees_within_its_tolerance_band` — swaps the mock so the
  validator reads a price 1% away from the leader's and asserts agreement.
- `test_validator_rejects_a_price_beyond_its_tolerance` — the same test at 10%
  asserts disagreement. Together these two pin down the equivalence relation.
- `test_thin_liquidity_is_refused` / `test_repeated_data_failures_void_the_war` —
  the failure paths that decide between refunding and paying.
- `test_no_float_rounding_drift` — parsing is integer-only, by construction.
- `test_stake_is_always_parsed_as_gen` — `--stake 1` is one GEN. It used to be
  one wei, which the CLI's own tests caught before anyone lost money to it.

### Which network should I use?

| Network | Chain ID | How you get GEN | Use it for |
| --- | --- | --- | --- |
| **Bradbury** | 4221 | [public faucet](https://testnet-faucet.genlayer.foundation) — funds any address | **The submitted demo.** Docs call it the production-like testnet with real AI/LLM workloads, and it has a public explorer |
| Asimov | 4221 | same public faucet | Infrastructure and stress testing |
| [Studionet](https://studio.genlayer.com) | 61999 | built-in 💧 button in the Studio account selector | Interactive poking in the browser |
| Localnet | 61127 | `client.fund_account(...)` | Fully local runs |

Two things that are easy to get wrong:

- **Asimov and Bradbury are not the same network, even though both report chain
  id 4221.** The GEN *balance* lives on the shared underlying L2 (so both
  faucets fund the same account), but each GenLayer RPC indexes its own consensus
  state, so a contract deployed through one is genuinely absent from the other.
  Reading a Bradbury contract through `rpc-asimov` fails with
  `contract not found` — which is exactly how this was discovered.
- **`genlayer-py` cannot fund an account on a testnet.** `fund_account()` raises
  unless the chain is localnet, so the money has to come from a faucet.
- **The Studionet faucet is a button in the Studio UI, not a public URL.** That
  is fine if your key is imported into Studio, but it is why the deploy script
  below targets Bradbury — a public faucet can fund a key that only exists in
  your terminal.

Read-only calls (`get_config`, `get_sources`, `preview_sample`) cost nothing and
work on any network, so they are the cheapest way to confirm the oracle's
numeric behaviour before spending anything.

### Deploy

Get testnet GEN from <https://testnet-faucet.genlayer.foundation>, then:

```bash
export GENLAYER_PRIVATE_KEY=0x...
export GENLAYER_NETWORK=bradbury     # production-like testnet; asimov is for stress testing
python deploy/deploy.py              # writes deploy/deployment.json
```

Deployed contracts are visible at `explorer-bradbury.genlayer.com`.

### Pre-flight a token before opening a war

The contract voids a war - and refunds both sides - if the two providers
disagree by more than 500 bps or the thinnest pool is under $25,000. Check
first, using the same two providers the contract reads:

```bash
python tools/check_token.py --chain base --address 0x532f27101965dd16442E59d40670FaF5eBB142E4
```

```
  dexscreener     price=0.005807  liquidity=$1,319,340
  geckoterminal   price=0.005804027131  liquidity=$1,240,284
  spread          0.05%
  thinnest pool   $1,240,284

PASS: passes both integrity rules
```

**Provider rate limits are a real operational risk.** DexScreener returns HTTP
429 quite aggressively per IP; three quick calls from one machine is enough to
trip it, and a 429 is indistinguishable from "provider down" to the contract -
the war retries and can void. The tool backs off and retries on 429 for this
reason, and it is worth checking the token immediately before opening a war
rather than minutes earlier.

### Use it from the CLI

```bash
python -m cli.meme_war config
python -m cli.meme_war sources --chain base --address 0x<token>
python -m cli.meme_war parse '$0.00012345'          # how the oracle reads a value
python -m cli.meme_war open --symbol PEPE --address 0x<token> --chain base --side UP --stake 0.01
python -m cli.meme_war list
python -m cli.meme_war join --war 0x<war> --stake 0.01
python -m cli.meme_war resolve --war 0x<war>
python -m cli.meme_war claim
```

### Frontend

```bash
cd frontend && python -m http.server 8080
# open http://localhost:8080/?address=0x<deployed contract>
```

No bundler and no `node_modules`. The page reads the live oracle policy from the
contract, lists open wars, and lets you open / join / resolve / claim through the
injected wallet.

---

## Design decisions worth knowing

**Stakes are matched, not pooled.** A war only becomes live when somebody takes
the exact opposite side at the same size. There is no house, no AMM and no
counterparty risk beyond the other player.

**The entry price is fixed at match time, by the contract.** Neither player can
choose a flattering entry; both sides trade the same observation, taken once
both are committed.

**Players cannot supply price sources.** Sources are derived by the contract from
`(chain, token)` against a fixed provider table. A player who could name the
source could name a fake one.

**Resolution is permissionless but not player-controlled.** Anyone may push an
expired war to settlement; the outcome comes from validators, not from the
caller.

**Payouts are pull-based.** Settlement credits a balance and `claim()` withdraws
it. A failed transfer to one winner can never block anyone else's settlement.

**Retry vs. void is explicit.** A provider that is unreachable is transient, so
the war retries up to `MAX_ATTEMPTS`. Providers that answer but contradict each
other, or that report a wash-tradeable pool, are a structural problem and void
immediately.

## Status

**Live on GenLayer Bradbury testnet** (chain 4221):

| | |
| --- | --- |
| Contract | `0x8F47f49A140a5e898E4eA0C4F473AFcBCBD6Af1f` |
| Deploy tx | `0x69b3197e600b7ccd67aae7d4683d16452f8bc572c5ff8a3ea132cf2db4b476c9` |
| Explorer | <https://explorer-bradbury.genlayer.com/address/0x8F47f49A140a5e898E4eA0C4F473AFcBCBD6Af1f> |

Verified against the live contract, not just locally:

- `get_config()` returns the policy the tests assert — 200 bps validator
  tolerance, 500 bps cross-source agreement, $25,000 liquidity floor.
- `preview_sample("$0.00012345")` returns `123450000000000`, so the integer
  parser behaves on chain exactly as it does in the suite.
- **A full round trip completed on chain.** A BRETT war was opened, matched
  (the contract read both providers on chain and locked an entry price of
  `0.00580086753` with `$1,219,802` of observed liquidity), settled after the 1h
  window at `0.005774663406`, and the winning side withdrew the pot.

  | Step | Transaction |
  | --- | --- |
  | Deploy | `0x69b3197e600b7ccd67aae7d4683d16452f8bc572c5ff8a3ea132cf2db4b476c9` |
  | Match — oracle reads entry | `0x4555b693a4819052964bf2dcf4c8ee934c189dd6a104bb2808308ed9c95b730e` |
  | Settle — oracle reads exit | `0x0b8e252964fa6e13db9a893950571480c244119cf45946cc01fc95c42ffe6021` |
  | Winner claims the pot | `0xd1169d4d1020faafaeefbc2ca4d6dda47e0b02abaf21fb5f8b088d5d30a10bda` |

Remaining:

- The frontend has **not** been run against the deployed contract (no browser
  available where this was built). Its first testnet run is its smoke test.
- `tests/integration/` needs `MEMEWAR_LIVE=1` and spends testnet funds.

## Notes on this network

Two things about GenLayer testnets cost real time here; both are handled in
`tools/net.py` so they do not cost you any:

- **Connections are unreliable.** TLS connections to `rpc-bradbury` get reset
  from some networks, and the RPC sits behind Cloudflare, which rejects Python's
  default `python-requests/*` User-Agent with a 403 challenge. Transient
  failures are retried, and every transaction hash is printed the moment the node
  returns it — a receipt poll that dies must not lose a deployment that already
  landed. If reads fail outright, route through a proxy via `HTTPS_PROXY`.
- **A single transaction is capped at 2^24 gas**, and the contract source *is*
  the deploy calldata, so a well-documented 25 KB contract estimates to ~20.7M
  gas and is rejected. `tools/minify.py` strips comments and docstrings at deploy
  time (18.0 KB, ~14.9M gas) by deleting lines rather than rewriting code, so
  what ships is byte-for-byte the code that was tested.

## Roadmap

- Third price provider so a single outage cannot block matching (the median path
  already handles 3+ readings).
- Bonded dispute window after settlement.
- Multi-outcome wars rather than direction only.

## License

MIT — see [LICENSE](LICENSE).
