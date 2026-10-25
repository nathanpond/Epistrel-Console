"""Console-side fan-out reads (#64, M3 convention 12): N lookups at once, one shared deadline.

``fan_out(read, keys)`` runs ``read(key)`` for each distinct key on
:data:`~console.engine.timeouts.LOOKUP_WORKERS` threads and waits at most
:data:`~console.engine.timeouts.LOOKUP_DEADLINE_S` in all. Each key maps to its value, or to the
:class:`~console.engine.client.EngineClientError` its read raised; a read still running at the
deadline maps to ``EngineUnreachable("timeout")`` and is left to finish on its own. Pages show
which reads failed. Reads only: a recorded call would write the operation log from a worker
thread.
"""

import time
from collections.abc import Callable, Hashable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from console.engine.client import EngineClientError, EngineUnreachable
from console.engine.timeouts import LOOKUP_DEADLINE_S, LOOKUP_WORKERS


def fan_out[K: Hashable, V](
    read: Callable[[K], V],
    keys: Iterable[K],
    *,
    workers: int = LOOKUP_WORKERS,
    deadline_s: float = LOOKUP_DEADLINE_S,
) -> dict[K, V | EngineClientError]:
    distinct = list(dict.fromkeys(keys))
    if not distinct:
        return {}
    results: dict[K, V | EngineClientError] = {}
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(distinct))))
    try:
        futures: dict[K, Future[V]] = {key: pool.submit(read, key) for key in distinct}
        deadline = time.monotonic() + deadline_s
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                results[key] = EngineUnreachable("timeout")
            except EngineClientError as exc:
                results[key] = exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return results
