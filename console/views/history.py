"""The history browser (#52): ``/stories/<id>/history/``, a story's transcript at any
viewpoint and past turn, with audit records, the rolling summary and the variant chains.

One GET page. The controls (:class:`console.forms.history.HistoryForm`) submit as a GET, so
every view is a shareable URL. Reads, each degrading its own section:

- ``GET /stories/{id}`` (cast for the selects, head turn) — required; a failure is the error
  page (#43's middleware);
- ``GET /stories/{id}/variants`` — the variants panel (and a chain's tip for the as-of list);
- ``GET /stories/{id}/history`` — only when the controls are valid; a refusal (422 ``as_of``
  beyond head, 403 ``omniscient_required``) replaces the items area and header line with the
  error panel, the controls and the variants panel still render;
- ``GET /stories/{id}/settings`` — only when audit records are shown, for policy names;
- ``GET /stories/{id}/predicates`` — once, only when the page shows a structured beat (#147),
  for the propositions' predicate names (a failure shows ids);
- ``GET /stories/{id}/effective-config`` — at most once, on an active-chain page only, when a
  turn's shown items don't say whether it is structured: a turn whose input is shown but none of
  whose items carries ``propositions`` (#181), for "Explain retrieval", or any turn ≥ 1 none of
  whose items carries ``propositions`` or ``fidelity`` (no player input needed), for
  "Perception" (#195): whether the story runs the structured pipeline (a failure: no link);
- ``GET /operations/{id}?viewpoint=omniscient`` — only with "Show all traces" (#147): once per
  structured turn on the page, the first 20, on a local pool with a shared deadline
  (:func:`turn_traces`); a failure marks that turn's beats unavailable and lists it.

No read is added for M6 (#254): each turn's State changes, its beats' (omniscient) and the
Incomplete badge come from the history read itself, worded without units (D5, D10).

An HTMX request (the viewpoint select's ``change``) gets the controls fragment alone, so the
audit box enables and disables live; without JavaScript the form refuses audit server-side.

M7 (#304): View "As originally recorded" (``view=as_recorded``) is built by
:func:`_as_recorded_page`: after the story and variants reads, one
``GET /stories/{id}/history?view=as_recorded&viewpoint=omniscient`` (``as_of`` the checkpoint,
``variant`` the chain) and **no** settings, effective-config, predicates or turn-trace read
(Explain, Perception, notices, traces and policy names are corrected-view features). The page is
read-only (:attr:`Page.as_recorded`: no feedback widget, no Explain/Perception/Report link, no
paging). A refusal of the checkpoint (404 ``checkpoint_not_found``, 409 ``checkpoint_incomplete``,
422 ``validation_error`` on ``query.as_of``) is shown beside Checkpoint
(:attr:`Controls.checkpoint_error`); 403 ``omniscient_required`` and 422 ``invalid_view`` are
Console bugs, worded and logged; anything else is #43's panel, as on main. The View select
re-renders the controls over HTMX like the viewpoint select.
"""

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.utils.safestring import SafeString
from django.views.decorators.http import require_safe

from console import cast as cast_helpers
from console.canon import cast_names
from console.effects import outbox_list_notice_html
from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import History, HistoryItem, Story, SummarySegment, VariantChain
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.engine_form_errors import engine_field_errors
from console.feedback import FeedbackWidget, collapsed, first_parts
from console.forms.history import AS_RECORDED, HistoryForm, as_of_options
from console.held import is_held
from console.history import (
    AS_RECORDED_CONTROLS_NOTE,
    AS_RECORDED_NOTE,
    CHECKPOINT_INCOMPLETE,
    CHECKPOINT_NOT_FOUND,
    INVALID_VIEW_TEXT,
    NO_CHECKPOINT_YET,
    NOT_LOADED,
    UNAVAILABLE,
    BeatTrace,
    DerivedRow,
    ErasureRow,
    TurnGroup,
    audience_filter_label,
    audit_record,
    beat_trace,
    chain_key,
    chain_tip,
    checkpoint_text,
    derived_rows,
    erased_since,
    erasure_rows,
    explain_query,
    explain_url,
    inspector_url,
    is_structured,
    operation_links,
    parse_boundary,
    policy_names,
    report_item,
    split_audit,
    summary_rows,
    turn_groups,
    turn_label,
)
from console.inspect import is_structured_pipeline
from console.nav import story_links
from console.notices import notice_cards, overridden_notices
from console.operation_log import note_engine_record
from console.perception import perception_url, turn_links_perception
from console.portability import is_importing
from console.predicates import predicate_names
from console.rewrites import AS_RECORDED_LINE, OMNISCIENT_REQUIRED_TEXT
from console.story_pause import is_paused, pause_banner
from console.timeline import (
    ARCHIVED_NOTE,
    PAUSED_NOTE,
    PRUNED_CHAIN_NOTE,
    PRUNED_MEMBERSHIP,
    Basis,
    Link,
)
from console.turn_trace import TurnTrace, read_turn_trace
from console.world import viewpoint_line

CONTROLS_TEMPLATE = "console/history/_controls.html"
PAGE_TEMPLATE = "console/history/history.html"
TRACE_TURNS_MAX = 20  # turns whose trace one page reads (#147)
TRACE_WORKERS = 4
TRACE_DEADLINE_S = 10.0  # shared by every trace read of one page
TRACE_CAP_NOTE = f"Escalation and repair loaded for the first {TRACE_TURNS_MAX} turns on this page."
TIMEOUT = "timeout"
CHECKPOINT_NOT_FOUND_CODE = "checkpoint_not_found"
CHECKPOINT_INCOMPLETE_CODE = "checkpoint_incomplete"
OMNISCIENT_REQUIRED_CODE = "omniscient_required"
INVALID_VIEW_CODE = "invalid_view"
VALIDATION_ERROR_CODE = "validation_error"
HTMX_ATTRS = {
    "hx-trigger": "change",
    "hx-target": "#history-controls",
    "hx-swap": "outerHTML",
    "hx-include": "closest form",
}


@dataclass(frozen=True)
class Controls:
    """The controls fragment's context."""

    story_id: str
    form: HistoryForm
    head: int
    last: int  # the as-of datalist's last turn: the chain's tip, else the head
    options: list[int]
    audit_disabled: bool
    show_errors: bool  # not on the HTMX re-render, which only follows the viewpoint select
    player_view_disabled: bool = False  # an explicit omniscient viewpoint (#62)
    # #304: a refusal of the as-recorded checkpoint, shown under Checkpoint.
    checkpoint_error: str = ""
    as_recorded_line: str = AS_RECORDED_LINE  # E2, beside the disabled viewpoint (#304)
    as_recorded_controls_note: str = AS_RECORDED_CONTROLS_NOTE


@dataclass(frozen=True)
class ChainRow:
    chain: VariantChain
    key: str
    view_url: str
    parent: str
    swap: Link | None = None  # every chain but the active one (#53) and a pruned one (#293)
    fork: Link | None = None  # fork along this chain, through its tip (#53); not when pruned
    note: str = ""  # a pruned chain's PRUNED_CHAIN_NOTE (#293)

    @property
    def pruned(self) -> bool:
        return self.chain.membership == PRUNED_MEMBERSHIP


@dataclass(frozen=True)
class Page:
    """Everything below the controls."""

    history: History | None = None
    error: ErrorPanel | None = None
    viewpoint_label: str = ""
    groups: list[TurnGroup] = field(default_factory=list)
    summary: list[SummarySegment] | None = None
    older_url: str = ""
    newest_url: str = ""
    chain: str = ""  # the variant parameter, when set
    clear_chain_url: str = ""
    # ``?variant=`` naming a pruned chain, answered 409 ``pruned`` (#293): the note and "Clear
    # chain" stand in for the error panel.
    pruned_note: str = ""
    settings_error: ErrorPanel | None = None
    legend: bool = False  # a structured beat is shown: the verdict legend (#147)
    classification_legend: bool = False  # a classified say is shown: the legend line (#207)
    trace_cap_note: str = ""
    trace_failures: str = ""  # "Turn trace unavailable: T3 (timeout), …"
    # A character read's (label, raw) audience filter; ``None`` for an omniscient echo (#62).
    audience_filter: tuple[str, str] | None = None
    # #254: D4's line on a character read showing a State-changes row, and D11's notice when a
    # shown change carries an outbox id.
    world_viewpoint: str = ""
    outbox_notice: SafeString | str = ""
    # #304: the history as originally recorded. Read-only: no per-row write or corrected-only
    # control renders (#305's "Edit this line…" link renders only when this is False).
    as_recorded: bool = False
    checkpoint_text: str = ""  # "turn 2", "turn 2⁺¹", "the latest (turn 3)"
    erased_since: str = ""  # ERASED_SINCE, "" with no erasure marker
    erasures: list[ErasureRow] = field(default_factory=list)
    derived: list[DerivedRow] | None = None  # None: ``derived`` is null, the section is left out
    back_url: str = ""  # "See turn <N> corrected"
    back_turn: int | None = None
    # A checkpoint refusal (copied into ``Controls.checkpoint_error``): the items area shows only
    # main's "nothing was read" line.
    as_recorded_error: str = ""
    # 403 ``omniscient_required`` / 422 ``invalid_view``: the worded Console bug, with ``error``
    # (#43's panel) collapsed under it.
    error_text: str = ""

    @property
    def as_recorded_note(self) -> str:
        return AS_RECORDED_NOTE


def _url(story_id: str, params: QueryDict) -> str:
    base = reverse("stories:history", args=[story_id])
    query = urlencode([(k, v) for k, values in params.lists() for v in values if v != ""])
    return f"{base}?{query}" if query else base


def _with(request: HttpRequest, **changes: str | None) -> QueryDict:
    params = request.GET.copy()
    for name, value in changes.items():
        if value is None:
            params.pop(name, None)
        else:
            params[name] = value
    return params


def _variants(
    client: EngineClient, request: HttpRequest, story_id: str
) -> tuple[list[VariantChain], ErrorPanel | None]:
    try:
        return client.list_variants(story_id), None
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return [], panel_for(exc)


def _controls(
    story: Story,
    form: HistoryForm,
    chains: list[VariantChain],
    *,
    show_errors: bool = True,
    checkpoint_error: str = "",
) -> Controls:
    head = max(story.next_turn_index - 1, 0)
    variant = form.value("variant")
    tip = chain_tip(chains, variant) if variant else None
    last = tip if tip is not None else head
    url = reverse("stories:history", args=[story.id])
    for name in ("view", "viewpoint"):  # #304: the View select follows the viewpoint's lead
        form.fields[name].widget.attrs.update({"hx-get": url, **HTMX_ATTRS})
    form.fields["as_of"].widget.attrs.update(
        {"list": "as-of-turns", "placeholder": f"head ({last})"}
    )
    form.fields["checkpoint"].widget.attrs.update({"placeholder": "latest"})
    if form.as_recorded:  # #304: shown, but not read on this view
        for name in ("actor_character_id", "limit"):
            form.fields[name].widget.attrs["disabled"] = True
    return Controls(
        story.id,
        form,
        head,
        last,
        as_of_options(last),
        audit_disabled=not form.omniscient,
        show_errors=show_errors,
        player_view_disabled=form.omniscient or form.as_recorded,
        checkpoint_error=checkpoint_error,
    )


def switched_view(params: QueryDict) -> QueryDict:
    """The HTMX View switch (#304): a valid ``as_of`` pre-fills Checkpoint going to the
    as-recorded view, and a valid checkpoint's turn (``N`` of ``N+k``) pre-fills ``as_of``
    going back, when the other control wasn't submitted."""
    data = params.copy()
    if data.get("view") == AS_RECORDED:
        as_of = data.get("as_of", "")
        if "checkpoint" not in data and as_of.isascii() and as_of.isdigit():
            data["checkpoint"] = str(int(as_of))
    elif "as_of" not in data:
        parsed = parse_boundary(data.get("checkpoint"))
        if parsed is not None:
            data["as_of"] = str(parsed[0])
    return data


def _chain_rows(request: HttpRequest, story: Story, chains: list[VariantChain]) -> list[ChainRow]:
    """Each chain with its "view" link (``viewpoint``, ``limit`` and ``player_view`` kept,
    ``variant`` set) and
    its Swap (not on the active chain; ``&check=1`` when it ``requires_check``) and Fork links,
    carrying the story read's revision and head (#53). A pruned chain (#293) gets none of the
    three, only :data:`PRUNED_CHAIN_NOTE`: the Engine refuses each with 409 ``pruned``."""
    story_id = story.id
    basis = Basis(
        story_id,
        story.timeline_revision,
        story.next_turn_index - 1,
        story.name or "",
        story.status == "archived",
        request.get_full_path(),
        paused=is_paused(story),
    )
    rows = []
    for chain in chains:
        key = chain_key(chain)
        params = QueryDict(mutable=True)
        for name in ("viewpoint", "limit", "player_view", "view", "checkpoint"):
            if request.GET.get(name):
                params[name] = request.GET[name]
        params["variant"] = key
        parent = f"{chain.parent.turn}:{chain.parent.variant}" if chain.parent else "—"
        if chain.membership == PRUNED_MEMBERSHIP:
            rows.append(ChainRow(chain, key, "", parent, note=PRUNED_CHAIN_NOTE))
            continue
        swap = (
            basis.swap(chain.turn, chain.variant, requires_check=chain.requires_check)
            if chain.membership != "active"
            else None
        )
        fork = basis.fork(chain.tip_turn, chain=(chain.turn, chain.variant))
        rows.append(ChainRow(chain, key, _url(story_id, params), parent, swap, fork))
    return rows


def _page(client: EngineClient, request: HttpRequest, story: Story, form: HistoryForm) -> Page:
    query = form.query()
    try:
        history = client.get_history(story.id, **query)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        variant = query["variant"] or ""
        if variant and isinstance(exc, EngineError) and exc.code == PRUNED_MEMBERSHIP:
            clear = _url(story.id, _with(request, variant=None))
            return Page(chain=variant, clear_chain_url=clear, pruned_note=PRUNED_CHAIN_NOTE)
        return Page(error=panel_for(exc), chain=variant)
    cast = story.cast
    labels = cast_helpers.character_labels(cast)
    items, audit_items = split_audit(history)
    names: dict[str, str | None] = {}
    settings_error = None
    if query["include_audit"]:
        try:
            names = policy_names(client.get_story_settings(story.id))
        except EngineClientError as exc:
            log_engine_error(request, exc)
            settings_error = panel_for(exc)
    links = operation_links(story.id, [i.operation_id for i in [*items, *audit_items]])
    records = [audit_record(i, names) for i in audit_items]
    if not query["include_audit"]:  # erasure markers show only with omniscient audit (#87)
        records = [r for r in records if not r.is_marker]
    groups = turn_groups(
        history,
        items,
        records,
        labels=labels,
        links=links,
        actor_filtered=bool(query["actor_character_id"]),
        omniscient=history.output_viewpoint == cast_helpers.OMNISCIENT,
        story_id=story.id,
    )
    _with_feedback(groups, collapsed(story.id, "transcript", request.get_full_path()))
    variant = query["variant"] or ""
    # A chain view isn't the active timeline (#142): no Explain link, no effective-config read.
    structured = None if variant else _story_structured(client, request, story.id)
    if structured is not None:
        _with_explain(story.id, groups, structured)
    _with_perception(story.id, groups, structured, variant or None)
    _with_report(story.id, groups)
    _with_notices(request, story, groups, labels, chain=bool(variant))
    traces = _with_traces(client, request, story, groups, load=form.show_traces)
    older = history.next_before_turn
    return Page(
        history=history,
        viewpoint_label=cast_helpers.viewpoint_label(history.output_viewpoint, cast),
        groups=groups,
        summary=summary_rows(history.summary) if history.summary is not None else None,
        older_url=_url(story.id, _with(request, before_turn=str(older))) if older else "",
        newest_url=(
            _url(story.id, _with(request, before_turn=None)) if query["before_turn"] else ""
        ),
        chain=variant,
        clear_chain_url=_url(story.id, _with(request, variant=None)) if variant else "",
        settings_error=settings_error,
        legend=traces.legend,
        classification_legend=traces.classification_legend,
        trace_cap_note=traces.cap_note,
        trace_failures=traces.failures,
        audience_filter=(
            None
            if history.output_viewpoint == cast_helpers.OMNISCIENT
            else audience_filter_label(history.audience_filter)
        ),
        world_viewpoint=_world_viewpoint(history, groups, labels),
        outbox_notice=(outbox_list_notice_html(story_id=story.id) if _dispatched(groups) else ""),
    )


def _world_viewpoint(
    history: History, groups: Sequence[TurnGroup], labels: Mapping[str, str]
) -> str:
    """D4's "Viewpoint: Alice — hidden States are left out" on a character read whose page shows
    a State-changes row (#254); "" otherwise, or when ``output_viewpoint`` isn't a string."""
    viewpoint: object = history.output_viewpoint
    if not isinstance(viewpoint, str) or not viewpoint or viewpoint == cast_helpers.OMNISCIENT:
        return ""
    if not any(g.state_changes or g.state_changes_unparsed for g in groups):
        return ""
    return viewpoint_line(viewpoint, labels)


def _dispatched(groups: Sequence[TurnGroup]) -> bool:
    """Whether a parsed turn- or beat-level change on the page carries an outbox id (D11)."""
    rows = [
        *(c for g in groups for c in g.state_changes),
        *(c for g in groups for r in g.rows for c in r.beat_changes),
    ]
    return any(c.outbox_id for c in rows)


# --- Beat traces (#147) -------------------------------------------------------------------------


def read_failure(exc: EngineClientError) -> str:
    """A failed trace (or perception, #195) read's reason in the page notice."""
    if isinstance(exc, EngineError):
        return f"HTTP {exc.http_status} {exc.code}" if exc.code else f"HTTP {exc.http_status}"
    if isinstance(exc, EngineUnreachable):
        return "unreachable"
    return "error"


_failure = read_failure  # the name before #195 made it public


def turn_traces(
    client: EngineClient,
    operation_ids: Sequence[str],
    *,
    request: HttpRequest | None = None,
) -> dict[str, TurnTrace | str]:
    """Each operation's trace (#139's :func:`read_turn_trace`), or why it is unavailable: the
    notice reason ("timeout", "HTTP 503 database_unavailable", "unreachable", "HTTP 409 …").

    The reads run on a pool of 4 local to this call, under one deadline for all of them; reads
    still running then are abandoned (they end on the client's own read timeout) and the page
    renders. Only Engine reads run in the workers; errors are logged here, after the wait."""
    found: dict[str, TurnTrace | str] = {}
    if not operation_ids:
        return found
    executor = ThreadPoolExecutor(max_workers=TRACE_WORKERS)
    try:
        futures: dict[str, Future[TurnTrace]] = {
            op_id: executor.submit(read_turn_trace, client, op_id) for op_id in operation_ids
        }
        wait(futures.values(), timeout=TRACE_DEADLINE_S)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    for op_id, future in futures.items():
        if not future.done() or future.cancelled():
            found[op_id] = TIMEOUT
            continue
        exc = future.exception()
        if isinstance(exc, EngineClientError):
            if request is not None:
                log_engine_error(request, exc)
            found[op_id] = read_failure(exc)
        elif exc is not None:
            raise exc
        else:
            trace = future.result()
            note_engine_record(trace.operation)  # #257: on the request thread, never a worker
            found[op_id] = trace
    return found


@dataclass(frozen=True)
class Traces:
    legend: bool = False
    classification_legend: bool = False  # any row's trace is classified (#207)
    cap_note: str = ""
    failures: str = ""


def _with_traces(
    client: EngineClient,
    request: HttpRequest,
    story: Story,
    groups: list[TurnGroup],
    *,
    load: bool,
) -> Traces:
    """Give each structured beat its trace (#147). The registry is read once when the page has
    a structured beat; with ``load``, each structured generated turn's trace too (the first
    :data:`TRACE_TURNS_MAX`, in page order)."""
    items = [row.item for group in groups for row in group.rows]
    if not any(is_structured(i) for i in items):
        return Traces()
    names = predicate_names(request, client, story.id)
    cast = cast_names(story.cast)
    operations: dict[str, str] = {}  # op id → turn label, page order
    for item in items:
        if is_structured(item) and item.origin == "generated":
            operations.setdefault(item.operation_id, turn_label(item.turn, item.variant))
    wanted = list(operations)[:TRACE_TURNS_MAX] if load else []
    read = turn_traces(client, wanted, request=request)
    for group in groups:
        group.rows = [
            replace(row, trace=_trace(row.item, read, story.id, cast, names, load=load))
            for row in group.rows
        ]
    failed = [f"{operations[op]} ({why})" for op, why in read.items() if isinstance(why, str)]
    classified = any(
        row.trace is not None and row.trace.classified for group in groups for row in group.rows
    )
    return Traces(
        legend=True,
        classification_legend=classified,
        cap_note=TRACE_CAP_NOTE if load and len(operations) > TRACE_TURNS_MAX else "",
        failures=f"Turn trace unavailable: {', '.join(failed)}" if failed else "",
    )


def _trace(
    item: HistoryItem,
    read: Mapping[str, TurnTrace | str],
    story_id: str,
    cast: Mapping[str, str],
    names: Mapping[str, str],
    *,
    load: bool,
) -> BeatTrace | None:
    found = read.get(item.operation_id)
    trace = found if isinstance(found, TurnTrace) else None
    state = UNAVAILABLE if isinstance(found, str) else NOT_LOADED
    return beat_trace(
        item,
        trace,
        state,
        story_id=story_id,
        cast_names=cast,
        predicate_names=names,
        open_trace=load,
    )


def _story_structured(
    client: EngineClient, request: HttpRequest, story_id: str
) -> Callable[[], bool]:
    """Whether the story runs the structured pipeline, read from its effective config on the
    first call only (#181); a failed read logs and answers ``False``."""
    found: list[bool] = []

    def structured() -> bool:
        if not found:
            try:
                found.append(
                    is_structured_pipeline(client.get_effective_config(story_id).effective)
                )
            except EngineClientError as exc:
                log_engine_error(request, exc)
                found.append(False)
        return found[0]

    return structured


def _with_explain(
    story_id: str, groups: list[TurnGroup], story_structured: Callable[[], bool]
) -> None:
    """Give each structured turn whose input the page shows its "Explain retrieval" link (#142),
    built from the page's items. ``story_structured`` reads the effective config, once, only for
    a turn whose shown items don't say (a do-only input with no beat in view, #181)."""
    for group in groups:
        rows = [row.item for row in group.rows]
        found = explain_query(rows, group.turn, group.variant, story_structured)
        group.explain_url = explain_url(story_id, found) if found is not None else None


def _with_perception(
    story_id: str,
    groups: list[TurnGroup],
    story_structured: Callable[[], bool] | None,
    variant: str | None,
) -> None:
    """Give each structured turn ≥ 1 its "Perception" link (#195), carrying the page's chain.
    ``story_structured`` (active-chain pages only; the same lazy read as Explain's) decides a
    turn whose shown items don't."""
    for group in groups:
        rows = [row.item for row in group.rows]
        if turn_links_perception(rows, group.turn, story_structured):
            group.perception_url = perception_url(story_id, group.turn, variant)


def _with_notices(
    request: HttpRequest,
    story: Story,
    groups: list[TurnGroup],
    labels: Mapping[str, str],
    *,
    chain: bool,
) -> None:
    """Give each turn its co-author notice cards (#185), named by the page's labels (C4). Fix
    / Clarify is offered only on the story's latest turn on the active chain (the story read's
    head), greyed on a chain view; Fix and Override return to this page. Overrides come from
    the session."""
    overridden = overridden_notices(request)
    latest = story.next_turn_index - 1
    for group in groups:
        cards = notice_cards(
            group.notices_raw,
            story.id,
            labels,
            overridden=overridden,
            latest=latest,
            chain=chain,
            next_url=request.get_full_path(),
        )
        group.notices = cards or None


def _with_report(story_id: str, groups: list[TurnGroup]) -> None:
    """Give each turn ≥ 1 whose chosen item is generated its "Report" link (#203): the play
    page's inspector for that item, where the Composition shows when the Console logged the
    turn (whichever chain the page shows)."""
    for group in groups:
        found = report_item([row.item for row in group.rows]) if group.turn >= 1 else None
        group.report_url = inspector_url(story_id, found) if found is not None else None


def _with_feedback(groups: list[TurnGroup], widget: Callable[[str], FeedbackWidget]) -> None:
    """Give the first rendered part of each generated message its "Rate" control (#83)."""
    rows = [row for group in groups for row in group.rows]
    marks = iter(first_parts(row.item for row in rows))
    for group in groups:
        group.rows = [
            replace(row, feedback=widget(row.item.message_id)) if next(marks) else row
            for row in group.rows
        ]


# --- As originally recorded (#304) --------------------------------------------------------------


def _checkpoint_refusal(exc: EngineError, checkpoint: str | None) -> str:
    """The text shown beside Checkpoint for a refusal of it, else ""."""
    if exc.code == CHECKPOINT_NOT_FOUND_CODE:
        if not checkpoint:
            return NO_CHECKPOINT_YET
        return CHECKPOINT_NOT_FOUND.format(checkpoint=checkpoint_text(checkpoint))
    if exc.code == CHECKPOINT_INCOMPLETE_CODE:
        return CHECKPOINT_INCOMPLETE
    if exc.code == VALIDATION_ERROR_CODE:
        return _as_of_message(exc)
    return ""


def _as_of_message(exc: EngineError) -> str:
    """A ``validation_error`` on ``query.as_of``: the failure's message (its ``field``, or a
    ``loc`` list, ending in ``as_of``), else the error's; "" when no failure names it."""
    for failure in engine_field_errors(exc):
        if failure.path.split(".")[-1] == "as_of":
            return failure.message or exc.message
    listed = exc.details.get("errors") if isinstance(exc.details, Mapping) else None
    for entry in listed if isinstance(listed, list) else []:
        loc = entry.get("loc") if isinstance(entry, Mapping) else None
        if isinstance(loc, list) and loc and str(loc[-1]) == "as_of":
            message = entry.get("message", entry.get("msg"))
            return message if isinstance(message, str) and message else exc.message
    return ""


def _back_url(
    request: HttpRequest, story_id: str, checkpoint: str | None
) -> tuple[str, int | None]:
    """ "See turn <N> corrected": this page with ``view`` and ``checkpoint`` dropped, and
    ``as_of`` the checkpoint's turn (turn N for ``N+k``; none for the latest). ``variant`` and
    the carried corrected controls are kept."""
    parsed = parse_boundary(checkpoint)
    turn = parsed[0] if parsed is not None else None
    params = _with(
        request,
        view=None,
        checkpoint=None,
        before_turn=None,
        as_of=None if turn is None else str(turn),
    )
    return _url(story_id, params), turn


def _as_recorded_page(
    client: EngineClient, request: HttpRequest, story: Story, form: HistoryForm
) -> Page:
    """The history as originally recorded (#304): one history read, rendered read-only."""
    query = form.query()
    checkpoint = query["as_of"]
    variant = query["variant"] or ""
    try:
        history = client.get_history(story.id, **query)
    except EngineClientError as exc:
        if is_held(exc) or is_importing(exc):
            raise  # #87's held page and E10's importing page (the middleware)
        log_engine_error(request, exc)
        if variant and isinstance(exc, EngineError) and exc.code == PRUNED_MEMBERSHIP:
            clear = _url(story.id, _with(request, variant=None))
            return Page(chain=variant, clear_chain_url=clear, pruned_note=PRUNED_CHAIN_NOTE)
        if isinstance(exc, EngineError):
            beside = _checkpoint_refusal(exc, checkpoint)
            if beside:
                return Page(as_recorded=True, as_recorded_error=beside, chain=variant)
            bug = {
                OMNISCIENT_REQUIRED_CODE: OMNISCIENT_REQUIRED_TEXT,
                INVALID_VIEW_CODE: INVALID_VIEW_TEXT,
            }
            if exc.code in bug:
                return Page(
                    as_recorded=True, error=panel_for(exc), error_text=bug[exc.code], chain=variant
                )
        return Page(as_recorded=True, error=panel_for(exc), chain=variant)
    labels = cast_helpers.character_labels(story.cast)
    items, audit_items = split_audit(history)
    links = operation_links(story.id, [i.operation_id for i in [*items, *audit_items]])
    records = [audit_record(i, {}) for i in audit_items]  # policy ids bare, by design
    groups = turn_groups(
        history,
        items,
        records,
        labels=labels,
        links=links,
        actor_filtered=False,
        omniscient=True,
        story_id=story.id,
    )
    erasures = erasure_rows(history.erasure_markers)
    back_url, back_turn = _back_url(request, story.id, checkpoint)
    return Page(
        history=history,
        viewpoint_label=cast_helpers.viewpoint_label(cast_helpers.OMNISCIENT, story.cast),
        groups=groups,
        chain=variant,
        clear_chain_url=_url(story.id, _with(request, variant=None)) if variant else "",
        as_recorded=True,
        checkpoint_text=checkpoint_text(checkpoint, history.as_of),
        erased_since=erased_since(len(erasures)) if erasures else "",
        erasures=erasures,
        derived=derived_rows(history.derived),
        back_url=back_url,
        back_turn=back_turn,
    )


@require_safe
def history(request: HttpRequest, story_id: str) -> HttpResponse:
    client = engine_client_for(request)
    story = client.get_story(story_id)
    htmx = is_htmx(request)
    form = HistoryForm(switched_view(request.GET) if htmx else request.GET, cast=story.cast)
    if htmx:
        chains = _variants(client, request, story_id)[0] if form.value("variant") else []
        controls = _controls(story, form, chains, show_errors=False)
        return render(request, CONTROLS_TEMPLATE, {"controls": controls})
    chains, variants_error = _variants(client, request, story_id)
    page = None
    if form.is_valid():
        build = _as_recorded_page if form.as_recorded else _page
        page = build(client, request, story, form)
    checkpoint_error = page.as_recorded_error if page is not None else ""
    context = {
        "story": story,
        "controls": _controls(story, form, chains, checkpoint_error=checkpoint_error),
        "page": page,
        "chains": _chain_rows(request, story, chains),
        "variants_error": variants_error,
        "archived_note": ARCHIVED_NOTE if story.status == "archived" else "",
        "paused_note": PAUSED_NOTE if is_paused(story) else "",
        "pause_banner": pause_banner(story),  # #80
        "page_links": story_links(story.id, None),  # E11 (#296): no Engine read
    }
    return render(request, PAGE_TEMPLATE, context)
