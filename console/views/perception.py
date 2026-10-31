"""A turn's perception trace (#195): ``/stories/<id>/turns/<turn>/perception/``.

One plain GET page (no HTMX), read-only. Its reads, in order:

1. ``GET /stories/{id}`` for the cast and the title; a failure is #43's error page (the
   middleware; 503 ``erasure_pending`` is #87's held wrapper);
2. turn 0 is the story's setup: a notice, and no other read;
3. ``GET /stories/{id}/effective-config`` for the gate (C6): ``m5_gate("perception", effective,
   level="co_location")``. A 503 ``erasure_pending`` is #87's held wrapper; any other failure
   greys the panel with ``GATE_UNREAD``. A greyed page makes no history read;
4. only when the gate passes: one ``GET /stories/{id}/history`` per cast member (cast order, at
   most :data:`console.perception.READS_MAX`) at ``viewpoint=character:<id>``,
   ``player_view=story``, ``before_turn=turn+1``, ``limit=1`` (the Engine takes a page's first
   turn whole, so that is exactly the turn) and the history chain ``variant`` when given. They run
   on a pool of :data:`TRACE_WORKERS` local to the request under one :data:`TRACE_DEADLINE_S`
   deadline (#147's ``turn_traces`` pattern); a read's own failure (its 503 ``erasure_pending``
   and 409 ``unknown_chain`` included) is one failed column.

The grid itself is :func:`console.perception.grid`. The page says it is reconstructed (Engine
request nathanpond/Epistrel#607), never the Engine's recorded perception.
"""

from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from console import cast as cast_helpers
from console.engine.client import EngineClient, EngineClientError
from console.engine.models import History, Story
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import is_held
from console.inspect import (
    PERCEPTION,
    UNREAD,
    Unavailable,
    history_turn_url,
    m5_gate,
    short_id,
)
from console.perception import (
    ALL_TIMED_OUT,
    FOOTNOTE,
    NO_CAST,
    RACE,
    READS_MAX,
    SETUP_NOTICE,
    STORY_VIEW,
    cap_note,
    caveat,
    grid,
    no_beats,
)
from console.views.history import TIMEOUT, TRACE_DEADLINE_S, TRACE_WORKERS, read_failure

TEMPLATE = "console/perception/turn.html"
LEVEL = "co_location"  # the grid means something from co-location up (envelopes need full)
ASCII_DIGITS = frozenset("0123456789")


def _is_variant(raw: str) -> bool:
    """``<turn>:<variant>``: two non-empty runs of ASCII digits, the whole string (#223). No
    regex: ``\\d`` takes Unicode digits, ``$`` a trailing newline, and CodeQL flags a
    repeated digit class on user input as a polynomial ReDoS."""
    turn, colon, variant = raw.partition(":")
    return bool(colon and turn and variant) and ASCII_DIGITS.issuperset(turn + variant)


def _read(
    client: EngineClient, story_id: str, character_id: str, turn: int, variant: str | None
) -> History:
    return client.get_history(
        story_id,
        viewpoint=cast_helpers.viewpoint_for(character_id),
        player_view=STORY_VIEW,
        before_turn=turn + 1,
        limit=1,
        variant=variant,
    )


def character_views(
    client: EngineClient,
    story_id: str,
    character_ids: list[str],
    turn: int,
    variant: str | None,
    *,
    request: HttpRequest | None = None,
) -> tuple[dict[str, History | str], dict[str, EngineClientError]]:
    """Each character's story-view read of ``turn`` (character id → body, or the failure
    reason: "timeout", "HTTP …", "unreachable", "error"; in ``character_ids`` order), and the
    errors behind the failed ones. Copied from ``turn_traces``: a pool local to this call, one
    deadline for all reads, errors logged after the wait."""
    found: dict[str, History | str] = {}
    errors: dict[str, EngineClientError] = {}
    if not character_ids:
        return found, errors
    executor = ThreadPoolExecutor(max_workers=TRACE_WORKERS)
    try:
        futures: dict[str, Future[History]] = {
            cid: executor.submit(_read, client, story_id, cid, turn, variant)
            for cid in character_ids
        }
        wait(futures.values(), timeout=TRACE_DEADLINE_S)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    for cid, future in futures.items():
        if not future.done() or future.cancelled():
            found[cid] = TIMEOUT
            continue
        exc = future.exception()
        if isinstance(exc, EngineClientError):
            if request is not None:
                log_engine_error(request, exc)
            found[cid] = read_failure(exc)
            errors[cid] = exc
        elif exc is not None:
            raise exc
        else:
            found[cid] = future.result()
    return found, errors


def _gate(request: HttpRequest, client: EngineClient, story_id: str) -> Unavailable | None:
    """``None`` when the story's perception serves the grid; else why not. A held story's
    503 escapes to #87's wrapper (``HeldStoryMiddleware``)."""
    try:
        config = client.get_effective_config(story_id)
    except EngineClientError as exc:
        if is_held(exc):
            raise
        log_engine_error(request, exc)
        return UNREAD
    return m5_gate(PERCEPTION, config.effective, level=LEVEL)


def _back_url(story_id: str, turn: int, variant: str | None) -> str:
    url = history_turn_url(story_id, turn)
    if not variant:
        return url
    path, anchor = url.split("#", 1)
    return f"{path}&variant={variant}#{anchor}"


def _story_name(story: Story) -> str:
    return story.name or short_id(story.id)


@require_safe
def perception(request: HttpRequest, story_id: str, turn: int) -> HttpResponse:
    client = engine_client_for(request)
    story = client.get_story(story_id)
    raw_variant = request.GET.get("variant", "")
    variant = raw_variant if _is_variant(raw_variant) else None
    context: dict[str, Any] = {
        "story": story,
        "story_name": _story_name(story),
        "turn": turn,
        "back_url": _back_url(story.id, turn, variant),
        "config_url": reverse("stories:config", args=[story.id]),
    }
    if turn < 1:
        context["notice"] = SETUP_NOTICE
        return render(request, TEMPLATE, context)
    gate = _gate(request, client, story_id)
    if gate is not None:
        context["unavailable"] = gate
        return render(request, TEMPLATE, context)
    cast = story.cast
    if not cast:
        context["notice"] = NO_CAST
        return render(request, TEMPLATE, context)
    wanted = [c.id for c in cast[:READS_MAX]]
    reads, errors = character_views(client, story_id, wanted, turn, variant, request=request)
    found = grid(cast, reads, turn)
    context.update(
        caveat=caveat(len(wanted)),
        cap_note=cap_note(len(cast) - READS_MAX) if len(cast) > READS_MAX else "",
        failures=found.failures,
        footnote=FOOTNOTE,
    )
    if found.all_failed:
        first = next((errors[cid] for cid in wanted if cid in errors), None)
        if all(reads[cid] == TIMEOUT for cid in wanted):
            context["replaced"] = ALL_TIMED_OUT
        elif first is not None:
            context["engine_error"] = panel_for(first)
        return render(request, TEMPLATE, context)
    if found.race:
        context["replaced"] = RACE
        return render(request, TEMPLATE, context)
    if not found.rows:
        context["replaced"] = no_beats(turn)
        return render(request, TEMPLATE, context)
    context["grid"] = found
    return render(request, TEMPLATE, context)
