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
- ``GET /stories/{id}/settings`` — only when audit records are shown, for policy names.

An HTMX request (the viewpoint select's ``change``) gets the controls fragment alone, so the
audit box enables and disables live; without JavaScript the form refuses audit server-side.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_safe

from console import cast as cast_helpers
from console.engine.client import EngineClient, EngineClientError
from console.engine.models import History, Story, SummarySegment, VariantChain
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.feedback import FeedbackWidget, collapsed, first_parts
from console.forms.history import HistoryForm, as_of_options
from console.history import (
    TurnGroup,
    audit_record,
    chain_key,
    chain_tip,
    explain_query,
    explain_url,
    operation_links,
    policy_names,
    split_audit,
    summary_rows,
    turn_groups,
)
from console.story_pause import is_paused, pause_banner
from console.timeline import ARCHIVED_NOTE, PAUSED_NOTE, Basis, Link

CONTROLS_TEMPLATE = "console/history/_controls.html"
PAGE_TEMPLATE = "console/history/history.html"


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


@dataclass(frozen=True)
class ChainRow:
    chain: VariantChain
    key: str
    view_url: str
    parent: str
    swap: Link | None = None  # every chain but the active one (#53)
    fork: Link | None = None  # fork along this chain, through its tip (#53)


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
    settings_error: ErrorPanel | None = None


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
    story: Story, form: HistoryForm, chains: list[VariantChain], *, show_errors: bool = True
) -> Controls:
    head = max(story.next_turn_index - 1, 0)
    variant = form.value("variant")
    tip = chain_tip(chains, variant) if variant else None
    last = tip if tip is not None else head
    url = reverse("stories:history", args=[story.id])
    form.fields["viewpoint"].widget.attrs.update(
        {
            "hx-get": url,
            "hx-trigger": "change",
            "hx-target": "#history-controls",
            "hx-swap": "outerHTML",
            "hx-include": "closest form",
        }
    )
    form.fields["as_of"].widget.attrs.update(
        {"list": "as-of-turns", "placeholder": f"head ({last})"}
    )
    return Controls(
        story.id,
        form,
        head,
        last,
        as_of_options(last),
        audit_disabled=not form.omniscient,
        show_errors=show_errors,
    )


def _chain_rows(request: HttpRequest, story: Story, chains: list[VariantChain]) -> list[ChainRow]:
    """Each chain with its "view" link (``viewpoint`` and ``limit`` kept, ``variant`` set) and
    its Swap (not on the active chain; ``&check=1`` when it ``requires_check``) and Fork links,
    carrying the story read's revision and head (#53)."""
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
        for name in ("viewpoint", "limit"):
            if request.GET.get(name):
                params[name] = request.GET[name]
        params["variant"] = key
        parent = f"{chain.parent.turn}:{chain.parent.variant}" if chain.parent else "—"
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
        return Page(error=panel_for(exc), chain=query["variant"] or "")
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
    )
    _with_feedback(groups, collapsed(story.id, "transcript", request.get_full_path()))
    variant = query["variant"] or ""
    if not variant:  # a chain view isn't the active timeline (#142)
        _with_explain(story.id, groups)
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
    )


def _with_explain(story_id: str, groups: list[TurnGroup]) -> None:
    """Give each structured turn whose input the page shows its "Explain retrieval" link (#142),
    built from the page's items only (zero Engine reads)."""
    for group in groups:
        found = explain_query([row.item for row in group.rows], group.turn, group.variant)
        group.explain_url = explain_url(story_id, found) if found is not None else None


def _with_feedback(groups: list[TurnGroup], widget: Callable[[str], FeedbackWidget]) -> None:
    """Give the first rendered part of each generated message its "Rate" control (#83)."""
    rows = [row for group in groups for row in group.rows]
    marks = iter(first_parts(row.item for row in rows))
    for group in groups:
        group.rows = [
            replace(row, feedback=widget(row.item.message_id)) if next(marks) else row
            for row in group.rows
        ]


@require_safe
def history(request: HttpRequest, story_id: str) -> HttpResponse:
    client = engine_client_for(request)
    story = client.get_story(story_id)
    form = HistoryForm(request.GET, cast=story.cast)
    if is_htmx(request):
        chains = _variants(client, request, story_id)[0] if form.value("variant") else []
        controls = _controls(story, form, chains, show_errors=False)
        return render(request, CONTROLS_TEMPLATE, {"controls": controls})
    chains, variants_error = _variants(client, request, story_id)
    page = _page(client, request, story, form) if form.is_valid() else None
    context = {
        "story": story,
        "controls": _controls(story, form, chains),
        "page": page,
        "chains": _chain_rows(request, story, chains),
        "variants_error": variants_error,
        "archived_note": ARCHIVED_NOTE if story.status == "archived" else "",
        "paused_note": PAUSED_NOTE if is_paused(story) else "",
        "pause_banner": pause_banner(story),  # #80
    }
    return render(request, PAGE_TEMPLATE, context)
