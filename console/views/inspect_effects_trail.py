"""The Effects tab's Effects trail section (#255; D3, D10, D11, D13, D18; C11, C12).

``inspect:effects-trail`` (``/stories/<id>/inspect/effects/trail/``) renders through #135's
:func:`console.views.inspect.inspect_page` with ``Tab.EFFECTS``. D3's registry row gates it on
``states`` at ``on``: with the gate closed the shell's short-circuit renders the greyed inline
panel and this module's build is never called (no history read).

Reads per render, in order:

1. the shell's effective-config and story reads;
2. exactly one ``GET /stories/{id}/history?viewpoint=omniscient&limit=200[&before_turn=N]``.

No States read is made, so values carry no unit (D5). The trail lists the State changes each turn
on the page committed (``TurnSummary.state_effects``, #231), one ``<section class="trail-turn">``
per turn with at least one change (parsed or not), ascending within the page as the Engine
returns it; pages go newest first ("Older →" with ``before_turn=<next_before_turn>``, "Newest
page" dropping it, C12). Only the active chain at head is read (no ``variant``/``as_of``): the
cursor is the page's only input. Inter-turn slot operations are not in the history read (D18), and
:data:`TRAIL_SCOPE_NOTE` says so whenever the section body renders.

A ``before_turn`` that isn't ASCII digits is dropped and never sent (first page, with
``CURSOR_DROPPED``). Errors stay in the section (convention 10): a 422 on a cursor
:data:`CURSOR_REFUSED` with "Newest page" (no second read); 403 ``omniscient_required``
``OMNISCIENT_TEXT``; 404 :data:`NOT_FOUND`; 409 ``module_off`` and 503 ``erasure_pending``
re-raise to the shell (greyed panel, #87's held page); anything else #43's panel.
"""

import json
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.safestring import SafeString
from django.views.decorators.http import require_safe

from console.cast import character_labels
from console.effects import INCOMPLETE_TEXT, INCOMPLETE_TITLE, outbox_list_notice_html
from console.engine.client import EngineClientError, EngineError
from console.engine.models import TurnSummary
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT
from console.held import is_held
from console.history import unread_line
from console.inspect import Tab, history_turn_url, is_module_off
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.turn_world import StateChangeRow, state_change_rows
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page

TEMPLATE = "console/inspect/effects_trail.html"
TRAIL_LIMIT = 200  # the Engine's default page; it counts items, not turns
CURSOR = "before_turn"
CAPTION = "State changes"

TRAIL_SCOPE_NOTE = (
    "Only changes turns made are listed — a State set, clock advance or late settlement between "
    "turns isn't in the history read; a State's “since” position or the write's result names it"
)
EMPTY_TEXT = "No turn has changed a State yet"
EMPTY_TRY_OLDER = "No turn on this page changed a State — try Older"
EMPTY_CURSOR_PAGE = (
    "No State changes on this page — the timeline may have changed since the last page"
)
CURSOR_REFUSED = "That page link was refused — showing the first page"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


@dataclass(frozen=True)
class TrailTurn:
    """One turn's block: its heading, link and anchor, D13's badge, #245's rows and the raw
    entries that didn't parse (each as indented JSON)."""

    turn: int
    variant: int | None
    url: str
    incomplete: bool
    rows: tuple[StateChangeRow, ...]
    unparsed: tuple[str, ...]

    @property
    def heading(self) -> str:
        return f"Turn {self.turn} · variant {self.variant or 0}"

    @property
    def anchor(self) -> str:
        return f"trail-turn-{self.turn}-{self.variant or 0}"

    @property
    def unread(self) -> str:
        return unread_line(len(self.unparsed))

    @property
    def count(self) -> int:
        return len(self.rows) + len(self.unparsed)


def read_cursor(value: str | None) -> tuple[int | None, bool]:
    """(the ``before_turn`` to send, whether a typed value was dropped). Only ASCII digits are
    sent, as ``int(value)`` (leading zeros fine; the Engine's 422 handles range); an empty value
    is no cursor."""
    if not value:
        return None, False
    if value.isascii() and value.isdigit():
        return int(value), False
    return None, True


def count_text(changes: int, turns: int, *, on_page: bool) -> str:
    """C11's count line: "N State changes on M turns", " on this page" when paged."""
    text = (
        f"{changes} State change{'' if changes == 1 else 's'} "
        f"on {turns} turn{'' if turns == 1 else 's'}"
    )
    return f"{text} on this page" if on_page else text


def trail_turn(summary: TurnSummary, story_id: str, names: dict[str, str]) -> TrailTurn | None:
    """The block for one turn summary, or ``None`` when it changed no State."""
    raw = summary.unparsed.get("state_effects", summary.state_effects)
    rows, rejected = state_change_rows(raw, story_id, names)
    if not rows and not rejected:
        return None
    return TrailTurn(
        turn=summary.turn,
        variant=summary.variant,
        url=history_turn_url(story_id, summary.turn, summary.variant),
        incomplete=summary.incomplete,
        rows=rows,
        unparsed=tuple(json.dumps(e, indent=2, ensure_ascii=False, default=str) for e in rejected),
    )


def _section(ctx: InspectContext) -> dict[str, Any]:
    before_turn, dropped = read_cursor(ctx.request.GET.get(CURSOR))
    context: dict[str, Any] = {
        "scope_note": TRAIL_SCOPE_NOTE,
        "cursor_dropped": CURSOR_DROPPED if dropped else "",
        "incomplete_text": INCOMPLETE_TEXT,
        "incomplete_title": INCOMPLETE_TITLE,
        "caption": CAPTION,
    }
    try:
        history = ctx.client.get_history(
            ctx.story_id, viewpoint="omniscient", limit=TRAIL_LIMIT, before_turn=before_turn
        )
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        status = exc.http_status if isinstance(exc, EngineError) else None
        if status == 422 and before_turn is not None:
            context["cursor_refused"] = CURSOR_REFUSED
            context["first_query"] = paging(ctx.request.GET, None, None, param=CURSOR)[
                "first_query"
            ]
        elif isinstance(exc, EngineError) and exc.code == OMNISCIENT_REQUIRED:
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif status == 404:
            context["not_found"] = NOT_FOUND
        else:
            context["trail_error"] = panel_for(exc)
        return context

    names = character_labels(ctx.story.cast) if ctx.story is not None else {}
    blocks = [
        block
        for block in (trail_turn(s, ctx.story_id, names) for s in history.turns)
        if block is not None
    ]
    next_cursor = history.next_before_turn
    links = paging(
        ctx.request.GET,
        str(before_turn) if before_turn is not None else None,
        str(next_cursor) if next_cursor is not None else None,
        param=CURSOR,
    )
    paged = before_turn is not None
    dispatched = any(row.outbox_id for block in blocks for row in block.rows)
    empty = ""
    if not blocks:
        empty = EMPTY_CURSOR_PAGE if paged else EMPTY_TRY_OLDER if next_cursor else EMPTY_TEXT
    outbox_notice: SafeString | str = (
        outbox_list_notice_html(story_id=ctx.story_id) if dispatched else ""
    )
    context.update(
        trail=history,
        blocks=blocks,
        count=(
            count_text(
                sum(b.count for b in blocks),
                len(blocks),
                on_page=paged or next_cursor is not None,
            )
            if blocks
            else ""
        ),
        outbox_notice=outbox_notice,
        cast_unavailable=CAST_UNAVAILABLE if ctx.story is None and blocks else "",
        empty=empty,
        **links,
    )
    return context


@require_safe
def effects_trail(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:effects-trail``, every State change the story's turns committed (#255)."""
    return inspect_page(request, story_id, Tab.EFFECTS, TEMPLATE, _section)
