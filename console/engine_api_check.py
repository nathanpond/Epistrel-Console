"""Does the running Engine speak the API this Console was built against?

Compares the live ``/openapi.json`` with the recorded copy (``console/engine/openapi.json``) using
the same ``diff()`` as ``manage.py engine_schema --check`` (#34) and the daily drift check (#40).
The verdict is informational: a mismatch never stops the Console working. It never raises.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from console import engine_probe
from console.engine import schema
from console.engine.client import EngineClient, EngineError, EngineUnreachable

log = logging.getLogger("console.engine")

TIMEOUT_S = 3.0
REPEAT_LOG_S = 60.0

Verdict = Literal["matches", "differs", "unknown"]

# A missing or corrupt recording is a shipped-file defect: it fails loudly at import.
RECORDED: dict[str, Any] = json.loads(schema.RECORDED_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ApiCheck:
    verdict: Verdict
    summary: str | None
    reason: str | None
    engine_version: str | None
    changes: tuple[str, ...] = ()

    def as_json(self) -> dict[str, str | None]:
        """The four keys /health carries."""
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "reason": self.reason,
            "engine_version": self.engine_version,
        }


def _unknown(reason: str) -> ApiCheck:
    return ApiCheck("unknown", None, reason, None)


def _compare(client: EngineClient) -> ApiCheck:
    try:
        doc = client.openapi(timeout=TIMEOUT_S)
    except EngineUnreachable as exc:
        return _unknown(exc.reason)
    except EngineError as exc:
        return _unknown("bad response" if exc.code == "bad_response" else f"HTTP {exc.http_status}")
    if not isinstance(doc.get("paths"), dict):
        return _unknown("bad response")
    info = doc.get("info")
    version = info.get("version") if isinstance(info, dict) else None
    engine_version = version if isinstance(version, str) else None
    difference = schema.diff(RECORDED, doc)
    if not difference:
        return ApiCheck("matches", None, None, engine_version)
    return ApiCheck("differs", difference.summary(), None, engine_version, difference.changes())


def check_engine_api(client: EngineClient | None) -> ApiCheck:
    """One ``GET /openapi.json`` (no retries), compared with the recording."""
    if client is None:
        result = _unknown("not configured")
    else:
        try:
            result = _compare(client)
        except AssertionError:
            raise  # the fake Engine's signals reach the test
        except Exception:
            log.warning("unexpected error checking the Engine's API", exc_info=True)
            result = _unknown("transport error")
    _log(client.base_url if client else None, result)
    return result


_lock = threading.Lock()
_last_verdict: Verdict | None = None
_last_logged = 0.0


def _log(url: str | None, result: ApiCheck) -> None:
    """One line per verdict change, plus at most one repeat per minute while it is not a match.

    ``unknown`` is not logged while the probe already reports the Engine unreachable: that line
    has said it.
    """
    global _last_verdict, _last_logged
    with _lock:
        now = time.monotonic()
        changed = result.verdict != _last_verdict
        if result.verdict == "matches":
            if changed and _last_verdict is not None:
                log.info("Engine at %s matches the recorded API again", url)
        elif result.verdict == "unknown" and engine_probe.last_state() == "unreachable":
            pass
        elif changed or now - _last_logged >= REPEAT_LOG_S:
            detail = result.summary if result.verdict == "differs" else result.reason
            log.warning("Engine API at %s %s: %s", url, result.verdict, detail)
        else:
            _last_verdict = result.verdict
            return
        _last_verdict = result.verdict
        _last_logged = now
