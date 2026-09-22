"""Compatibility shim: run ``py-genlayer`` v0.2 contracts under the v0.3 test runner.

The GenLayer test runner (``genlayer-test`` 0.29.x) resolves the SDK through a
v0.3-shaped layout::

    from genlayer import calldata          # v0.3
    from genlayer import types as sdk_types

In the runner that the network's ``py-genlayer:latest`` currently resolves to
(v0.2.16, the runner the official ``genlayer-project-boilerplate`` pins) those
modules live one level deeper::

    genlayer.py.calldata
    genlayer.py.types

Because every one of these helpers swallows ``ImportError``, the failure is
silent and nasty: the message context is never injected into fd 0, so the
contract dies at import time with ``unexpected end of memory``, and
``vm.sender`` stops propagating to ``gl.message``. This module rebinds those
helpers to the v0.2 paths. It is a no-op when a v0.3 SDK is active, so the same
test suite keeps working once the runner moves on.

The rebinding has to happen *after* the runner puts the SDK on ``sys.path`` but
*before* the message is injected, so ``sdk_loader.setup_sdk_paths`` is wrapped.
"""

from __future__ import annotations

import os
import sys

# The runner picks the newest cached GenVM version unless this is pinned, and a
# stale v0.3 prerelease in the cache would otherwise shadow v0.2.16.
PINNED_GENVM_VERSION = "v0.2.16"

_installed = False


def _v02_modules():
    try:
        import genlayer.py.calldata as calldata
        import genlayer.py.types as types
    except ImportError:
        return None
    return types, calldata


def _install() -> bool:
    global _installed
    if _installed:
        return True

    found = _v02_modules()
    if found is None:
        # A v0.3 SDK is active - the runner's own helpers are correct.
        return False

    types, calldata = found

    import gltest.direct.loader as loader
    import gltest.direct.sdk_compat as sdk_compat
    import gltest.direct.vm as vm_mod
    import gltest.direct.wasi_mock as wasi_mock

    get_calldata = lambda: calldata  # noqa: E731
    get_types = lambda: types  # noqa: E731
    get_address = lambda: types.Address  # noqa: E731
    get_lazy = lambda: types.Lazy  # noqa: E731
    get_address_u256 = lambda: (types.Address, types.u256)  # noqa: E731

    sdk_compat.import_calldata = get_calldata
    sdk_compat.import_types = get_types
    sdk_compat.import_address = get_address
    sdk_compat.import_lazy = get_lazy
    sdk_compat.import_address_u256 = get_address_u256

    # These modules imported the helpers by value at import time.
    loader.import_calldata = get_calldata
    loader.import_address = get_address
    loader.import_lazy = get_lazy

    wasi_mock.import_calldata = get_calldata

    vm_mod.import_address_u256 = get_address_u256

    install_windows_safe_injection()

    _installed = True
    return True


def install() -> None:
    """Wrap the runner so the shim is applied right after the SDK is located."""
    os.environ.setdefault("GENVM_VERSION", PINNED_GENVM_VERSION)

    import gltest.direct.sdk_loader as sdk_loader

    if getattr(sdk_loader.setup_sdk_paths, "_brier_court_shim", False):
        return

    original_setup = sdk_loader.setup_sdk_paths

    def setup_sdk_paths_and_shim(contract_path=None, version=None):
        added = original_setup(contract_path, version)
        _install()
        return added

    setup_sdk_paths_and_shim._brier_court_shim = True  # type: ignore[attr-defined]
    sdk_loader.setup_sdk_paths = setup_sdk_paths_and_shim


# --- v0.2 storage allocation ------------------------------------------------


def _allocate_contract(contract_cls, vm, *args, **kwargs):
    """Mirror of ``genlayer.py.storage.inmem_allocate`` bound to the test VM.

    The runner's own version imports ``genlayer.storage`` (v0.3). Both of its
    fallbacks miss on v0.2, and the last-ditch ``contract_cls(*args)`` produces
    an instance with no ``__type_desc__``, so every storage write explodes.
    """
    from genlayer.py.storage import ROOT_SLOT_ID, Root
    from genlayer.py.storage._internal.generate import (
        ORIGINAL_INIT_ATTR,
        Lit,
        _storage_build,
    )

    Root.MANAGER = vm._storage

    type_desc = _storage_build(contract_cls, {})
    if isinstance(type_desc, Lit):
        raise TypeError(f"{contract_cls.__name__} has no storage to allocate")

    instance = type_desc.get(vm._storage.get_store_slot(ROOT_SLOT_ID), 0)

    init = getattr(type_desc, "cls", None)
    if init is None:
        init = getattr(contract_cls, "__init__", None)
    else:
        init = getattr(init, "__init__", None)
    if init is not None:
        if hasattr(init, ORIGINAL_INIT_ATTR):
            init = getattr(init, ORIGINAL_INIT_ATTR)
        init(instance, *args, **kwargs)

    return instance


# --- wiring ------------------------------------------------------------------


def install_windows_safe_injection() -> None:
    """Swap in the deferred-unlink injection and matching cleanup."""
    global _original_cleanup

    import gltest.direct.loader as loader
    import gltest.direct.vm as vm_mod

    loader._inject_message_to_fd0 = _inject_message_to_fd0
    loader._allocate_contract = _allocate_contract

    if _original_cleanup is None:
        _original_cleanup = vm_mod.VMContext._cleanup_after_deactivate
        vm_mod.VMContext._cleanup_after_deactivate = _cleanup_after_deactivate


class _LiveMessage:
    """``gl.message`` bound to the live test VM.

    ``genlayer.gl`` builds ``message`` as a plain namedtuple when it is first
    imported. Under GenVM that is correct - every transaction gets a fresh VM
    and re-imports the contract - but the direct runner reuses one process for
    the whole test, so the snapshot taken at deploy time would pin
    ``sender_address`` forever and ``vm.sender`` would stop mattering.
    """

    __slots__ = ()

    def __getattr__(self, name):
        from gltest.direct import wasi_mock

        vm = wasi_mock.get_vm()
        if vm is None:
            raise AttributeError(name)

        found = _v02_modules()
        if found is None:
            raise AttributeError(name)
        types, _ = found

        if name == "sender_address":
            return _coerce_address(vm.sender, types.Address)
        if name == "origin_address":
            return _coerce_address(vm.origin, types.Address)
        if name == "contract_address":
            return _coerce_address(vm._contract_address, types.Address)
        if name == "value":
            return types.u256(vm._value)
        if name == "chain_id":
            return types.u256(vm._chain_id)
        raise AttributeError(name)


def _coerce_address(value, address_cls):
    if value is None or isinstance(value, address_cls):
        return value
    if isinstance(value, bytes):
        return address_cls(value)
    if hasattr(value, "as_bytes"):
        return address_cls(value.as_bytes)
    return value


def _inject_message_to_fd0(vm) -> None:  # pragma: no cover - plumbing
    """Same contract as the runner's helper, minus the POSIX-only unlink.

    The runner deletes the temp file while fd 0 still references it. That is
    legal on POSIX and a hard ``PermissionError`` on Windows, which is why the
    upstream helper cannot run there. We keep the file alive until the VM is
    torn down instead.
    """
    import tempfile

    found = _v02_modules()
    if found is None:
        return
    types, calldata = found
    Address = types.Address

    def coerce(value):
        if value is None or isinstance(value, Address):
            return value
        if isinstance(value, bytes):
            return Address(value)
        if hasattr(value, "as_bytes"):
            return Address(value.as_bytes)
        return value

    encoded = calldata.encode(
        {
            "contract_address": coerce(vm._contract_address),
            "sender_address": coerce(vm.sender),
            "origin_address": coerce(vm.origin),
            "stack": [],
            "value": vm._value,
            "datetime": vm._datetime,
            "is_init": False,
            "chain_id": vm._chain_id,
            "entry_kind": 0,
            "entry_data": b"",
            "entry_stage_data": None,
        }
    )

    fd, path = tempfile.mkstemp()
    os.write(fd, encoded)
    os.lseek(fd, 0, os.SEEK_SET)
    vm._original_stdin_fd = os.dup(0)
    os.dup2(fd, 0)
    os.close(fd)
    vm._injected_message_path = path

    # The message is now readable, so it is safe to import the SDK module that
    # reads it - and immediately replace its frozen snapshot with a live view.
    try:
        import genlayer.gl as gl_mod
        import genlayer.gl.vm as gl_vm

        gl_mod.message = _LiveMessage()

        # The runner patches ``genlayer.vm.run_nondet`` to skip cloudpickle, but
        # v0.2 keeps it at ``genlayer.gl.vm``. Aliasing the module makes the
        # runner's own patch land, which is what removes the cloudpickle
        # dependency and records the validator for ``vm.run_validator()``.
        sys.modules.setdefault("genlayer.vm", gl_vm)
    except Exception:
        pass


def _cleanup_after_deactivate(self) -> None:
    _original_cleanup(self)
    path = getattr(self, "_injected_message_path", None)
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
        self._injected_message_path = None


_original_cleanup = None
