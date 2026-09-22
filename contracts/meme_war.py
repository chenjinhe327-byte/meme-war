# v2.0.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
#
# The runner hash above is pinned on purpose: it is the py-genlayer v0.2.16
# runner the whole test suite was executed against (and the one the official
# genlayer-project-boilerplate pins). `latest` would let the deployed artifact
# drift away from the tested one.
#
# Meme War v2 - trustless PvP settlement for assets that have no oracle.
#
# A war is a two-sided, equal-stake bet on the direction of a long-tail token
# over a fixed window. Two players lock the same stake; the contract observes an
# entry price when the war is matched and an exit price when it expires; the
# winner takes the pot.
#
# Why this needs GenLayer, and not a classic oracle:
#
#   Chainlink-style feeds only exist for assets somebody paid to list. The whole
#   point of a meme-coin war is the token that has no feed, no listing, and no
#   API contract - the only place its price exists is a DEX aggregator page.
#   Reading that page and agreeing on a *number* is exactly what GenLayer
#   validators can do and a deterministic chain cannot.
#
# The hard part is not fetching a price, it is agreeing on one. Two validators
# never see byte-identical pages: the price moves between their two executions,
# pagination differs, a field is renamed. A contract that compares an exact
# price string therefore never reaches consensus and can never settle. This
# contract instead compares prices *numerically, within a tolerance band*, and
# requires two independent providers to corroborate each other. That is the
# core primitive here; everything else is bookkeeping around it.

import datetime
import json
from dataclasses import dataclass

from genlayer import *


# --- economics / policy ------------------------------------------------------

PRICE_DECIMALS = 18
PRICE_ONE = 10 ** PRICE_DECIMALS

# Validators independently re-read the same two providers. Between the leader's
# execution and the validator's the price legitimately drifts, so they agree if
# their independently computed medians are within this band.
VALIDATOR_TOLERANCE_BPS = 200  # 2%

# Two *independent* providers must corroborate each other. Beyond this spread the
# data is not trustworthy and the war voids rather than paying out.
SOURCE_AGREEMENT_BPS = 500  # 5%

# Anti-manipulation: a token whose pools are thinner than this is trivially
# wash-traded to any price the attacker wants. Wars on such a token void.
MIN_LIQUIDITY_USD = 25_000
LIQUIDITY_DECIMALS = 6  # providers report USD with 6 decimals of precision
LIQUIDITY_SCALE = 10 ** LIQUIDITY_DECIMALS

MATCH_WINDOW_SECONDS = 6 * 3600
MIN_TIME_TO_RESOLVE = 3600
MAX_ATTEMPTS = 3

# Failure reasons are machine-readable prefixes so callers can tell a structural
# problem (the market is not trustworthy - void it) apart from a transient one
# (a provider blinked - try again later).
ERR_DATA_UNAVAILABLE = "MemeWar:DATA_UNAVAILABLE"
ERR_DATA_DISAGREE = "MemeWar:DATA_DISAGREE"
ERR_DATA_THIN = "MemeWar:DATA_THIN"
STRUCTURAL_DATA_ERRORS = (ERR_DATA_DISAGREE, ERR_DATA_THIN)

ST_OPEN = "OPEN"
ST_MATCHED = "MATCHED"
ST_RESOLVED = "RESOLVED"
ST_VOID = "VOID"

SIDE_UP = "UP"
SIDE_DOWN = "DOWN"
WINNER_VOID = "VOID"

TIMEFRAMES = {"1h": 3600, "24h": 86400, "7d": 604800}

# Providers are chosen by the contract, never by the player. A player who could
# supply the price source could supply a fake one.
PROVIDER_TEMPLATES = (
    (
        "dexscreener",
        "https://api.dexscreener.com/latest/dex/tokens/{address}",
    ),
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


# --- storage -----------------------------------------------------------------


@allow_storage
@dataclass
class War:
    war_id: str
    creator: Address
    creator_side: str
    opponent: Address
    token_symbol: str
    token_address: str
    chain: str
    timeframe: str
    stake: u256
    pot: u256
    created_at: u256
    match_deadline: u256
    resolve_at: u256
    entry_price: u256
    entry_liquidity: u256
    exit_price: u256
    status: str
    winner: str
    attempts: u256
    settled_at: u256


# --- deterministic numeric helpers ------------------------------------------
#
# Everything below is pure integer arithmetic. LLM and HTTP output is untrusted
# text, so it is converted into scaled integers before any of it can influence
# money, and every conversion is total (returns None instead of raising).


def _materialize(value):
    """Force a lazy nondeterministic result to its concrete value.

    The SDK hands back ``Lazy`` wrappers which forward most operations but not
    all, and the direct test runner returns plain values instead. Unwrapping
    explicitly keeps contracts working identically under both.
    """
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return value
    return value


def _now() -> int:
    """Transaction time in unix seconds.

    GenVM intercepts ``datetime.now`` and returns the transaction timestamp, so
    this is deterministic across validators rather than the host clock.
    """
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())


def parse_decimal(text, decimals: int = PRICE_DECIMALS):
    """Parse a human/JSON decimal into a scaled integer, or None.

    Deliberately avoids ``float``: binary floats are not the same number on
    every platform and this value decides who gets paid. Accepts thousands
    separators, a leading currency symbol, signs and exponent notation because
    every one of those shows up in real provider payloads.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if raw == "":
        return None
    for junk in ("$", ",", "_", " ", "\u00a0"):
        raw = raw.replace(junk, "")
    if raw == "":
        return None

    sign = 1
    if raw[0] in "+-":
        if raw[0] == "-":
            sign = -1
        raw = raw[1:]
    if raw == "":
        return None

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
    if not whole.isdigit():
        return None
    if fraction and not fraction.isdigit():
        return None

    digits_text = whole + fraction
    digits = int(digits_text)
    # Position of the decimal point relative to the end of `digits`, then shift
    # by the requested scale.
    shift = len(whole) + exponent - len(digits_text) + decimals
    if shift >= 0:
        return sign * digits * (10 ** shift)
    divisor = 10 ** (-shift)
    return sign * (digits // divisor)


def consensus_price(values: list) -> int:
    """Reduce independent readings to the single number a war trades on.

    With exactly two providers the median is ambiguous - picking the lower one
    would systematically bias the entry price against the UP side - so the
    midpoint is used instead. With three or more readings the median wins, which
    discards a single manipulated outlier.
    """
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    if len(ordered) == 2:
        return (ordered[0] + ordered[1]) // 2
    return ordered[(len(ordered) - 1) // 2]


def relative_spread_bps(a: int, b: int) -> int:
    """|a-b| / min(a,b), in basis points. Asymmetric-free and integer-only."""
    if a <= 0 or b <= 0:
        return 10 ** 9
    low = a if a < b else b
    high = b if a < b else a
    return ((high - low) * 10000) // low


def _parse_usd(text):
    """Provider USD fields are strings with 6 decimals of precision."""
    return parse_decimal(text, LIQUIDITY_DECIMALS)


def extract_dexscreener(payload: dict):
    """Pick the deepest pool from a DexScreener token payload."""
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return None
    best_price = None
    best_liquidity = None
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        price = parse_decimal(pair.get("priceUsd"))
        liquidity = pair.get("liquidity") or {}
        liquidity_usd = _parse_usd(liquidity.get("usd"))
        if price is None or price <= 0 or liquidity_usd is None:
            continue
        if best_liquidity is None or liquidity_usd > best_liquidity:
            best_liquidity = liquidity_usd
            best_price = price
    if best_price is None:
        return None
    return {"price": best_price, "liquidity": best_liquidity}


def extract_geckoterminal(payload: dict):
    """Read the GeckoTerminal token payload shape."""
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    attributes = data.get("attributes")
    if not isinstance(attributes, dict):
        return None
    price = parse_decimal(attributes.get("price_usd"))
    liquidity = _parse_usd(attributes.get("total_reserve_in_usd"))
    if price is None or price <= 0 or liquidity is None:
        return None
    return {"price": price, "liquidity": liquidity}


EXTRACTORS = {
    "dexscreener": extract_dexscreener,
    "geckoterminal": extract_geckoterminal,
}


def canonical_sources(chain: str, token_address: str) -> list:
    network = CHAIN_NETWORK.get(chain)
    if network is None:
        raise gl.vm.UserError("MemeWar: unsupported chain")
    sources = []
    for name, template in PROVIDER_TEMPLATES:
        url = template.replace("{address}", token_address).replace("{network}", network)
        sources.append({"provider": name, "url": url})
    return sources


def observe_price(sources: list) -> dict:
    """Read every provider and reduce the readings to one number.

    Returns a plain dict so it can cross the consensus boundary. Raises
    ``gl.vm.UserError`` when the data is not good enough to settle on, which
    callers turn into a void rather than a payout.
    """
    samples = []
    for source in sources:
        provider = source["provider"]
        extractor = EXTRACTORS[provider]
        try:
            response = _materialize(gl.nondet.web.get(source["url"]))
        except Exception:
            continue  # provider unreachable / blocked / rate limited
        if response.status != 200 or not response.body:
            continue
        try:
            payload = json.loads(bytes(response.body).decode("utf-8"))
        except Exception:
            continue
        sample = extractor(payload)
        if sample is None:
            continue
        sample["provider"] = provider
        samples.append(sample)

    if len(samples) < len(sources):
        raise gl.vm.UserError(
            ERR_DATA_UNAVAILABLE
            + ": only "
            + str(len(samples))
            + " of "
            + str(len(sources))
            + " providers answered"
        )

    prices = [sample["price"] for sample in samples]
    liquidities = [sample["liquidity"] for sample in samples]

    spread = relative_spread_bps(min(prices), max(prices))
    if spread > SOURCE_AGREEMENT_BPS:
        raise gl.vm.UserError(
            ERR_DATA_DISAGREE
            + ": providers disagree by "
            + str(spread // 100)
            + " bps"
        )

    liquidity = min(liquidities)
    if liquidity < MIN_LIQUIDITY_USD * LIQUIDITY_SCALE:
        raise gl.vm.UserError(ERR_DATA_THIN + ": liquidity below the floor")

    return {
        "price": consensus_price(prices),
        "liquidity": liquidity,
        "spread_bps": spread,
        "providers": len(samples),
    }


def agreed_price(sources: list) -> dict:
    """Wrap ``observe_price`` in leader/validator consensus.

    The validator does not re-compare pages, it re-derives the *number* and
    accepts the leader when the two numbers are within
    ``VALIDATOR_TOLERANCE_BPS``. Comparing numbers with a tolerance is what
    makes a price-based Intelligent Contract settleable at all; a byte-exact
    comparison of two live pages would essentially never agree.
    """

    def leader_fn() -> str:
        observation = observe_price(sources)
        return json.dumps(observation, sort_keys=True)

    def validator_fn(result) -> bool:
        if not isinstance(result, gl.vm.Return):
            # A leader that failed because the data is bad is legitimate; only
            # re-run when the leader claims success.
            return False
        leader_payload = result.calldata
        if not isinstance(leader_payload, str):
            return False
        try:
            leader = json.loads(leader_payload)
        except Exception:
            return False

        try:
            mine = observe_price(sources)
        except Exception:
            return False

        if relative_spread_bps(mine["price"], leader["price"]) > VALIDATOR_TOLERANCE_BPS:
            return False
        return relative_spread_bps(mine["liquidity"], leader["liquidity"]) <= 10000

    raw = _materialize(gl.vm.run_nondet(leader_fn, validator_fn))
    return json.loads(raw)


# --- contract ----------------------------------------------------------------


class MemeWar(gl.Contract):
    wars: TreeMap[str, War]
    open_ids: DynArray[str]
    owner_ids: TreeMap[Address, DynArray[str]]
    claims: TreeMap[Address, u256]
    total_wars: u256
    total_settled: u256

    def __init__(self):
        self.total_wars = u256(0)
        self.total_settled = u256(0)

    # -- market making --------------------------------------------------------

    @gl.public.write.payable
    def create_war(
        self,
        token_symbol: str,
        token_address: str,
        chain: str,
        timeframe: str,
        side: str,
    ) -> str:
        """Open a war and lock the creator's stake as the first side of the pot."""
        stake = int(gl.message.value)
        if stake <= 0:
            raise gl.vm.UserError("MemeWar: a stake is required to open a war")

        symbol = token_symbol.strip().upper()
        address = token_address.strip()
        network = chain.strip().lower()
        window = timeframe.strip().lower()
        direction = side.strip().upper()

        if symbol == "" or len(symbol) > 24:
            raise gl.vm.UserError("MemeWar: token symbol length out of range")
        if address == "" or len(address) > 128:
            raise gl.vm.UserError("MemeWar: token address length out of range")
        if network not in CHAIN_NETWORK:
            raise gl.vm.UserError("MemeWar: unsupported chain")
        if window not in TIMEFRAMES:
            raise gl.vm.UserError("MemeWar: unsupported timeframe")
        if direction not in (SIDE_UP, SIDE_DOWN):
            raise gl.vm.UserError("MemeWar: side must be UP or DOWN")

        sender = gl.message.sender_address
        now = _now()
        war_id = "0x" + Keccak256(
            (
                sender.as_hex
                + "|"
                + network
                + "|"
                + address
                + "|"
                + window
                + "|"
                + str(int(self.total_wars))
            ).encode("utf-8")
        ).hexdigest()

        self.wars[war_id] = War(
            war_id=war_id,
            creator=sender,
            creator_side=direction,
            opponent=Address(b"\x00" * 20),
            token_symbol=symbol,
            token_address=address,
            chain=network,
            timeframe=window,
            stake=u256(stake),
            pot=u256(stake),
            created_at=u256(now),
            match_deadline=u256(now + MATCH_WINDOW_SECONDS),
            resolve_at=u256(now + TIMEFRAMES[window]),
            entry_price=u256(0),
            entry_liquidity=u256(0),
            exit_price=u256(0),
            status=ST_OPEN,
            winner="",
            attempts=u256(0),
            settled_at=u256(0),
        )
        self.open_ids.append(war_id)
        self.owner_ids.get_or_insert_default(sender).append(war_id)
        self.total_wars = self.total_wars + u256(1)
        return war_id

    @gl.public.write.payable
    def join_war(self, war_id: str) -> None:
        """Take the other side, matching the stake exactly.

        The entry price is fixed here, once both sides are committed, so neither
        player can pick a flattering entry and both trade the same observation.
        """
        if war_id not in self.wars:
            raise gl.vm.UserError("MemeWar: unknown war")
        war = self.wars[war_id]

        if war.status != ST_OPEN:
            raise gl.vm.UserError("MemeWar: war is not open")
        if _now() > int(war.match_deadline):
            raise gl.vm.UserError("MemeWar: matching window has closed")

        sender = gl.message.sender_address
        if sender == war.creator:
            raise gl.vm.UserError("MemeWar: creator cannot take both sides")
        if int(gl.message.value) != int(war.stake):
            raise gl.vm.UserError("MemeWar: stake must match the creator exactly")

        sources = canonical_sources(war.chain, war.token_address)
        observation = agreed_price(sources)

        war.opponent = sender
        war.entry_price = u256(observation["price"])
        war.entry_liquidity = u256(observation["liquidity"])
        war.pot = u256(int(war.stake) * 2)
        war.status = ST_MATCHED
        self.owner_ids.get_or_insert_default(sender).append(war_id)

    @gl.public.write
    def cancel_open_war(self, war_id: str) -> None:
        """Reclaim a stake that nobody matched once the window has closed."""
        if war_id not in self.wars:
            raise gl.vm.UserError("MemeWar: unknown war")
        war = self.wars[war_id]
        if war.status != ST_OPEN:
            raise gl.vm.UserError("MemeWar: war is not open")
        if _now() <= int(war.match_deadline):
            raise gl.vm.UserError("MemeWar: matching window is still open")
        if gl.message.sender_address != war.creator:
            raise gl.vm.UserError("MemeWar: only the creator can cancel")

        war.status = ST_VOID
        war.winner = WINNER_VOID
        war.settled_at = u256(_now())
        self._credit(war.creator, int(war.stake))

    # -- settlement -----------------------------------------------------------

    @gl.public.write
    def resolve_war(self, war_id: str) -> str:
        """Settle a matched war once it has expired.

        Permissionless: anyone may push the result, because the result is
        decided by validators rather than by the caller. Unusable data voids the
        war and refunds both stakes - refusing to pay is always safer than
        paying the wrong side.
        """
        if war_id not in self.wars:
            raise gl.vm.UserError("MemeWar: unknown war")
        war = self.wars[war_id]

        if war.status != ST_MATCHED:
            raise gl.vm.UserError("MemeWar: war is not awaiting settlement")
        if _now() < int(war.resolve_at):
            raise gl.vm.UserError("MemeWar: the war has not expired yet")

        sources = canonical_sources(war.chain, war.token_address)

        try:
            observation = agreed_price(sources)
        except Exception as exc:
            # A provider that blinks is worth retrying. Providers that answer but
            # contradict each other, or that report a wash-tradeable pool, are a
            # structural problem: void now instead of settling on bad data.
            text = str(exc)
            structural = False
            for marker in STRUCTURAL_DATA_ERRORS:
                if marker in text:
                    structural = True
            war.attempts = war.attempts + u256(1)
            if structural or int(war.attempts) >= MAX_ATTEMPTS:
                self._void(war)
            return WINNER_VOID

        exit_price = int(observation["price"])
        entry_price = int(war.entry_price)

        if exit_price == entry_price:
            self._void(war)
            return WINNER_VOID

        winning_side = SIDE_UP if exit_price > entry_price else SIDE_DOWN
        winner = war.creator if war.creator_side == winning_side else war.opponent

        war.exit_price = u256(exit_price)
        war.winner = winning_side
        war.status = ST_RESOLVED
        war.settled_at = u256(_now())
        self.total_settled = self.total_settled + u256(1)
        self._credit(winner, int(war.pot))
        return winning_side

    def _void(self, war) -> None:
        war.status = ST_VOID
        war.winner = WINNER_VOID
        war.settled_at = u256(_now())
        self._credit(war.creator, int(war.stake))
        if war.opponent != Address(b"\x00" * 20):
            self._credit(war.opponent, int(war.stake))

    def _credit(self, account, amount: int) -> None:
        if amount <= 0:
            return
        self.claims[account] = u256(int(self.claims.get(account, u256(0))) + amount)

    @gl.public.write
    def claim(self) -> None:
        """Withdraw everything credited to the caller.

        Pull rather than push: a failed transfer to one winner must never be
        able to block the settlement of anyone else's war.
        """
        sender = gl.message.sender_address
        amount = int(self.claims.get(sender, u256(0)))
        if amount <= 0:
            raise gl.vm.UserError("MemeWar: nothing to claim")
        if int(self.balance) < amount:
            raise gl.vm.UserError("MemeWar: contract balance is short")
        self.claims[sender] = u256(0)
        gl.get_contract_at(sender).emit_transfer(value=u256(amount))

    # -- views ----------------------------------------------------------------

    @gl.public.view
    def get_war(self, war_id: str) -> dict:
        if war_id not in self.wars:
            return {}
        war = self.wars[war_id]
        return {
            "war_id": war.war_id,
            "creator": war.creator.as_hex,
            "creator_side": war.creator_side,
            "opponent": war.opponent.as_hex,
            "token_symbol": war.token_symbol,
            "token_address": war.token_address,
            "chain": war.chain,
            "timeframe": war.timeframe,
            "stake": int(war.stake),
            "pot": int(war.pot),
            "created_at": int(war.created_at),
            "match_deadline": int(war.match_deadline),
            "resolve_at": int(war.resolve_at),
            "entry_price": int(war.entry_price),
            "entry_liquidity": int(war.entry_liquidity),
            "exit_price": int(war.exit_price),
            "status": war.status,
            "winner": war.winner,
            "attempts": int(war.attempts),
            "settled_at": int(war.settled_at),
        }

    @gl.public.view
    def list_open(self, limit: u256) -> list:
        out = []
        for war_id in list(self.open_ids):
            war = self.wars[war_id]
            if war.status != ST_OPEN:
                continue
            out.append(self.get_war(war_id))
            if len(out) >= int(limit):
                break
        return out

    @gl.public.view
    def list_by_owner(self, account: str, limit: u256) -> list:
        addr = Address(account)
        if addr not in self.owner_ids:
            return []
        out = []
        for war_id in list(self.owner_ids[addr]):
            out.append(self.get_war(war_id))
            if len(out) >= int(limit):
                break
        return out

    @gl.public.view
    def get_claim(self, account: str) -> int:
        return int(self.claims.get(Address(account), u256(0)))

    @gl.public.view
    def get_sources(self, chain: str, token_address: str) -> list:
        """The canonical price sources a war on this token would read."""
        return canonical_sources(chain.strip().lower(), token_address.strip())

    @gl.public.view
    def preview_sample(self, raw_value: str) -> dict:
        """How the oracle would read a raw provider value.

        Exposed on purpose: the numeric contract between "what a DEX page says"
        and "what the contract stores" should be inspectable by anyone relying
        on a war, not buried in the source.
        """
        return {
            "raw": raw_value,
            "price_scaled": parse_decimal(raw_value, PRICE_DECIMALS),
            "usd_scaled": parse_decimal(raw_value, LIQUIDITY_DECIMALS),
        }

    @gl.public.view
    def get_config(self) -> dict:
        return {
            "price_decimals": PRICE_DECIMALS,
            "validator_tolerance_bps": VALIDATOR_TOLERANCE_BPS,
            "source_agreement_bps": SOURCE_AGREEMENT_BPS,
            "min_liquidity_usd": MIN_LIQUIDITY_USD,
            "match_window_seconds": MATCH_WINDOW_SECONDS,
            "max_attempts": MAX_ATTEMPTS,
            "timeframes": list(TIMEFRAMES.keys()),
            "chains": list(CHAIN_NETWORK.keys()),
            "total_wars": int(self.total_wars),
            "total_settled": int(self.total_settled),
        }
