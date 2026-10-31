"""The Knowledge tab's Beliefs section (#190): what one character believes.

``inspect:beliefs`` (``/stories/<id>/inspect/knowledge/beliefs/``, the Knowledge tab selected)
renders through #135's :func:`console.views.inspect.inspect_page` with ``Tab.KNOWLEDGE`` (#189),
so a greyed tab makes no beliefs or registry read. The GET filters are
:class:`console.forms.inspect.BeliefsForm`'s (``character``, ``as_of``, ``proposition_id``), each
the Engine's query parameter; the cursor is ``after`` (convention 6 via
:func:`console.paging.paging`).

Reads per render, sequential: the shell's effective config and story (cast names), then one
``GET /stories/{id}/inspect/beliefs`` (``viewpoint=omniscient``, ``limit=100``), then, only when
it answered at least one row, convention 2's one registry read (``list_predicates``: the names,
``{}`` on failure: short ids, and #191's ``cardinality``), then #191's canon comparison
(:func:`console.belief_canon.canon_column`: at most 4 ``list_facts`` calls, none for a past
turn or a page with no comparable row). No beliefs call for
an invalid filter, a blank character (no modeled characters, or nothing typed when the story
read failed). An ``after`` failing the id pattern is dropped (:data:`console.paging.
CURSOR_DROPPED`) and never sent.

Errors stay inside the section (convention 10): 422 ``unknown_character`` beside Character; any
other 422 beside the field ``details.field`` names, else above the filters (``query.after`` with
"First page"); ``viewpoint_unresolved`` and 403 ``omniscient_required`` read "This read needs the
omniscient viewpoint"; 404 is the not-found line; 409 ``module_off`` escapes to the shell's
greyed panel; 503 ``erasure_pending`` to #87's held wrapper; anything else #43's panel below the
filters. Wording and rows: :mod:`console.beliefs`.
"""

import dataclasses
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.belief_canon import CanonColumn, canon_column
from console.beliefs import (
    ENTER_CHARACTER,
    FIRST_ORDER_NOTICE,
    IDENTITIES,
    IDENTITIES_FILTERED,
    IDENTITIES_FIRST_PAGE,
    NO_CHARACTERS,
    PLAYER_NOTICE,
    belief_rows,
    count_text,
    empty_text,
    header_text,
    identity_rows,
    story_minor_ids,
    time_text,
)
from console.canon import cast_names
from console.cast import Character, character_labels
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import AuthoredBelief, BeliefWritten, PredicateList
from console.engine_errors import EMPTY, log_engine_error, panel_for
from console.forms.beliefs import (
    NESTED_LEGEND,
    NESTED_LINE,
    NOT_READABLE,
    DirectBeliefForm,
    gated_fields,
    nested_depth,
)
from console.forms.inspect import OMNISCIENT_TEXT, VIEWPOINT_UNRESOLVED, BeliefsForm
from console.held import is_held
from console.ids import is_ulid, new_ulid
from console.inspect import (
    NESTED_BELIEFS,
    Tab,
    Unavailable,
    gate_refusal,
    is_module_off,
    m5_gate,
    module_off_unavailable,
)
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED, paging
from console.positions import knowledge_position_text, knowledge_position_url, valid_minutes_text
from console.predicates import predicate_names
from console.views.inspect import OMNISCIENT_REQUIRED, TEMPLATE_KEY, InspectContext, inspect_page
from console.wording import character_label, humanised, write_refusal

BELIEFS_TEMPLATE = "console/inspect/beliefs.html"
PAGE_LIMIT = 100  # convention 6 (the route allows 500)
CURSOR = "after"


def _beliefs(ctx: InspectContext) -> dict[str, Any]:
    request = ctx.request
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    minor = story_minor_ids(ctx.story.characters if ctx.story is not None else None)
    labels = {c.id: character_label(c.id, names, minor) for c in cast or ()}
    form = BeliefsForm(request.GET, cast=cast, labels=labels)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    context: dict[str, Any] = {
        "form": form,
        "cast_names": names,
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "filter_url": reverse("inspect:beliefs", args=[ctx.story_id]),
        "actions": None,  # convention 14: #194's row actions (set once a character is read)
        "actions_next": request.get_full_path(),
        "direct_url": direct_url(ctx.story_id),  # #194: "Direct a belief…"
        "marks": None,  # #191's Canon column (set once a beliefs page answered)
    }
    form.is_valid()
    if not form.valid("character"):
        return context
    character: str = form.cleaned_data["character"]
    if not character:
        if not form.errors:
            context["prompt"] = NO_CHARACTERS if cast is not None else ENTER_CHARACTER
        return context
    label = character_label(character, names, minor)
    context.update(
        direct_url=direct_url(ctx.story_id, character),
        actions=ACTIONS_TEMPLATE,
        actions_holder=character,
    )
    player = any(c.id == character and c.is_player for c in cast or ())
    as_of_valid = form.valid("as_of")
    time = time_text(form.cleaned_data["as_of"]) if as_of_valid else None
    context["header"] = header_text(label, time, None)
    params = request.GET.copy()
    params["character"] = character  # the (possibly defaulted) character, always explicit
    if "proposition_id" in params:
        params["proposition_id"] = str(form.data.get("proposition_id") or "")
    params.pop(CURSOR, None)
    if cursor:
        params[CURSOR] = cursor
    context.update(paging(params, cursor, None, param=CURSOR))
    if not form.is_valid():
        return context
    query = form.query()
    try:
        body = ctx.client.inspect_beliefs(ctx.story_id, limit=PAGE_LIMIT, after=cursor, **query)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code in (OMNISCIENT_REQUIRED, VIEWPOINT_UNRESOLVED):
            context["omniscient_required"] = OMNISCIENT_TEXT
        elif isinstance(exc, EngineError) and exc.http_status == 422:
            context["after_refused"] = form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["beliefs_not_found"] = True
        else:
            context["beliefs_error"] = panel_for(exc)
        return context

    as_of, proposition = query["as_of"], query["proposition_id"]
    column = CanonColumn({})
    if body.beliefs:
        registry = _registry(request, ctx.client, ctx.story_id)
        context["predicate_names"] = {} if registry is None else registry.names()
        column = canon_column(
            request,
            ctx.client,
            ctx.story_id,
            body.beliefs,
            body.revision,
            cardinality=None if registry is None else registry.cardinality(),
            as_of=as_of,
        )
    rows = belief_rows(ctx.story_id, body.beliefs, column.marks)
    filtered = as_of is not None or proposition is not None
    context.update(
        beliefs=body,
        rows=rows,
        header=header_text(label, time, body.revision),
        count=count_text(len(rows), partial=bool(cursor or body.next_after), filtered=filtered),
        first_order=FIRST_ORDER_NOTICE,
        marks=column.marks,
        canon_notices=column.notices,
        canon_summary=column.summary,
        player_notice=PLAYER_NOTICE if player else None,
        empty=empty_text(
            label, paged=cursor is not None, proposition=bool(proposition), as_of=as_of
        ),
        show_identities=not player,
        identities_heading=IDENTITIES_FILTERED if filtered else IDENTITIES,
        identities_first_page=IDENTITIES_FIRST_PAGE if cursor else None,
        identities=identity_rows(
            ctx.story_id, body.identities, names, frozenset(b.belief_id for b in body.beliefs)
        ),
        **paging(params, cursor, body.next_after, param=CURSOR),
    )
    return context


def _registry(request: HttpRequest, client: EngineClient, story_id: str) -> PredicateList | None:
    """Convention 2's one registry read, supplying both the predicate names and #191's
    ``cardinality``; ``None`` when it fails (logged: short ids, no functional rule)."""
    try:
        return client.list_predicates(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


@require_safe
def beliefs(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:beliefs``, one character's partition (#190)."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, BELIEFS_TEMPLATE, _beliefs)


# --- direct a belief (#194) -----------------------------------------------------------------
#
# ``inspect:belief-direct`` (``knowledge/beliefs/direct/``, GET and POST) and
# ``inspect:belief-written?report=<pk>`` (convention 8), both through ``inspect_page`` with the
# Knowledge tab, so a greyed tab makes no other call. The GET makes, at most, the one pre-fill
# read (``inspect_beliefs(character=holder, proposition_id=…, limit=1)``) when ``holder`` and
# ``proposition_id`` both pass ``is_ulid``. The POST checks #188's ``nested_beliefs`` gate on the
# raw POST (the shell's effective-config read is its only other call), then the form (every
# check before any call), then ``write_belief`` once with the page's key; the result page renders
# on the POST response with one ``predicate_names`` read (convention 17). Refusals re-render the
# form with a fresh key; the mapping is ``_refused``'s. Wording: :mod:`console.forms.beliefs`.

DIRECT_TEMPLATE = "console/inspect/belief_direct.html"
WRITTEN_TEMPLATE = "console/inspect/belief_written.html"
ACTIONS_TEMPLATE = "console/inspect/_belief_actions.html"
MISSING_TEMPLATE = "console/inspect/canon_missing.html"
WRITE_KIND = "write_belief"
REPORT_PARAM = "report"
VERB_PHRASE = "direct a belief"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
PREFILL_FAILED = "Couldn't pre-fill from that belief"
FIRST_ORDER = "first_order"
NESTED = "nested"


def beliefs_url(story_id: str, **params: str) -> str:
    url = reverse("inspect:beliefs", args=[story_id])
    kept = {k: v for k, v in params.items() if v}
    return f"{url}?{urlencode(kept)}" if kept else url


def direct_url(story_id: str, holder: str | None = None) -> str:
    """``inspect:belief-direct``, carrying ``holder`` when there is one."""
    url = reverse("inspect:belief-direct", args=[story_id])
    return f"{url}?{urlencode({'holder': holder})}" if holder else url


def written_report_url(story_id: str, pk: int) -> str:
    url = reverse("inspect:belief-written", args=[story_id])
    return f"{url}?{urlencode({REPORT_PARAM: pk})}"


def _normalised(params: QueryDict, name: str) -> str:
    return str(params.get(name) or "").strip().upper()


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


# --- the result page ---


@dataclass(frozen=True)
class Holder:
    """A character for ``_character.html``: its cast name ("" off-cast or without the story
    read: a short id), id and whether it is the player."""

    label: str
    character_id: str
    is_player: bool


def _holder(character_id: str, cast: Sequence[Character] | None) -> Holder:
    member = next((c for c in cast or () if c.id == character_id), None)
    if member is None or cast is None:
        return Holder("", character_id, False)
    return Holder(character_labels(cast)[member.id], character_id, member.is_player)


def recorded_chain(request_body: object) -> list[str] | None:
    """The recorded request's ``attributed_to`` (outermost first), or ``None`` when it isn't a
    list of strings."""
    chain = request_body.get("attributed_to") if isinstance(request_body, dict) else None
    if isinstance(chain, list) and chain and all(isinstance(c, str) for c in chain):
        return chain
    return None


def kind_text(belief: AuthoredBelief, chain: list[str] | None, names: Mapping[str, str]) -> str:
    """ "First-order", or "Nested: Alice thinks Bob believes it (depth 2)"."""
    if belief.kind == FIRST_ORDER:
        return "First-order"
    if belief.kind != NESTED:
        return humanised(belief.kind)
    holder = character_label(belief.holder_character_id, names)
    if chain is None:
        attributed = character_label(belief.attributed_holder_id, names)
        return (
            f"Nested: {holder} thinks … {attributed} believes it "
            f"(depth {belief.depth}, chain not recorded)"
        )
    thinks = " thinks ".join([holder, *(character_label(c, names) for c in chain)])
    return f"Nested: {thinks} believes it (depth {belief.depth})"


def confidence_text(level: str, scalar: float | None) -> str:
    """Convention 19: "Suspected · 0.300" (the scalar left out when null)."""
    text = humanised(level)
    return text if scalar is None else f"{text} · {scalar:.3f}"


def parse_written(raw: object) -> tuple[BeliefWritten, list[Any]] | None:
    """A stored ``BeliefWritten`` with ``closed`` read item by item (C2): the parsed answer and
    the items that aren't ids (shown raw in More); ``None`` when it doesn't parse."""
    if not isinstance(raw, dict):
        return None
    closed = raw.get("closed")
    bad: list[Any] = []
    if isinstance(closed, list):
        bad = [c for c in closed if not isinstance(c, str)]
        raw = {**raw, "closed": [c for c in closed if isinstance(c, str)]}
    parsed = BeliefWritten.from_json(raw)
    return None if parsed is None else (parsed, bad)


def _written(
    ctx: InspectContext,
    written: BeliefWritten,
    row: EngineOperation | None,
    *,
    chain: list[str] | None,
    raw: object,
    bad_closed: Sequence[Any] = (),
) -> dict[str, Any]:
    """The result page's context (rendered on the POST response, or for ``?report=``)."""
    story_id = ctx.story_id
    cast = ctx.story.cast if ctx.story is not None else None
    names = cast_names(cast)
    belief, transition = written.belief, written.transition
    holder_id = belief.holder_character_id
    first_order = belief.kind == FIRST_ORDER
    return {
        TEMPLATE_KEY: WRITTEN_TEMPLATE,
        "written": written,
        "belief": belief,
        "transition": transition,
        "holder": _holder(holder_id, cast),
        "kind_text": kind_text(belief, chain, names),
        "proposition": belief.proposition_view,
        "raw_proposition": _pretty(belief.proposition),
        "predicate_names": predicate_names(ctx.request, ctx.client, story_id),  # convention 17
        "cast_names": names,
        "confidence": confidence_text(belief.confidence_level, belief.confidence_scalar),
        "transition_confidence": confidence_text(
            transition.confidence_level, transition.confidence_scalar
        ),
        "accessibility": f"{belief.accessibility:.3f}",
        "held_from": knowledge_position_text(belief.held_from),
        "held_from_url": knowledge_position_url(story_id, belief.held_from),
        "held_to": knowledge_position_text(belief.held_to),
        "held_to_url": knowledge_position_url(story_id, belief.held_to),
        "valid_text": valid_minutes_text(belief.valid_from, belief.valid_to),
        "valid_at": EMPTY if transition.valid_at is None else f"{transition.valid_at} min",
        "position": knowledge_position_text(transition.position),
        "position_url": knowledge_position_url(story_id, transition.position),
        "closed": written.closed,
        "bad_closed": [_pretty(c) for c in bad_closed],
        "antecedent": _pretty(transition.antecedent),
        "raw_response": _pretty(raw),
        "operation_pk": row.pk if row is not None else None,
        "replayed": row is not None and row.replayed,
        "report_url": written_report_url(story_id, row.pk) if row is not None else None,
        "first_order": first_order,
        "see_url": beliefs_url(story_id, character=holder_id, proposition_id=belief.proposition_id)
        if first_order
        else None,
        "see_label": f"See {character_label(holder_id, names)}'s belief",
        "not_readable": None if first_order else NOT_READABLE,
        "again_url": direct_url(story_id, holder_id),
    }


def written_row(
    pk: object, story_id: str
) -> tuple[EngineOperation, BeliefWritten, list[Any]] | None:
    """The #44 row ``pk`` and its answer when it is an ``ok`` ``write_belief`` of this story
    whose stored response parses; else ``None``."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if (
        row.kind != WRITE_KIND
        or row.story_id != story_id
        or row.outcome != EngineOperation.Outcome.OK
    ):
        return None
    parsed = parse_written(row.response)
    return None if parsed is None else (row, *parsed)


def _row_chain(row: EngineOperation | None) -> list[str] | None:
    stored = row.request if row is not None else None
    return recorded_chain(stored.get("body") if isinstance(stored, dict) else None)


def _report(ctx: InspectContext) -> dict[str, Any]:
    found = written_row(ctx.request.GET.get(REPORT_PARAM), ctx.story_id)
    if found is None:
        return {"missing": NOT_FOUND, "back_url": beliefs_url(ctx.story_id)}
    row, body, bad = found
    return _written(ctx, body, row, chain=_row_chain(row), raw=row.response, bad_closed=bad)


@require_safe
def written(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:belief-written?report=<pk>``: a belief write's result page again, from the #44
    row's stored response and request (convention 8)."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, WRITTEN_TEMPLATE, _report)


# --- the form page ---


def _prefill(ctx: InspectContext) -> tuple[dict[str, Any], str | None]:
    """The GET's initial values from the belief ``holder`` holds about ``proposition_id`` (one
    read when both are ids), and the notice when that couldn't be done."""
    params = ctx.request.GET
    holder = _normalised(params, "holder")
    initial: dict[str, Any] = {"holder": holder} if is_ulid(holder) else {}
    if "proposition_id" not in params:
        return initial, None
    proposition_id = _normalised(params, "proposition_id")
    if not (is_ulid(holder) and is_ulid(proposition_id)):
        return initial, PREFILL_FAILED
    try:
        body = ctx.client.inspect_beliefs(
            ctx.story_id, holder, proposition_id=proposition_id, limit=1
        )
    except EngineClientError as exc:
        if is_held(exc):
            raise
        log_engine_error(ctx.request, exc)
        return initial, PREFILL_FAILED
    if not body.beliefs:
        return initial, PREFILL_FAILED
    first = body.beliefs[0]
    initial["proposition"] = first.proposition
    initial["confidence_level"] = first.current_level
    if first.current_scalar is not None:
        initial["confidence_scalar"] = f"{first.current_scalar:.3f}"
    return initial, None


def _form(
    ctx: InspectContext,
    data: QueryDict | None,
    gate: Unavailable | None,
    initial: Mapping[str, Any] | None = None,
) -> DirectBeliefForm:
    cast = ctx.story.cast if ctx.story is not None else None
    return DirectBeliefForm(
        data,
        cast=cast,
        names={c.id: character_label(c.id, cast_names(cast)) for c in cast or ()},
        depth=nested_depth(ctx.effective),
        nested_gate=gate,
        initial=initial,
    )


def _context(ctx: InspectContext, form: DirectBeliefForm, holder: str) -> dict[str, Any]:
    gate = form.nested_gate
    return {
        "form": form,
        "idempotency_key": new_ulid(),  # #43: every render mints a fresh key
        "action_url": direct_url(ctx.story_id),
        "back_url": beliefs_url(ctx.story_id, character=holder if is_ulid(holder) else ""),
        "predicates_url": reverse("inspect:predicates", args=[ctx.story_id]),
        "cast_unavailable": CAST_UNAVAILABLE if ctx.story is None else None,
        "nested_gate": gate,
        "nested_legend": NESTED_LEGEND,
        "nested_line": NESTED_LINE,
        "not_readable": NOT_READABLE,
    }


def _refused(
    ctx: InspectContext, exc: EngineClientError, form: DirectBeliefForm, context: dict[str, Any]
) -> dict[str, Any] | HttpResponse:
    """A refused write: the form again (a fresh key, every input kept) with the refusal, or
    convention 10's not-found page; held and other ``module_off`` escape to the shell."""
    if is_held(exc):
        raise exc
    if is_module_off(exc) and exc.details.get("module") != NESTED_BELIEFS:
        raise exc
    log_engine_error(ctx.request, exc)
    if not isinstance(exc, EngineError):
        context["write_error"] = panel_for(exc)
        return context
    if is_module_off(exc):
        gate = module_off_unavailable(exc, Tab.KNOWLEDGE, ctx.effective)
        regreyed = _form(ctx, ctx.request.POST, gate)
        regreyed.is_valid()
        context.update(form=regreyed, nested_gate=gate, refusal=gate.reason)
        return context
    if exc.http_status == 404:
        back = beliefs_url(ctx.story_id)
        context = {"story_id": ctx.story_id, "back_url": back, "text": NOT_FOUND}
        return render(ctx.request, MISSING_TEMPLATE, context)
    if exc.http_status == 422:
        form.apply_engine_error(exc)
        return context
    refusal = write_refusal(exc, ctx.story_id, VERB_PHRASE)
    if refusal is not None:
        context.update(refusal=refusal.text, refusal_url=refusal.url)
    else:
        context["write_error"] = panel_for(exc)
    return context


def _direct(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    gate = m5_gate(NESTED_BELIEFS, ctx.effective)
    if request.method != "POST":
        initial, notice = _prefill(ctx)
        form = _form(ctx, None, gate, initial)
        context = _context(ctx, form, str(initial.get("holder") or ""))
        context["prefill_failed"] = notice
        return context
    form = _form(ctx, request.POST, gate)
    valid = form.is_valid()
    context = _context(ctx, form, str(form.cleaned_data.get("holder") or ""))
    depth = nested_depth(ctx.effective)
    refused = gate_refusal(request.POST, ctx.effective, gated_fields(request.POST, depth))
    if refused is not None:
        context["refusal"] = refused.reason
        return context
    if not valid:
        return context
    body = form.body
    try:
        result = ctx.client.write_belief(
            ctx.story_id, body, idempotency_key=posted_idempotency_key(request)
        )
    except EngineClientError as exc:
        return _refused(ctx, exc, form, context)
    pk = getattr(ctx.client, "last_operation_id", None)
    row = EngineOperation.objects.filter(pk=pk).first() if isinstance(pk, int) else None
    raw = row.response if row is not None else dataclasses.asdict(result)
    return _written(ctx, result, row, chain=recorded_chain(body), raw=raw)


@require_http_methods(["GET", "HEAD", "POST"])
def direct(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:belief-direct``: the Direct-a-belief form, then
    ``POST /admin/stories/{id}/beliefs`` and the result page on the POST response."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, DIRECT_TEMPLATE, _direct)
