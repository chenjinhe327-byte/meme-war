"""Make genlayer-py survive a flaky connection.

`GenLayerProvider.make_request` issues a single `requests.post` with **no
timeout and no retry**:

    response = requests.post(self.url, json=payload, headers={...})

One dropped TLS connection - which happens routinely on a network that has to
tunnel out through a proxy - therefore kills an otherwise fine deploy partway
through, with an `SSLEOFError` that says nothing about which call failed.

The GenLayer RPC sits behind Cloudflare, which also rejects the default
`python-requests/*` User-Agent with a 403 challenge page; genlayer-py sets
`genlayer-py`, which is allowed, so that part is already fine.

This installs bounded retries around transient failures only. Re-sending a signed
transaction is safe: the payload is identical, so it is the same transaction
hash and the node treats the second one as a duplicate.
"""

from __future__ import annotations

import time

TRANSIENT_MARKERS = (
    "SSLError",
    "SSLEOFError",
    "ConnectionError",
    "ConnectionResetError",
    "ConnectionAbortedError",
    "ProtocolError",
    "ChunkedEncodingError",
    "Timeout",
    "timed out",
    "Max retries exceeded",
    "Temporary failure",
    "RemoteDisconnected",
    "EOF occurred",
    "ended prematurely",
    "502",
    "503",
    "504",
)

DEFAULT_ATTEMPTS = 6
DEFAULT_BASE_DELAY = 1.5


def is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    for marker in TRANSIENT_MARKERS:
        if marker in text:
            return True
    # GenLayerError wraps the underlying requests error, so walk the chain.
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None and cause is not exc:
        return is_transient(cause)
    return False


def install_retries(attempts: int = DEFAULT_ATTEMPTS, base_delay: float = DEFAULT_BASE_DELAY) -> bool:
    """Wrap the provider's request method. Returns True if it was installed."""
    try:
        from genlayer_py.provider.provider import GenLayerProvider
    except ImportError:
        return False

    if getattr(GenLayerProvider.make_request, "_retrying", False):
        return True

    original = GenLayerProvider.make_request

    def make_request(self, method, params):
        delay = base_delay
        last_error = None
        for attempt in range(attempts):
            try:
                return original(self, method, params)
            except Exception as exc:  # noqa: BLE001 - re-raised below when not transient
                if not is_transient(exc):
                    raise
                last_error = exc
                if attempt < attempts - 1:
                    print(
                        f"  network hiccup on {method} "
                        f"({type(exc).__name__}), retry {attempt + 2}/{attempts} in {delay:.1f}s",
                        flush=True,
                    )
                    time.sleep(delay)
                    delay *= 1.7
        raise last_error

    make_request._retrying = True  # type: ignore[attr-defined]
    GenLayerProvider.make_request = make_request
    return True


def install_response_shims(sink=print) -> bool:
    """Teach genlayer-py 0.18 to read what the current RPC actually returns.

    `read_contract` does:

        enc_result = make_request("gen_call", ...)["result"]
        calldata.decode(bytes.fromhex("0x" + enc_result))

    but the node answers `gen_call` with an *object*:

        {"data": "<hex>", "eqOutputs": [], "status": {...}, "logs": [...]}

    so `"0x" + enc_result` raises `TypeError: can only concatenate str (not
    "dict") to str` for every read. Flattening `result` to its `data` field
    restores the shape the SDK expects. Writes are unaffected - their receipts
    already match.
    """
    try:
        from genlayer_py.provider.provider import GenLayerProvider
    except ImportError:
        return False

    if getattr(GenLayerProvider.make_request, "_shimming_reads", False):
        return True

    original = GenLayerProvider.make_request

    def make_request(self, method, params):
        response = original(self, method, params)
        if method == "gen_call" and isinstance(response, dict):
            result = response.get("result")
            if isinstance(result, dict):
                status = result.get("status") or {}
                if status.get("code") not in (None, 0):
                    sink(
                        f"  gen_call reported status {status.get('code')}: "
                        f"{status.get('message')}"
                    )
                response["result"] = result.get("data", "")
        return response

    make_request._shimming_reads = True  # type: ignore[attr-defined]
    GenLayerProvider.make_request = make_request
    return True


def install_tx_hash_logger(sink=print) -> bool:
    """Print every submitted transaction hash the moment the node returns it.

    A deploy is two steps: submit, then poll for the receipt. On a flaky link the
    poll can die after the transaction has already been mined - which is exactly
    what happened once here, leaving a deployed contract nobody could name. The
    hash is knowable the instant the node accepts the transaction, so print it
    then instead of after the receipt arrives.
    """
    try:
        from genlayer_py.provider.provider import GenLayerProvider
    except ImportError:
        return False

    if getattr(GenLayerProvider.make_request, "_logging_hashes", False):
        return True

    original = GenLayerProvider.make_request

    def make_request(self, method, params):
        result = original(self, method, params)
        if method == "eth_sendRawTransaction" and isinstance(result, dict):
            tx_hash = result.get("result")
            if tx_hash:
                sink(f"submitted tx {tx_hash}")
        return result

    make_request._logging_hashes = True  # type: ignore[attr-defined]
    GenLayerProvider.make_request = make_request
    return True


class _TolerantStatusTable(dict):
    """A dict that names unknown status codes instead of raising KeyError."""

    def __init__(self, base, sink):
        super().__init__(base)
        self._sink = sink
        self._reported = set()

    def __missing__(self, key):
        from genlayer_py.types.transactions import TransactionStatus

        if key not in self._reported:
            self._sink(f"  unknown consensus status {key!r}; treating as pending")
            self._reported.add(key)
        return TransactionStatus.PENDING


def install_status_shim(sink=print) -> bool:
    """Survive consensus status codes this SDK version has never heard of.

    genlayer-py 0.18 maps status numbers 0-13. The network has since added at
    least one more (14), and the decoder indexes that table directly, so an
    unknown code raises `KeyError: '14'` *before* any polling can happen - the
    transaction is fine, the client simply cannot name its state.

    Unknown codes are treated as "not settled yet", which keeps
    `wait_for_transaction_receipt` polling rather than crashing. That is the
    conservative reading: an unrecognised state is far likelier to be
    mid-consensus than a final success.
    """
    try:
        from genlayer_py.types import transactions as tx_types
    except ImportError:
        return False

    table = tx_types.TRANSACTION_STATUS_NUMBER_TO_NAME
    if isinstance(table, _TolerantStatusTable):
        return True

    tx_types.TRANSACTION_STATUS_NUMBER_TO_NAME = _TolerantStatusTable(table, sink)
    return True


def wait_for_receipt(client, tx_hash, status=None, retries: int = 72, interval: int = 5000, **kwargs):
    """Poll for a receipt far longer than the SDK's 30 second default.

    `wait_for_transaction_receipt` gives up after 10 attempts x 3s. On this
    network a write routinely sits in COMMITTING for longer than that, so the
    default turns a perfectly good transaction into a timeout error - which is
    how several writes here "failed" after they had already succeeded. 72 x 5s is
    six minutes, comfortably past normal consensus without hanging forever.
    """
    from genlayer_py.types import TransactionStatus

    return client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        status=status or TransactionStatus.ACCEPTED,
        retries=retries,
        interval=interval,
        **kwargs,
    )


def install_all(sink=print) -> None:
    """Install every workaround this network needs, in the right order."""
    install_retries()
    install_tx_hash_logger(sink)
    install_response_shims(sink)
    install_status_shim(sink)
