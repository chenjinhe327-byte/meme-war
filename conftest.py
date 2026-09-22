"""Brier Court test bootstrap.

The GenLayer test suite (``genlayer-test``) is normally installed with pip. On
the machine this project was built on there is no working pip install path, so
the suite is used straight from its source tree instead. Resolution order:

1. ``GLTEST_SUITE_PATH`` environment variable
2. an importable ``gltest`` package (the normal, pip-installed case)
3. a local checkout kept in the shared workspace cache

The direct runner also caches the ~200 MB GenVM runner bundle under
``~/.cache``. ``CACHE_DIR`` is a module-level constant there, so we repoint it
into a directory we are allowed to write to before the first deploy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_FALLBACK_SUITES = [
    # Optional vendored copy, if you would rather not install the suite.
    Path(__file__).resolve().parent / ".vendor" / "genlayer-testing-suite",
]


def _suite_is_usable(path: Path) -> bool:
    return (path / "gltest" / "direct" / "pytest_plugin.py").is_file()


def _bootstrap_suite_path() -> None:
    env = os.environ.get("GLTEST_SUITE_PATH")
    if env and _suite_is_usable(Path(env)):
        sys.path.insert(0, env)
        return
    try:
        import gltest  # noqa: F401

        return
    except ImportError:
        pass
    for candidate in _FALLBACK_SUITES:
        if _suite_is_usable(candidate):
            sys.path.insert(0, str(candidate))
            return


_bootstrap_suite_path()

pytest_plugins = ("gltest.direct.pytest_plugin",)


def pytest_configure(config) -> None:  # noqa: ARG001
    try:
        import gltest.direct.sdk_loader as sdk_loader
    except ImportError:
        return

    target = os.environ.get("GLTEST_CACHE_DIR")
    if target:
        cache = Path(target)
    else:
        cache = Path(__file__).resolve().parent / ".cache" / "gltest-direct"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    sdk_loader.CACHE_DIR = cache

    # Make the runner speak the py-genlayer v0.2 layout that the network's
    # `py-genlayer:latest` resolves to. See tests/direct/_genvm_compat.py.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tests" / "direct"))
    import _genvm_compat

    _genvm_compat.install()
