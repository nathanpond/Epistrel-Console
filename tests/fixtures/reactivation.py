"""Reactivation re-check outcomes (#81): ``{operation, result}`` as a ``reactivated`` check's
``outcome`` carries it. The Engine dumps its core result (no ``idempotency_key``, no
``replayed``), so each result is #53's captured REST answer without its key. Plus a mismatched
outcome (an operation outside the vocabulary) and an unparseable one."""

from typing import Any

from tests.fixtures import engine_fixture


def _result(name: str) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture(name)
    body.pop("idempotency_key", None)
    return body


def outcome(op: str, fixture: str | None = None) -> dict[str, Any]:
    """The outcome of ``op`` (``untruncate``, ``swap`` or ``fork``), its result from #53's
    ``fixture`` (default: the op's own)."""
    return {"operation": op, "result": _result(fixture or op)}


UNTRUNCATED = outcome("untruncate")  # active_chain null: a plain reactivation
UNTRUNCATED_SWAPPED = outcome("untruncate", "untruncate_swapped")  # became a swap to 4:1
SWAPPED = outcome("swap")  # chain 4:2
FORKED = outcome("fork")
MISMATCHED = {"operation": "rebuild", "result": _result("swap")}
UNPARSEABLE = {"operation": "swap", "result": {"turn": "four"}}
