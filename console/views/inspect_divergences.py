"""The Knowledge tab's Divergences section (#192): the story's lies.

``inspect:divergences`` (``/stories/<id>/inspect/knowledge/divergences/``, the Knowledge tab
selected) renders through #135's :func:`console.views.inspect.inspect_page` with
``Tab.KNOWLEDGE`` (#189), so a greyed tab makes no divergences call. Each item is an utterance
whose speaker asserted a proposition that deviates from its own belief (FR-TURN-7, FR-ADMIN-5).
The GET filters are :class:`console.forms.inspect.DivergencesForm`'s (``speaker``, ``subject``,
``as_of``), each the Engine's query parameter; the cursor is ``after_id`` (convention 6 via
:func:`console.paging.paging`).

Reads per render, sequential: the shell's effective config and story (cast names), then one
``GET /stories/{id}/inspect/divergences`` (``viewpoint=omniscient``, ``limit=100``), then, only
when it answered at least one item, convention 2's one registry read (``predicate_names``, ``{}``
on failure: short ids) and one ``operation_links`` over the page's operation ids (Console rows,
no Engine call; a ``DatabaseError`` is logged and leaves every id as ``<code>``). No call for an
invalid filter. An ``after_id`` failing the id pattern is dropped
(:data:`console.paging.CURSOR_DROPPED`) and never sent.

Errors stay inside the section (convention 10): 422 ``unknown_character`` beside Speaker; any
other 422 beside the filter ``details.field`` names, else above the filters (``query.after_id``
with "First page"); ``viewpoint_unresolved`` and 403 ``omniscient_required`` read "This read
needs the omniscient viewpoint"; 404 is the not-found line; 409 ``module_off`` escapes to the
shell's greyed panel; 503 ``erasure_pending`` to #87's held wrapper; anything else #43's panel
below the filters.
"""

import dataclasses
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse, QueryDict
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_safe

from console.beliefs import Linked, MoreItem, story_minor_ids
from console.canon import cast_names
from console.cast import Character, character_labels
from console.engine.client import EngineClientError, EngineError
from console.engine.models import DivergenceItem, Proposition
from console.engine_errors import EMPTY, log_engine_error, panel_for
from console.forms.inspect import OMNISCIENT_TEXT, VIEWPOINT_UNRESOLVED, DivergencesForm
from console.held import is_held
from console.history import operation_links
from console.ids import is_ulid
from console.inspect import Tab, is_module_off
from console.ledger import MATCHING, ON_PAGE
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.positions import knowledge_position_text, knowledge_position_url, position_step
from console.predicates import predicate_names
from console.views.inspect import OMNISCIENT_REQUIRED, InspectContext, inspect_page
from console.wording import character_label

log = logging.getLogger(__name__)

DIVERGENCES_TEMPLATE = "console/inspect/divergences.html"
PAGE_LIMIT = 100  # convention 6 (the route allows 500)
CURSOR = "after_id"
INTRO = "Utterances whose speaker asserted something it doesn't itself believe."
NONE = "No divergences recorded"
NONE_FILTERED = "No divergences match these filters"
EMPTY_PAGE = "No divergences on this page — the list may have changed since the last page."
UNSETTLED = "No record settles it"
UNSETTLED_TITLE = "The speaker holds no contrary belief on record: a lie no record settles"
REVEALED = "Revealed"
REVEALED_TITLE = "The event of the reveal that settled it"
NOT_REVEALED = "Not revealed"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def count_text(n: int, revision: int, *, partial: bool, filtered: bool) -> str:
    """C11: "12 divergences" / "1 divergence", then "on this page" and/or "matching filters",
    then " · revision R"."""
    noun = "divergence" if n == 1 else "divergences"
    qualifier = ", ".join(text for text, on in ((ON_PAGE, partial), (MATCHING, filtered)) if on)
    count = f"{n:,} {noun} {qualifier}" if qualifier else f"{n:,} {noun}"
    return f"{count} · revision {revision}"


def empty_text(*, paged: bool, filtered: bool) -> str:
    """The empty page's line: a cursor page's, then a filtered one's, then the plain one."""
    if paged:
        return EMPTY_PAGE
    return NONE_FILTERED if filtered else NONE


@dataclass(frozen=True)
class Speaker:
    """A speaker for ``_character.html``: its C4 label ("" off-cast or without the story read: a
    short id), id and whether it is the player."""

    label: str
    character_id: str
    is_player: bool


@dataclass(frozen=True)
class DivergenceRow:
    """``_divergence_row.html``'s context for one item. ``speaker_url`` / ``subject_url`` are
    the "only this …" links (``None`` when already the filter, or no parsed subject);
    ``belief_url`` the Believes link (``None`` without ``believed_proposition_id`` or the
    Beliefs route); ``op_pk`` the #44 row when the Console logged the operation."""

    item: DivergenceItem
    when: Linked
    speaker: Speaker
    speaker_url: str | None
    asserted: Proposition | None
    subject_url: str | None
    believed: Proposition | None
    belief_url: str | None
    op_pk: int | None
    more: tuple[MoreItem, ...]
    raw_item: str


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _speaker(character_id: str, cast: Sequence[Character] | None, minor: frozenset[str]) -> Speaker:
    member = next((c for c in cast or () if c.id == character_id), None)
    if member is None or cast is None:
        return Speaker("", character_id, False)
    label = character_label(member.id, character_labels(cast), minor)
    return Speaker(label, character_id, member.is_player)


def _only(base: QueryDict, name: str, value: str) -> str:
    params = base.copy()
    params[name] = value
    return f"?{params.urlencode()}"


def _belief_url(beliefs_url: str | None, item: DivergenceItem) -> str | None:
    if beliefs_url is None or not item.believed_proposition_id:
        return None
    query = urlencode({"character": item.speaker, "proposition_id": item.believed_proposition_id})
    fragment = f"#b-{item.believed_belief_id}" if item.believed_belief_id else ""
    return f"{beliefs_url}?{query}{fragment}"


def _more(item: DivergenceItem) -> tuple[MoreItem, ...]:
    step = position_step(item.position)
    return (
        MoreItem("Utterance id", item.utterance_id, "code"),
        MoreItem("Asserted proposition id", item.asserted_proposition_id, "code"),
        MoreItem("Believed belief id", item.believed_belief_id, "code")
        if item.believed_belief_id
        else MoreItem("Believed belief id", EMPTY),
        MoreItem(step[0], step[1]),
    )


def divergence_rows(
    story_id: str,
    items: Sequence[DivergenceItem],
    *,
    cast: Sequence[Character] | None,
    minor: frozenset[str],
    base: QueryDict,
    speaker_filter: str,
    subject_filter: str,
    links: Mapping[str, int],
) -> list[DivergenceRow]:
    """The rows in the Engine's order. ``base`` is the page's query with the normalised filters
    and no cursor (the "only this …" links start from it)."""
    try:
        beliefs_url: str | None = reverse("inspect:beliefs", args=[story_id])
    except NoReverseMatch:
        beliefs_url = None
    rows = []
    for item in items:
        asserted = item.asserted_view
        subject = asserted.subject_id if asserted is not None else None
        rows.append(
            DivergenceRow(
                item=item,
                when=Linked(
                    knowledge_position_text(item.position),
                    knowledge_position_url(story_id, item.position),
                ),
                speaker=_speaker(item.speaker, cast, minor),
                speaker_url=None
                if item.speaker == speaker_filter
                else _only(base, "speaker", item.speaker),
                asserted=asserted,
                subject_url=None
                if not subject or subject == subject_filter
                else _only(base, "subject", subject),
                believed=item.believed_view,
                belief_url=_belief_url(beliefs_url, item),
                op_pk=links.get(item.operation_id),
                more=_more(item),
                raw_item=_pretty(dataclasses.asdict(item)),
            )
        )
    return rows


def _links(story_id: str, items: Sequence[DivergenceItem]) -> dict[str, int]:
    """One ``operation_links`` over the page's distinct operation ids; ``{}`` on a database
    failure (logged: every id renders as ``<code>``)."""
    try:
        return operation_links(story_id, {i.operation_id for i in items})
    except DatabaseError:
        log.warning("operation links unavailable for story %s", story_id, exc_info=True)
        return {}


def _divergences(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    minor = story_minor_ids(ctx.story.characters if ctx.story is not None else None)
    labels = {c.id: character_label(c.id, names, minor) for c in cast or ()}
    form = DivergencesForm(request.GET, cast=cast, labels=labels)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    params = request.GET.copy()
    for name in ("speaker", "subject"):
        if name in params:
            params[name] = form.normalised(name)
    params.pop(CURSOR, None)
    base = params.copy()
    if cursor:
        params[CURSOR] = cursor
    context: dict[str, Any] = {
        "form": form,
        "intro": INTRO,
        "cast_names": names,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "filter_url": reverse("inspect:divergences", args=[ctx.story_id]),
        "actions": None,  # convention 14: no story sets row actions yet
        "unsettled": UNSETTLED,
        "unsettled_title": UNSETTLED_TITLE,
        "revealed": REVEALED,
        "revealed_title": REVEALED_TITLE,
        "not_revealed": NOT_REVEALED,
        **paging(params, cursor, None, param=CURSOR),
    }
    if not form.is_valid():
        return context
    try:
        body = ctx.client.inspect_divergences(
            ctx.story_id, limit=PAGE_LIMIT, after_id=cursor, **form.query()
        )
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 422:
            context["after_refused"] = form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["not_found"] = NOT_FOUND
        else:
            context["divergences_error"] = panel_for(exc)
        return context

    filtered = form.any_filter
    links: dict[str, int] = {}
    if body.items:
        context["predicate_names"] = predicate_names(request, ctx.client, ctx.story_id)
        links = _links(ctx.story_id, body.items)
    context.update(
        divergences=body,
        rows=divergence_rows(
            ctx.story_id,
            body.items,
            cast=cast,
            minor=minor,
            base=base,
            speaker_filter=form.cleaned_data["speaker"],
            subject_filter=form.cleaned_data["subject"],
            links=links,
        ),
        count=count_text(
            len(body.items),
            body.revision,
            partial=bool(cursor or body.next_after_id),
            filtered=filtered,
        ),
        empty=empty_text(paged=cursor is not None, filtered=filtered),
        **paging(params, cursor, body.next_after_id, param=CURSOR),
    )
    return context


@require_safe
def divergences(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:divergences``, the story's lies (#192)."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, DIVERGENCES_TEMPLATE, _divergences)
