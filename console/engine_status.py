"""The status page's two Engine questions, asked at once: is it healthy, does its API match?

Each makes exactly one request (``/health``, ``/openapi.json``) with no retries. Run side by side,
a page waits for the slower of the two (about 3 s at worst), not their sum, and never more than
the shared deadline.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from console.engine.client import EngineClient
from console.engine_api_check import ApiCheck, check_engine_api
from console.engine_probe import ProbeResult, probe

DEADLINE_S = 4.0


def probe_engine(client: EngineClient | None) -> tuple[ProbeResult, ApiCheck]:
    """``(health probe, API check)`` for ``client`` (``None``: no Engine is configured)."""
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="engine-status")
    try:
        health = pool.submit(probe, client)
        api = pool.submit(check_engine_api, client)
        deadline = time.monotonic() + DEADLINE_S
        try:
            probed = health.result(timeout=max(0.0, deadline - time.monotonic()))
        except FutureTimeout:
            probed = ProbeResult("unreachable", False, None, None, None, "timeout", None)
        try:
            checked = api.result(timeout=max(0.0, deadline - time.monotonic()))
        except FutureTimeout:
            checked = ApiCheck("unknown", None, "timeout", None)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return probed, checked
