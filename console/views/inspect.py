"""A story's Inspect area (#135): the shell every Inspect tab renders in.

**The extension point.** A tab's view is one call::

    @require_safe
    def memory(request: HttpRequest, story_id: str) -> HttpResponse:
        return inspect_page(request, story_id, Tab.MEMORY, "console/inspect/memory.html", build)

where ``build(ctx: InspectContext) -> dict`` makes the tab's own reads and returns its context,
and the template extends ``console/inspect/base.html`` and fills ``{% block tab_content %}``. The
tab registers its URL in ``epistrel_console/urls.py``'s ``inspect_patterns`` as
``<tab value>/`` named ``<tab value>``; until it does, the bar leaves the tab out (#64's nav rule:
no stub pages).

:func:`inspect_page`, for every tab:

1. reads ``GET /stories/{id}/effective-config``, then ``GET /stories/{id}`` (sequential; the
   story read runs even when the first failed, so the header keeps the name; any failure of it,
   ``erasure_pending`` included, degrades the name to the story id);
2. a 503 ``erasure_pending`` on the effective-config read is #87's held wrapper; a 404 when the
   story read 404s too is convention 10's "Not found (it may have been deleted or truncated)"
   (#177); any other failure is #43's error panel. Either replaces the tab bar and the tab, and
   ``build`` is not called;
3. computes :func:`console.inspect.tab_availability`. A greyed tab renders the greyed panel and
   makes no other read (``build`` is not called);
4. otherwise calls ``build`` (whose context may name another template under
   :data:`TEMPLATE_KEY`, or which may answer an ``HttpResponse`` of its own, #146). A 409
   ``module_off`` escaping it becomes the greyed panel (via
   :func:`console.inspect.module_off_unavailable`) with "The story's configuration changed since
   this page loaded — reload"; the bar is not recomputed. Any other error escaping ``build``
   goes to the middleware (#43's panel, #87's held wrapper). A page with several reads renders
   each section's own error inside ``build``.
5. on the Knowledge tab (#189), served and not replaced by a 409, the context gains
   ``knowledge_sections`` (:func:`console.inspect.knowledge_sections`, the current section from
   the request's URL name), the sub-navigation of ``console/inspect/knowledge.html``.

Every render answers 200, the held wrapper aside (503). A route that answers empty or
``skipped`` where its module is off renders the same reason inline:
``{% include "console/inspect/_unavailable.html" with unavailable=… inline=True %}``
(``config_url`` is in every Inspect context).

No polling and no HTMX: one static read per page load, full navigations between tabs.
The one HTMX endpoint here is :func:`turn_trace` (#139), the lazy block the turn inspector and
the #44 detail page load when the operator opens it; it is not a tab.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from console.cast import OMNISCIENT
from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import EffectiveConfig, OmniscientStoryCharacter, Story
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import QUESTION_FIELDS, ConsolidateForm, RetrievalForm
from console.held import held_context, is_held, render_held
from console.hot_set import (
    FRACTION_UNAVAILABLE,
    REASON_LEGEND,
    browse_rows,
    budget_rows,
    episode_rows,
    hot_set_rows,
    minor_share_row,
    more,
    retirement_rows,
)
from console.ids import new_ulid
from console.inspect import (
    CAPABILITY_MAP_LABEL,
    CAPABILITY_MAP_URL,
    CONSOLIDATION,
    EPISODIC,
    PIPELINE,
    SETTINGS,
    Tab,
    Unavailable,
    capabilities,
    history_turn_url,
    is_module_off,
    knowledge_sections,
    module_off_unavailable,
    module_rows,
    overview_fingerprint,
    position_text,
    profile_flags,
    retrieval_url,
    setting,
    tab_availability,
    unavailable_for,
)
from console.retrieval import (
    KEEP_FIELDS,
    Filters,
    Names,
    candidates,
    episodic_skipped,
    legend,
    mention_rows,
    placed,
    time_view,
)
from console.retrieval import more as retrieval_more
from console.turn_trace import (
    COMMITTED,
    TURN_KIND,
    TurnTrace,
    read_turn_trace,
    trace_view,
    withheld,
)
from console.wording import minor_ids

OVERVIEW_TEMPLATE = "console/inspect/overview.html"
MEMORY_TEMPLATE = "console/inspect/memory.html"
RETRIEVAL_TEMPLATE = "console/inspect/retrieval.html"
MEMORY_ACTIONS_TEMPLATE = "console/inspect/_memory_actions.html"
OMNISCIENT_REQUIRED = "omniscient_required"


@dataclass(frozen=True)
class InspectContext:
    """What a tab's ``build`` gets: the request, its Engine client, and the page's two reads (the
    effective configuration, and the story or ``None`` when that read failed)."""

    request: HttpRequest
    client: EngineClient
    story_id: str
    tab: Tab
    config: EffectiveConfig
    story: Story | None
    story_name: str
    # Why ``story`` is ``None`` (the story read's failure), for a tab that maps it (#58: a 404
    # is the story-level not-found).
    story_error: EngineClientError | None = None

    @property
    def effective(self) -> dict[str, Any]:
        return self.config.effective


Build = Callable[[InspectContext], Mapping[str, Any] | HttpResponse]
# A build's context may name another template under this key (#146: a fact write's result page
# renders in place of its form, on the POST response); a build may also answer a response of
# its own (a redirect).
TEMPLATE_KEY = "inspect_template"


@dataclass(frozen=True)
class TabLink:
    """One tab in the bar: a link, or (``unavailable`` set) a greyed non-link."""

    tab: Tab
    url: str
    active: bool
    unavailable: Unavailable | None

    @property
    def label(self) -> str:
        return self.tab.label


def tab_links(
    story_id: str, current: Tab, availability: Mapping[Tab, Unavailable | None]
) -> list[TabLink]:
    """The bar: every tab whose URL is registered, in :class:`Tab` order."""
    links = []
    for tab in Tab:
        try:
            url = reverse(tab.url_name, args=[story_id])
        except NoReverseMatch:
            continue  # that tab's story has not landed yet
        links.append(TabLink(tab, url, tab == current, availability.get(tab)))
    return links


def _is_404(answer: object) -> bool:
    return isinstance(answer, EngineError) and answer.http_status == 404


def _story(request: HttpRequest, client: EngineClient, story_id: str) -> Story | EngineClientError:
    try:
        return client.get_story(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return exc


def inspect_page(
    request: HttpRequest, story_id: str, tab: Tab, template: str, build: Build
) -> HttpResponse:
    """Render Inspect tab ``tab`` of story ``story_id`` with ``template`` (see the module
    docstring for the reads, the gate and the error handling)."""
    client = engine_client_for(request)
    try:
        config: EffectiveConfig | EngineClientError = client.get_effective_config(story_id)
    except EngineClientError as exc:
        if is_held(exc):
            return render_held(request, story_id, exc)
        log_engine_error(request, exc)
        config = exc
    read = _story(request, client, story_id)
    story = read if isinstance(read, Story) else None
    story_name = story.name if story is not None and story.name else story_id
    context: dict[str, Any] = {
        "story_id": story_id,
        "story_name": story_name,
        "story": story,
        "tab": tab,
        "story_url": reverse("stories:detail", args=[story_id]),
        "config_url": reverse("stories:config", args=[story_id]),
        "reload_url": request.get_full_path(),
    }
    if isinstance(config, EngineClientError):
        if _is_404(config) and _is_404(read):
            context["story_not_found"] = True  # convention 10: the story is gone (#177)
        else:
            context["engine_error"] = panel_for(config)
        return render(request, template, context)
    availability = tab_availability(config.effective)
    context.update(config=config, tabs=tab_links(story_id, tab, availability))
    unavailable = availability.get(tab)
    if unavailable is not None:
        context["unavailable"] = unavailable
        return render(request, template, context)
    story_error = read if isinstance(read, EngineClientError) else None
    ctx = InspectContext(request, client, story_id, tab, config, story, story_name, story_error)
    try:
        built = build(ctx)
    except EngineClientError as exc:
        if not is_module_off(exc):
            raise
        log_engine_error(request, exc)
        context["unavailable"] = module_off_unavailable(exc, tab, config.effective)
        context["config_changed"] = True
        return render(request, template, context)
    if isinstance(built, HttpResponse):
        return built
    if tab is Tab.KNOWLEDGE:
        match = request.resolver_match
        current = match.url_name if match is not None else None
        context["knowledge_sections"] = knowledge_sections(story_id, current)
    context.update(built)
    return render(request, context.pop(TEMPLATE_KEY, template), context)


def _overview(ctx: InspectContext) -> dict[str, Any]:
    """The Overview (#151), from the shell's effective-config read alone (no other Engine read):
    the header line, the reported capabilities, the module table and the capability-map link."""
    config = ctx.config
    return {
        "profile_flags": profile_flags(config),
        "fingerprint_short": overview_fingerprint(config.fingerprint),
        "pipeline": setting(ctx.effective, SETTINGS[PIPELINE]),
        "capabilities": capabilities(config),
        "modules": module_rows(ctx.effective),
        "capability_map_url": CAPABILITY_MAP_URL,
        "capability_map_label": CAPABILITY_MAP_LABEL,
    }


@require_safe
def overview(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:overview``, the area's entry URL."""
    return inspect_page(request, story_id, Tab.OVERVIEW, OVERVIEW_TEMPLATE, _overview)


KNOWLEDGE_TEMPLATE = "console/inspect/knowledge.html"


def _knowledge(ctx: InspectContext) -> dict[str, Any] | HttpResponse:
    """The Knowledge tab's landing (#189): a redirect to the first registered section (the query
    string dropped), else the empty shell. It runs after the shell's two reads and the gate, so a
    greyed tab renders its panel and never redirects."""
    sections = knowledge_sections(ctx.story_id, None)
    if sections:
        return redirect(reverse(f"inspect:{sections[0].name}", args=[ctx.story_id]))
    return {}


@require_safe
def knowledge(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:knowledge``, the Knowledge tab's entry URL (#189)."""
    return inspect_page(request, story_id, Tab.KNOWLEDGE, KNOWLEDGE_TEMPLATE, _knowledge)


def _memory(ctx: InspectContext) -> dict[str, Any]:
    """The Memory tab (#140): one ``GET /admin/stories/{id}/hot-set`` read, its rows from
    :mod:`console.hot_set` with the shell's cast (``None`` when the story read failed).

    A failed read replaces the "As of" line and the sections: 404 ``not_found`` with convention
    10's line, any other error with #43's panel; ``erasure_pending`` (and a 409 ``module_off``,
    which the route doesn't declare) escape to #87's held wrapper (and the shell's panel). The
    Browse grid renders either way. ``episodic: false`` shows #135's reason inline above the
    sections, which still render (convention 7).

    After a successful hot-set read (#168) it reads the omniscient story (the minors' weights)
    then the deployment settings (``minor_hot_budget_fraction``); each failure is logged and
    degrades to a note in ``minor_notes`` (:func:`_minor_budgets`), never failing the tab."""
    browse = browse_rows(ctx.story_id)
    context: dict[str, Any] = {
        "browse": browse,
        "browse_links": all(link.url for row in browse for link in row.links),
    }
    try:
        body = ctx.client.get_hot_set(ctx.story_id)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 404:
            context["hot_set_not_found"] = True
        else:
            context["hot_set_error"] = panel_for(exc)
        context["consolidate"] = consolidate_section(ctx, None)
        return context
    context["consolidate"] = consolidate_section(ctx, body.head_turn)
    cast = ctx.story.cast if ctx.story is not None else None
    minors, fraction, weights_error, fraction_error = _minor_budgets(ctx)
    rows = hot_set_rows(body, cast, minors=minors, fraction=fraction)
    notes = []
    if weights_error is not None:
        notes.append(WEIGHTS_UNAVAILABLE.format(code=error_text(weights_error)))
    # With the weights unknown the story may have minors, so both notes show (#168).
    may_have_minors = weights_error is not None or any(row.minor for row in rows.counts)
    if fraction_error is not None and may_have_minors:
        notes.append(FRACTION_NOTE.format(code=error_text(fraction_error)))
    budgets = budget_rows(body.budgets)
    budgets.insert(1, minor_share_row(body.budgets, fraction))
    context.update(
        hot_set=body,
        episodic_off=None if body.episodic else unavailable_for(EPISODIC, ctx.effective),
        budgets=budgets,
        rows=rows,
        minor_notes=notes,
        fraction_unavailable=FRACTION_UNAVAILABLE,
        episodes=episode_rows(body.open_episodes, body.head_turn, cast),
        retirements=retirement_rows(ctx.story_id, body.recent_retirements, cast),
        reason_legend=REASON_LEGEND,
        more=more(body),
    )
    return context


WEIGHTS_UNAVAILABLE = (
    "Minor characters' budgets can't be shown (weights unavailable: {code}) \u2014 a minor's row "
    "may read under budget while the Engine counts it over"
)
FRACTION_NOTE = (
    "Minor budgets need the deployment's minor_hot_budget_fraction (unavailable: {code})"
)


def _minor_budgets(
    ctx: InspectContext,
) -> tuple[frozenset[str] | None, float | None, EngineClientError | None, EngineClientError | None]:
    """The Memory tab's two extra reads (#168), in order: the omniscient story's minors
    (``None`` when that read failed; an item that doesn't parse counts as major) and the
    deployment's ``minor_hot_budget_fraction`` (``None`` when that read failed), with each
    read's error. Every ``EngineClientError`` (held and ``module_off`` included) degrades."""
    minors: frozenset[str] | None = None
    fraction: float | None = None
    weights_error = fraction_error = None
    try:
        story = ctx.client.get_story(ctx.story_id, viewpoint=OMNISCIENT)
    except EngineClientError as exc:
        log_engine_error(ctx.request, exc)
        weights_error = exc
    else:
        parsed = (OmniscientStoryCharacter.from_json(item) for item in story.characters)
        minors = minor_ids(member for member in parsed if member is not None)
    try:
        fraction = ctx.client.get_deployment_settings().minor_hot_budget_fraction
    except EngineClientError as exc:
        log_engine_error(ctx.request, exc)
        fraction_error = exc
    return minors, fraction, weights_error, fraction_error


@dataclass(frozen=True)
class ConsolidateSection:
    """The Memory tab's "Consolidate a window" section (#145). ``unavailable`` (the
    ``consolidation`` capability is off: #135's greyed note) wins over ``no_turns`` (head 0);
    otherwise the GET ``form`` to ``url`` with the key minted at this render. The two
    Retrieval links (convention 11: pre-filled, no read; ``None`` while that tab isn't
    registered) show in every state."""

    unavailable: Unavailable | None
    no_turns: bool
    form: ConsolidateForm | None
    url: str
    idempotency_key: str
    semantic_url: str | None
    reflection_url: str | None


def consolidate_section(ctx: InspectContext, head: int | None) -> ConsolidateSection:
    """The section for ``head`` (the hot set's ``head_turn``; ``None`` when that read failed:
    the form then has no default "to" and no past-head check). The gate is the effective
    config's ``capabilities`` (the Engine's own ``module_on``)."""
    off = None if CONSOLIDATION in ctx.config.capabilities else unavailable_for(CONSOLIDATION)
    no_turns = off is None and head == 0
    form = None
    if off is None and not no_turns:
        initial: dict[str, Any] = {"from_turn": "1"}
        if head is not None:
            initial.update(to_turn=str(head), head=str(head))
        form = ConsolidateForm(initial=initial)
    return ConsolidateSection(
        unavailable=off,
        no_turns=no_turns,
        form=form,
        url=reverse("inspect:consolidate", args=[ctx.story_id]),
        idempotency_key=new_ulid(),
        semantic_url=retrieval_url(ctx.story_id, {"kind": "semantic"}),
        reflection_url=retrieval_url(ctx.story_id, {"kind": "reflection"}),
    )


@require_safe
def memory(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:memory``, the story's hot set (#140)."""
    return inspect_page(request, story_id, Tab.MEMORY, MEMORY_TEMPLATE, _memory)


def _head(story: Story | None) -> int | None:
    return story.next_turn_index - 1 if story is not None else None


def _actor_label(form: RetrievalForm, names: Names) -> str:
    """The results header's "As …": the asked actor's cast label (the player marked), its id
    when the cast doesn't name it, or the player default when the cast is unknown."""
    actor = form.cleaned_data.get("actor") or form.default_actor
    if not actor:
        return "the player (default)"
    return names.label(actor) or actor


@dataclass(frozen=True)
class ExplainBanner:
    """#142's reconstruction banner: the re-run of turn ``turn``'s retrieval, as ``actor_label``
    (the actor actually asked), linked back to the turn in history."""

    turn: int
    position: str  # "turn 7", "turn 7 · v2" (convention 4)
    actor_label: str
    history_url: str


def explain_banner(
    cleaned_data: Mapping[str, Any], story_id: str, actor_label: str
) -> ExplainBanner | None:
    """The banner when the question re-runs a past turn: ``explain`` a turn ≥ 1, ``as_of`` still
    the turn before it and a question asked. It checks only ``explain`` against ``as_of`` (the
    link carries no fingerprint of the input, so an edited ``q`` or actor keeps it). An as_of
    the Engine refused (a 422 on ``query.as_of``) is gone from ``cleaned_data``: no banner."""
    turn = cleaned_data.get("explain")
    as_of = cleaned_data.get("as_of")
    if not isinstance(turn, int) or as_of != turn - 1 or not cleaned_data.get("q"):
        return None
    variant = cleaned_data.get("explain_variant")
    variant = variant if isinstance(variant, int) else None
    return ExplainBanner(
        turn,
        position_text(turn, None, variant) or f"turn {turn}",
        actor_label,
        history_turn_url(story_id, turn, variant),
    )


def _retrieval(ctx: InspectContext) -> dict[str, Any]:
    """The Retrieval tab (#141) and, for a past turn's re-run, #142's reconstruction banner
    (:func:`explain_banner`) above whatever the read answered."""
    cast = ctx.story.cast if ctx.story is not None else None
    head = _head(ctx.story)
    form = RetrievalForm(ctx.request.GET, cast=cast, head=head)
    names = Names(cast)
    context: dict[str, Any] = {
        "form": form,
        "clear_url": retrieval_url(ctx.story_id, {}),
        "head": head,
        "legend": legend(ctx.effective),
    }
    if "q" not in ctx.request.GET:
        form.filters_only()
        context["prompt"] = True
        return context
    _ask(ctx, form, names, context)
    context["explain_banner"] = explain_banner(
        form.cleaned_data, ctx.story_id, _actor_label(form, names)
    )
    return context


def _ask(ctx: InspectContext, form: RetrievalForm, names: Names, context: dict[str, Any]) -> None:
    """The question (#141): at most one ``GET /stories/{id}/retrieve`` read
    (``viewpoint=omniscient``) and its Resolution and Candidates sections, into ``context``.

    No read for a request without ``q`` (a filter-only link, convention 11: the form pre-filled
    and the prompt) or with an invalid form. A 422 lands on the field ``details.field`` names or
    above the form; 403 ``omniscient_required`` and 404 render convention 10's lines, any other
    failure #43's panel, each below the form; ``erasure_pending`` (and a 409 ``module_off``)
    escape to #87's held wrapper (and the shell's panel). ``skipped: ["episodic"]`` shows #135's
    reason inline instead of the candidate tables (and of #152's sections). ``placed`` holds
    #152's Canon, Profiles, Context and Run facts from the same answer."""
    if not form.is_valid():
        return
    query = form.query()
    try:
        body = ctx.client.retrieve(ctx.story_id, **query)
    except EngineClientError as exc:
        if is_held(exc) or is_module_off(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            form.apply_engine_error(exc)
        elif isinstance(exc, EngineError) and exc.code == OMNISCIENT_REQUIRED:
            context["omniscient_required"] = True
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["retrieval_not_found"] = True
        else:
            context["retrieval_error"] = panel_for(exc)
        return
    keep = {name: form.value(name) for name in QUESTION_FIELDS if form.value(name)}
    back = {name: form.value(name) for name in KEEP_FIELDS if form.value(name)}
    filters = Filters(
        form.filter_value("kind"), form.filter_value("tier"), form.filter_value("memory")
    )
    segments, mentions = mention_rows(query["q"], body.resolution.mentions, names)
    context.update(
        retrieval=body,
        actor_label=_actor_label(form, names),
        as_of=query["as_of"],
        participants=[names.person(p) for p in body.participants],
        segments=segments,
        mentions=mentions,
        time=time_view(body.resolution),
        episodic_off=unavailable_for(EPISODIC, ctx.effective) if episodic_skipped(body) else None,
        candidates=candidates(ctx.story_id, body, names, filters, keep),
        placed=placed(ctx.story_id, body, names, back),
        actions=MEMORY_ACTIONS_TEMPLATE,  # convention 14: #143's row actions
        actions_next=ctx.request.get_full_path(),  # where a row action returns
        more=retrieval_more(body),
    )


@require_safe
def retrieval(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:retrieval``, the retrieval explorer (#141)."""
    return inspect_page(request, story_id, Tab.RETRIEVAL, RETRIEVAL_TEMPLATE, _retrieval)


# --- A turn's eval hooks and read set (#139) ----------------------------------------------------

TURN_TRACE_TEMPLATE = "console/_turn_trace.html"
NOT_FOUND = "not_found"


def _trace_state(story_id: str, trace: TurnTrace) -> str:
    """The partial's state, in #139's check order after the read: story mismatch, not a turn,
    withheld, not committed, no hooks recorded, else ``hooks``."""
    record = trace.operation
    if record.story_id != story_id:
        return NOT_FOUND
    if record.kind != TURN_KIND:
        return "not_turn"
    if trace.withheld_for is not None:
        return "withheld"
    if record.status != COMMITTED:
        return "not_committed"
    return "no_hooks" if trace.eval_hooks is None else "hooks"


def _settings_window(request: HttpRequest, client: EngineClient) -> int | None:
    """The deployment's ``idempotency_result_window`` (one settings read, on the withheld path
    only); ``None`` when the read fails (the note then assumes the default 24 h)."""
    try:
        return client.get_deployment_settings().idempotency_result_window
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


@require_safe
def turn_trace(request: HttpRequest, story_id: str, engine_operation_id: str) -> HttpResponse:
    """``inspect:turn-trace``: the turn inspector's and the #44 detail page's lazy "Eval hooks &
    read set" block (a bare fragment, with or without ``HX-Request``). Every state answers 200,
    errors included (convention 10: 404 → not found, 503 ``erasure_pending`` → #87's held
    partial, others → #43's panel). The reads aren't recorded in #44."""
    client = engine_client_for(request)
    context: dict[str, Any] = {"story_id": story_id}
    try:
        trace = read_turn_trace(client, engine_operation_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if is_held(exc):
            retry = reverse("stories:detail", args=[story_id])
            context.update(state="held", **held_context(story_id, exc.retry_after, retry))
        elif isinstance(exc, EngineError) and exc.http_status == 404:
            context["state"] = NOT_FOUND
        else:
            context.update(state="error", engine_error=panel_for(exc))
        return render(request, TURN_TRACE_TEMPLATE, context)
    state = _trace_state(story_id, trace)
    context.update(state=state, operation=trace.operation)
    if state in (NOT_FOUND, "not_turn"):
        return render(request, TURN_TRACE_TEMPLATE, context)
    if state == "withheld":
        hours = _settings_window(request, client)
        context["withheld"] = withheld(trace, hours, timezone.now())
    context["trace"] = trace_view(story_id, trace)
    return render(request, TURN_TRACE_TEMPLATE, context)
