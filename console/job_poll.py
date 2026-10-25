"""Polling an Engine job from a page (#64 M3 convention 10; first used by #77's offboarding, and
meant for #80's rating checks and #86's erasures).

A job page renders ``console/_job_poll.html`` around the job's own partial. While the job is
not terminal the wrapper carries ``hx-get=<poll_url> hx-trigger="every 2s"`` and swaps itself
(``outerHTML``); the terminal render has no ``hx-trigger``, so polling stops there. There is no
cutoff, and a "Refresh" link reloads the page without JavaScript.

A failed poll is classified by :func:`poll_failure`: no answer (unreachable, timeout) or a 503
keeps polling with :data:`RETRYING_TEXT`; a 404 is the job's own "not found" text; anything
else stops polling and shows the error panel.

The first terminal poll settles the job's ``queued`` operation-log row (#44) with
:func:`settle_job` (idempotent, so every later poll of a finished job changes nothing).
"""

from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest

from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine_access import complete_job
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.models import EngineOperation

POLL_TRIGGER = "every 2s"
POLL_TEMPLATE = "console/_job_poll.html"
RETRYING_TEXT = "Engine unavailable — retrying"
RETRY_STATUSES = frozenset({503})


@dataclass(frozen=True)
class PollFailure:
    """What a failed job read means for the page: keep polling (``retry``), the job is gone
    (``not_found``), or stop and show ``panel``."""

    retry: bool
    not_found: bool
    panel: ErrorPanel | None


def poll_failure(request: HttpRequest, exc: EngineClientError) -> PollFailure:
    """Classify (and log) a job read that failed."""
    log_engine_error(request, exc)
    if isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.http_status in RETRY_STATUSES
    ):
        return PollFailure(retry=True, not_found=False, panel=None)
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return PollFailure(retry=False, not_found=True, panel=None)
    return PollFailure(retry=False, not_found=False, panel=panel_for(exc))


def poll_context(
    *,
    poll_url: str,
    refresh_url: str,
    body_template: str,
    polling: bool,
    retrying: bool = False,
) -> dict[str, Any]:
    """The context ``console/_job_poll.html`` takes (merge the job's own context into it)."""
    return {
        "poll_url": poll_url,
        "refresh_url": refresh_url,
        "body_template": body_template,
        "polling": polling,
        "poll_trigger": POLL_TRIGGER,
        "retrying": retrying,
        "retrying_text": RETRYING_TEXT,
    }


def settle_job(job_id: str, *, failed: bool, error_code: str = "") -> int:
    """Settle the job's ``queued`` #44 row on its terminal poll: ``ok``, or ``error`` with
    ``error_code`` (``failed`` jobs pass ``error.class``). The 202's stored response is kept
    (it holds what the start was confirmed over). Returns the rows settled (0 or 1)."""
    outcome = EngineOperation.Outcome.ERROR if failed else EngineOperation.Outcome.OK
    return complete_job(job_id, outcome, error_code if failed else "")
