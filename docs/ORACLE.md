# The oracle: agreeing on a number

This is the part of Meme War that has no equivalent in a deterministic chain and
no off-the-shelf oracle. It is worth writing down precisely.

## 1. Why `strict_eq` cannot settle a price market

The natural GenLayer pattern is:

```python
def get_price() -> str:
    page = gl.nondet.web.render(url, mode="text")
    return gl.nondet.exec_prompt(f"Extract the price: {page}")

result = gl.eq_principle.strict_eq(get_price)
```

`strict_eq` asks every validator to reproduce the leader's value **exactly**.
That is right for a fact with a canonical spelling — a football score, a winner's
name — and wrong for a number that is being repriced continuously:

- the leader and each validator execute at different moments;
- the aggregator page they read has moved in between;
- two *correct* readings differ in the last digits;
- the comparison fails, the transaction never reaches consensus, and the stake
  cannot be released.

The failure is not a bug in the contract, it is the wrong equivalence relation.
A numeric fact needs a *tolerance*, and the tolerance has to be small enough to
catch a validator that read a genuinely different market.

## 2. Two levels of agreement

Meme War compares numbers at two distinct levels, and they answer different
questions.

### Level 1 — cross-source agreement (is the data real?)

Two independent providers are read: DexScreener and GeckoTerminal. Each returns a
price and a liquidity figure.

```
spread = |max(price) - min(price)| / min(price)
if spread > 500 bps:  the data is not trustworthy
```

This is what catches a single provider that is stale, wrong, or being fed a
manipulated pool. It is a *market integrity* check, not a consensus check, so it
happens inside the leader's computation.

### Level 2 — leader/validator tolerance (did we read the same market?)

```python
def validator_fn(result) -> bool:
    if not isinstance(result, gl.vm.Return):
        return False                       # a failed leader is not agreed to
    leader = json.loads(result.calldata)
    mine = observe_price(sources)          # full independent re-derivation
    return relative_spread_bps(mine["price"], leader["price"]) <= 200
```

The validator does not re-compare pages and does not compare JSON documents. It
re-derives the number from scratch and accepts the leader when the two numbers
are within **2%**.

2% is chosen against the two failure modes either side of it:

| Too tight | Too loose |
| --- | --- |
| Ordinary drift between two executions trips consensus, war can never settle | A validator that read a different token or a manipulated pool agrees anyway |

The liquidity figure is compared with a much wider band (100%) on purpose: pool
depth is far more volatile than price and its *job* is the floor test, which
`observe_price` applies independently on each side.

`test_validator_agrees_within_its_tolerance_band` and
`test_validator_rejects_a_price_beyond_its_tolerance` pin both edges of this band
down.

## 3. From readings to one number

```
for each provider:
    fetch JSON          (gl.nondet.web.get)
    status must be 200  (an error page is not a price)
    extract price + liquidity
    skip on any failure

require all providers answered        -> else DATA_UNAVAILABLE
require spread <= SOURCE_AGREEMENT_BPS -> else DATA_DISAGREE
require min(liquidity) >= floor        -> else DATA_THIN
price = consensus_price(prices)
```

`consensus_price` treats two readings and three-or-more readings differently:

- **two readings** — the median is undefined, and always taking the lower one
  would systematically bias the entry price against the UP side. The midpoint is
  used instead, which is symmetric between the two.
- **three or more** — the median, which discards a single manipulated outlier.

That is why adding a third provider is a roadmap item rather than a nicety: it
changes the reduction from "average of two" to "outlier-rejecting median", and
it removes the single point of failure where one provider being down blocks
matching entirely.

## 4. Failure taxonomy: retry or void?

Not all bad data is the same, and conflating the two would either strand stakes
or pay out on garbage.

| Reason | Meaning | Response |
| --- | --- | --- |
| `DATA_UNAVAILABLE` | a provider did not answer | **Transient** — retry, up to `MAX_ATTEMPTS`, then void and refund |
| `DATA_DISAGREE` | providers answered but contradict each other | **Structural** — void and refund *immediately* |
| `DATA_THIN` | the pool is too shallow to trust | **Structural** — void and refund *immediately* |

The reasoning: an unreachable provider may well be reachable in ten minutes, and
the players should not lose their stakes to a rate limit. A provider that
answers with a contradictory number is telling you the market itself is not
coherent right now, and re-running will not fix that — so the war voids rather
than settle on data nobody should trust.

The reasons cross the consensus boundary as machine-readable prefixes
(`MemeWar:DATA_DISAGREE`) precisely so the settle path can branch on them without
parsing prose.

## 5. Determinism

Everything outside `gl.vm.run_nondet` is deterministic integer arithmetic.

- **No `float` anywhere.** Provider values are parsed digit-by-digit into
  integers scaled by `10**18` (price) or `10**6` (USD liquidity). A binary float
  is not the same number on every platform, and this number decides who gets
  paid.
- **Total parsing.** `parse_decimal` returns `None` for anything it cannot
  understand — `"abc"`, `"1.2.3"`, `"1e400"`, `""` — rather than guessing. A
  value that cannot be parsed is a provider that did not answer.
- **Block time, not wall clock.** `_now()` reads `datetime.now`, which GenVM
  intercepts and returns the transaction timestamp from, so deadline checks are
  reproducible.
- **Conservative reduction.** When the two readings straddle the settlement
  boundary a tie voids rather than picking a side.

The parsing contract is public: `preview_sample(raw)` returns exactly how the
oracle would read a given string, so anyone relying on a war can check the
semantics instead of reading the source.

## 6. Attack surface

| Attack | Mitigation |
| --- | --- |
| Point the oracle at a fake source | Sources are derived by the contract from `(chain, token)`; players cannot supply them |
| Wash-trade a dust pool to an arbitrary price | Liquidity floor of $25,000, enforced on every reading |
| Manipulate one provider only | Two independent providers must corroborate within 500 bps |
| Front-run the entry price | Entry price is fixed by the contract at match time, not chosen by either player |
| Creator takes both sides | Rejected explicitly |
| Resolve twice / double payout | Status machine refuses a second settlement; refunds cannot stack |
| A failing transfer blocks other users | Payouts are pull-based; settlement credits, `claim()` withdraws |
| Stakes stranded by an outage | Bounded retries, then automatic void and refund |

## 7. Not covered yet

- **A third provider.** Today one provider outage blocks matching. The median
  path already handles 3+ readings.
- **A bonded dispute window.** Settlement is final as soon as validators agree.
- **Historical price anchoring.** Entry and exit are spot observations; a TWAP
  over a short window would further blunt a one-block manipulation.
- **Chain coverage.** The provider table covers six chains; a token on a chain
  with no aggregator coverage cannot open a war.
