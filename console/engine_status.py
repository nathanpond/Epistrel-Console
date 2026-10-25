"""The status page's Engine questions, asked at once: is it healthy, does its API match, and does it
accept our service key?

Each makes exactly one request (``/health``, ``/openapi.json``, ``/stories``) with no retries. Run
side by side, a page waits for the slowest of them (about 3 s at worst), not their sum, and never
more than the shared deadline. A call still running at the deadline is reported as ``timeout`` and
left to finish on its own; its later log line (whatever its outcome) follows the one logged here.
``/health`` asks only the first two: the key check belongs to the status page alone (#43).
"""

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Literal

from console.engine.client import EngineClient, EngineError, EngineUnreachable
from console.engine_api_check import ApiCheck, check_engine_api
from console.engine_probe import ProbeResult, probe

log = logging.getLogger("console.engine")

DEADLINE_S = 4.0

KeyVerdict = Literal["accepted", "rejected", "not_checked", "unknown"]


@dataclass(frozen=True)
class KeyCheck:
    """Whether the Engine accepts the Console's service key, from one authenticated read."""

    verdict: KeyVerdict
    detail: str

    @property
    def line(self) -> str:
        return f"Service key: {self.detail}"


def check_service_key(client: EngineClient | None) -> KeyCheck:
    """One ``GET /stories`` (``EngineClient.list_stories``); never raises."""
    if client is None:
        return KeyCheck("not_checked", "not checked — Engine not configured")
    try:
        client.list_stories()
    except EngineUnreachable:
        return KeyCheck("not_checked", "not checked — Engine unreachable")
    except EngineError as exc:
        if exc.http_status == 401:
            return KeyCheck("rejected", "rejected (401)")
        if exc.code == "bad_response":
            return KeyCheck("unknown", "unknown — bad response")
        return KeyCheck("unknown", f"unknown — HTTP {exc.http_status} {exc.code}")
    except AssertionError:
        raise  # the fake Engine's signals reach the test
    except Exception:
        log.warning("unexpected error checking the service key", exc_info=True)
        return KeyCheck("unknown", "unknown — transport error")
    return KeyCheck("accepted", "accepted")


def _wait[T](
    future: Future[T], deadline: float, client: EngineClient | None, path: str, fallback: T
) -> T:
    try:
        return future.result(timeout=max(0.0, deadline - time.monotonic()))
    except FutureTimeout:
        _log_deadline(client, path)
        return fallback


def _run(
    client: EngineClient | None, with_key: bool
) -> tuple[ProbeResult, ApiCheck, KeyCheck | None]:
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="engine-status")
    try:
        health = pool.submit(probe, client)
        api = pool.submit(check_engine_api, client)
        key: Future[KeyCheck] | None = pool.submit(check_service_key, client) if with_key else None
        deadline = time.monotonic() + DEADLINE_S
        probed = _wait(
            health,
            deadline,
            client,
            "/health",
            ProbeResult("unreachable", False, None, None, None, "timeout", None),
        )
        checked = _wait(
            api, deadline, client, "/openapi.json", ApiCheck("unknown", None, "timeout", None)
        )
        keyed = (
            _wait(key, deadline, client, "/stories", KeyCheck("unknown", "unknown — timeout"))
            if key is not None
            else None
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return probed, checked, keyed


def probe_engine(client: EngineClient | None) -> tuple[ProbeResult, ApiCheck]:
    """``(health probe, API check)`` for ``client`` (``None``: no Engine is configured)."""
    probed, checked, _ = _run(client, with_key=False)
    return probed, checked


def status_reads(client: EngineClient | None) -> tuple[ProbeResult, ApiCheck, KeyCheck]:
    """The status page's three reads under one shared deadline."""
    probed, checked, keyed = _run(client, with_key=True)
    return probed, checked, keyed or KeyCheck("unknown", "unknown — not run")


def _log_deadline(client: EngineClient | None, path: str) -> None:
    url = client.base_url if client else None
    log.warning(
        "Engine at %s: no answer from %s within %.1f s; reported as timeout", url, path, DEADLINE_S
    )
