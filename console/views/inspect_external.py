"""The World tab's External values section (#248; D1, D3, D5, D15, D19).

``inspect:external-values`` (``/stories/<id>/inspect/world/external-values/``) renders through
#135's :func:`console.views.inspect.inspect_page` with ``Tab.WORLD``. It is never greyed (D1: the
Engine serves the read under every configuration). Each row is one ``external_read`` event: the
provider and value read, the State's scope, the value (D5's external-value exception: ``null`` is
"unknown"), when and by which turn it was read, its source, and the canon facts citing it
(FR-EXT-4), each linked to its Canon lineage. Every value is omniscient: there is no viewpoint
picker.

Reads per render, in order:

1. the shell's effective-config and story reads;
2. one ``GET /stories/{id}/inspect/external-facts`` (``viewpoint=omniscient``, ``limit=100``;
   ``as_of`` and ``after_id`` when given);
3. only when a parsed read on the page has a citing fact, convention 2's one predicate-registry
   read (``predicate_names``; ``{}`` on failure: short ids).

An invalid As of makes no call; an ``after_id`` failing the id pattern is dropped
(:data:`console.paging.CURSOR_DROPPED`) and never sent. Errors stay inside the section
(convention 10): a 422 ``query.as_of`` beside As of, ``query.after_id`` above the filters with
"First page", any other above the filters; ``viewpoint_unresolved`` and 403
``omniscient_required`` read :data:`console.forms.inspect.OMNISCIENT_TEXT`; 404 the not-found
line; 503 ``erasure_pending`` escapes to #87's held wrapper; anything else (``module_off``
included: the section is never greyed) is #43's panel below the filters. Unreadable entries
(D15) render one "couldn't be read" row each, the raw JSON in More.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_safe

from console.beliefs import MoreItem
from console.canon import cast_names, lineage_url
from console.effects import provider_url
from console.engine.client import EngineClientError, EngineError
from console.engine.models import ExternalFact, ExternalRead, Proposition, rejected_entries
from console.engine_errors import EMPTY, log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, VIEWPOINT_UNRESOLVED
from console.forms.world import ExternalValuesForm
from console.held import is_held
from console.ids import is_ulid
from console.inspect import Tab, history_turn_url, short_id
from console.ledger import MATCHING, ON_PAGE
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.positions import valid_minutes_text
from console.predicates import predicate_names
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page
from console.wording import character_label, humanised
from console.world import inferred_type, state_value_text

TEMPLATE = "console/inspect/external_values.html"
PAGE_LIMIT = 100  # convention 6 (the route allows 500)
CURSOR = "after_id"
INTRO = "Every value is shown as the Engine recorded it (omniscient)"
HEADER = "Values read from providers"
NOW = "now"
NONE = (
    "No provider values have been read for this story yet — a turn reads them when an "
    "externally owned State needs them"
)
NONE_THROUGH = "No provider values were read through turn {turn}"
EMPTY_PAGE = "No reads on this page — the list may have changed since the last page."
NOT_FOUND = "Not found (it may have been deleted or truncated)"
UNREADABLE = "This read couldn't be read"
UNKNOWN = "unknown"
STORY_SCOPE = "Story"
BINDINGS = "this story's Providers page"


def external_value_text(value: Any) -> tuple[str, bool]:
    """D5's external-value exception: ``(text, unknown)``. ``None`` → ("unknown", ``True``)
    (the provider gave no value); otherwise :func:`console.world.state_value_text` with the type
    inferred from the JSON value (bool before int; arrays and objects compact JSON), no unit. An
    empty string reads ``""``."""
    if value is None:
        return UNKNOWN, True
    if value == "":
        return '""', False
    return state_value_text(value, inferred_type(value)), False


def header_text(as_of: int | None, *, valid: bool, revision: int | None) -> str:
    """ "Values read from providers · now" (or "· through turn N"), then "· revision R" when the
    read answered; no "now"/"through" part for an invalid As of."""
    parts = [HEADER]
    if valid:
        parts.append(NOW if as_of is None else f"through turn {as_of}")
    if revision is not None:
        parts.append(f"revision {revision}")
    return " · ".join(parts)


def count_text(n: int, *, partial: bool, filtered: bool) -> str:
    """C11: "12 reads" / "1 read", then "on this page" and/or "matching filters"."""
    noun = "read" if n == 1 else "reads"
    qualifier = ", ".join(text for text, on in ((ON_PAGE, partial), (MATCHING, filtered)) if on)
    return f"{n:,} {noun} {qualifier}" if qualifier else f"{n:,} {noun}"


def empty_text(*, paged: bool, as_of: int | None) -> str:
    """The empty page's line: a cursor page's, then an As of one's, then the plain one."""
    if paged:
        return EMPTY_PAGE
    if as_of is not None:
        return NONE_THROUGH.format(turn=as_of)
    return NONE


@dataclass(frozen=True)
class Scope:
    """The Scope cell: ``text``, in ``<code title="<code_title>">`` when the id isn't in the
    cast (``code_title`` the full id), else plain."""

    text: str
    code_title: str = ""


@dataclass(frozen=True)
class CitedFact:
    """One citing fact version: its lineage ``url``, the parsed ``proposition`` (``None`` when
    it doesn't parse: ``raw_proposition`` holds its JSON for More), ``closed_at`` (``tx_to``,
    ``None`` while current) and its More entries."""

    fact_id: str
    url: str
    proposition: Proposition | None
    raw_proposition: str
    closed_at: str | None
    more: tuple[MoreItem, ...]


@dataclass(frozen=True)
class ExternalReadRow:
    """``_external_read_row.html``'s context for one read (blank provider, value name or source:
    "—", never linked or in ``<code>``)."""

    event_id: str
    provider: str
    provider_url: str | None
    value_name: str
    scope: Scope
    value_text: str
    unknown: bool
    source: str
    source_text: str
    read_at: str | None
    turn_text: str
    turn_url: str | None
    facts: tuple[CitedFact, ...]
    state_id: str
    raw_value: str


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _enum(term: str, value: str) -> MoreItem:
    """C3: humanised, the raw value in ``title`` ("—" for a blank one)."""
    return MoreItem(term, humanised(value), title=value) if value else MoreItem(term, EMPTY)


def _scope(scope_key: str | None, names: Mapping[str, str]) -> Scope:
    if not scope_key:
        return Scope(STORY_SCOPE)
    if scope_key in names:
        return Scope(character_label(scope_key, names))
    return Scope(short_id(scope_key), scope_key)


def _turn(story_id: str, read: ExternalRead) -> tuple[str, str | None]:
    """C1: "turn N" (" · vV" for a variant ≠ 0) linked to the history; "—" for no turn."""
    if read.turn is None:
        return EMPTY, None
    variant = read.variant or 0
    text = f"turn {read.turn}" + (f" · v{variant}" if variant else "")
    return text, history_turn_url(story_id, read.turn, variant)


def _cited(story_id: str, fact: ExternalFact) -> CitedFact:
    view = fact.proposition_view
    subject = view.subject_id if view is not None else None
    predicate = view.predicate_id if view is not None else None
    more = (
        MoreItem("Fact id", fact.fact_id, "code"),
        _enum("Scope", fact.scope),
        _enum("Provenance", fact.provenance),
        _enum("Authority", fact.authority),
        MoreItem("Valid time", valid_minutes_text(fact.valid_from, fact.valid_to)),
        MoreItem("Recorded", fact.tx_from, "time"),
    )
    return CitedFact(
        fact_id=fact.fact_id,
        url=lineage_url(story_id, fact.fact_id, subject, predicate),
        proposition=view,
        raw_proposition="" if view is not None else _pretty(fact.proposition),
        closed_at=fact.tx_to,
        more=more,
    )


def read_rows(
    story_id: str, items: Sequence[ExternalRead], names: Mapping[str, str]
) -> list[ExternalReadRow]:
    """The rows in the Engine's order (by event id)."""
    rows = []
    for read in items:
        value_text, unknown = external_value_text(read.value)
        turn_text, turn_url = _turn(story_id, read)
        rows.append(
            ExternalReadRow(
                event_id=read.event_id,
                provider=read.provider,
                provider_url=provider_url(read.provider),
                value_name=read.value_name,
                scope=_scope(read.scope_key, names),
                value_text=value_text,
                unknown=unknown,
                source=read.source,
                source_text=humanised(read.source) if read.source else EMPTY,
                read_at=read.read_at,
                turn_text=turn_text,
                turn_url=turn_url,
                facts=tuple(_cited(story_id, fact) for fact in read.facts),
                state_id=read.state_id or "",
                raw_value=_pretty(read.value),
            )
        )
    return rows


def _bindings_url(story_id: str) -> str | None:
    """``stories:providers`` (#247) when registered, else ``None`` (no Bindings line)."""
    try:
        return reverse("stories:providers", args=[story_id])
    except NoReverseMatch:
        return None


def _read_error(
    ctx: InspectContext, exc: EngineClientError, form: ExternalValuesForm
) -> dict[str, Any]:
    """Convention 10's in-section error (held re-raised to the middleware)."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
        return {"omniscient_required": OMNISCIENT_TEXT}
    if isinstance(exc, EngineError) and exc.http_status == 422:
        return {"after_refused": form.apply_engine_error(exc)}
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return {"not_found": NOT_FOUND}
    return {"external_error": panel_for(exc)}


def _section(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    form = ExternalValuesForm(request.GET)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    params = request.GET.copy()
    params.pop(CURSOR, None)
    if cursor:
        params[CURSOR] = cursor
    valid = form.is_valid()
    as_of = form.cleaned_data.get("as_of") if valid else None
    context: dict[str, Any] = {
        "form": form,
        "intro": INTRO,
        "cast_names": names,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "filter_url": reverse("inspect:external-values", args=[ctx.story_id]),
        "bindings_url": _bindings_url(ctx.story_id),
        "bindings_text": BINDINGS,
        "header": header_text(as_of, valid=valid, revision=None),
        "unreadable": UNREADABLE,
        **paging(params, cursor, None, param=CURSOR),
    }
    if not valid:
        return context
    try:
        body = ctx.client.inspect_external_facts(
            ctx.story_id, limit=PAGE_LIMIT, after_id=cursor, **form.query()
        )
    except EngineClientError as exc:
        context.update(_read_error(ctx, exc, form))
        return context

    raw = body.unparsed.get("items") if body.unparsed else None
    rejected = rejected_entries(raw, ExternalRead.from_json) if raw is not None else []
    if any(read.facts for read in body.items):
        context["predicate_names"] = predicate_names(request, ctx.client, ctx.story_id)
    else:
        context["predicate_names"] = {}
    context.update(
        external=body,
        header=header_text(as_of, valid=True, revision=body.revision),
        rows=read_rows(ctx.story_id, body.items, names),
        rejected=[_pretty(entry) for entry in rejected],
        count=count_text(
            len(body.items),
            partial=bool(cursor or body.next_after_id),
            filtered=form.any_filter,
        ),
        empty=None if body.items or rejected else empty_text(paged=cursor is not None, as_of=as_of),
        **paging(params, cursor, body.next_after_id, param=CURSOR),
    )
    return context


@require_safe
def external_values(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:external-values``, the values external providers answered for the story
    (#248)."""
    return inspect_page(request, story_id, Tab.WORLD, TEMPLATE, _section)
