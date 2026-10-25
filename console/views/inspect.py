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
2. a 503 ``erasure_pending`` on the effective-config read is #87's held wrapper; any other
   failure is #43's error panel in place of the tab bar and the tab, and ``build`` is not called;
3. computes :func:`console.inspect.tab_availability`. A greyed tab renders the greyed panel and
   makes no other read (``build`` is not called);
4. otherwise calls ``build``. A 409 ``module_off`` escaping it becomes the greyed panel (via
   :func:`console.inspect.module_off_unavailable`) with "The story's configuration changed since
   this page loaded — reload"; the bar is not recomputed. Any other error escaping ``build``
   goes to the middleware (#43's panel, #87's held wrapper). A page with several reads renders
   each section's own error inside ``build``.

Every render answers 200, the held wrapper aside (503). A route that answers empty or
``skipped`` where its module is off renders the same reason inline:
``{% include "console/inspect/_unavailable.html" with unavailable=… inline=True %}``
(``config_url`` is in every Inspect context).

No polling and no HTMX: one static read per page load, full navigations between tabs.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_safe

from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import EffectiveConfig, Story
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.forms.inspect import QUESTION_FIELDS, RetrievalForm
from console.held import is_held, render_held
from console.hot_set import (
    REASON_LEGEND,
    browse_rows,
    budget_rows,
    episode_rows,
    hot_set_rows,
    more,
    retirement_rows,
)
from console.inspect import (
    CAPABILITY_MAP_LABEL,
    CAPABILITY_MAP_URL,
    EPISODIC,
    PIPELINE,
    SETTINGS,
    Tab,
    Unavailable,
    capabilities,
    is_module_off,
    module_off_unavailable,
    module_rows,
    overview_fingerprint,
    pipeline_dials,
    profile_flags,
    retrieval_url,
    setting,
    tab_availability,
    unavailable_for,
)
from console.retrieval import (
    Filters,
    Names,
    candidates,
    episodic_skipped,
    legend,
    mention_rows,
    time_view,
)
from console.retrieval import more as retrieval_more

OVERVIEW_TEMPLATE = "console/inspect/overview.html"
MEMORY_TEMPLATE = "console/inspect/memory.html"
RETRIEVAL_TEMPLATE = "console/inspect/retrieval.html"
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

    @property
    def effective(self) -> dict[str, Any]:
        return self.config.effective


Build = Callable[[InspectContext], Mapping[str, Any]]


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


def _story(request: HttpRequest, client: EngineClient, story_id: str) -> Story | None:
    try:
        return client.get_story(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


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
    story = _story(request, client, story_id)
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
        context["engine_error"] = panel_for(config)
        return render(request, template, context)
    availability = tab_availability(config.effective)
    context.update(config=config, tabs=tab_links(story_id, tab, availability))
    unavailable = availability.get(tab)
    if unavailable is not None:
        context["unavailable"] = unavailable
        return render(request, template, context)
    ctx = InspectContext(request, client, story_id, tab, config, story, story_name)
    try:
        context.update(build(ctx))
    except EngineClientError as exc:
        if not is_module_off(exc):
            raise
        log_engine_error(request, exc)
        context["unavailable"] = module_off_unavailable(exc, tab, config.effective)
        context["config_changed"] = True
    return render(request, template, context)


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
        "dials": pipeline_dials(ctx.effective),
        "capability_map_url": CAPABILITY_MAP_URL,
        "capability_map_label": CAPABILITY_MAP_LABEL,
    }


@require_safe
def overview(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:overview``, the area's entry URL."""
    return inspect_page(request, story_id, Tab.OVERVIEW, OVERVIEW_TEMPLATE, _overview)


def _memory(ctx: InspectContext) -> dict[str, Any]:
    """The Memory tab (#140): one ``GET /admin/stories/{id}/hot-set`` read, its rows from
    :mod:`console.hot_set` with the shell's cast (``None`` when the story read failed).

    A failed read replaces the "As of" line and the sections: 404 ``not_found`` with convention
    10's line, any other error with #43's panel; ``erasure_pending`` (and a 409 ``module_off``,
    which the route doesn't declare) escape to #87's held wrapper (and the shell's panel). The
    Browse grid renders either way. ``episodic: false`` shows #135's reason inline above the
    sections, which still render (convention 7)."""
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
        return context
    cast = ctx.story.cast if ctx.story is not None else None
    context.update(
        hot_set=body,
        episodic_off=None if body.episodic else unavailable_for(EPISODIC, ctx.effective),
        budgets=budget_rows(body.budgets),
        rows=hot_set_rows(body, cast),
        episodes=episode_rows(body.open_episodes, body.head_turn, cast),
        retirements=retirement_rows(ctx.story_id, body.recent_retirements, cast),
        reason_legend=REASON_LEGEND,
        more=more(body),
    )
    return context


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


def _retrieval(ctx: InspectContext) -> dict[str, Any]:
    """The Retrieval tab (#141): the GET form, then at most one ``GET /stories/{id}/retrieve``
    read (``viewpoint=omniscient``) and its Resolution and Candidates sections.

    No read for a request without ``q`` (a filter-only link, convention 11: the form pre-filled
    and the prompt) or with an invalid form. A 422 lands on the field ``details.field`` names or
    above the form; 403 ``omniscient_required`` and 404 render convention 10's lines, any other
    failure #43's panel, each below the form; ``erasure_pending`` (and a 409 ``module_off``)
    escape to #87's held wrapper (and the shell's panel). ``skipped: ["episodic"]`` shows #135's
    reason inline instead of the candidate tables."""
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
    if not form.is_valid():
        return context
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
        return context
    keep = {name: form.value(name) for name in QUESTION_FIELDS if form.value(name)}
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
        actions=None,  # convention 14: #143 sets the row actions partial
        more=retrieval_more(body),
    )
    return context


@require_safe
def retrieval(request: HttpRequest, story_id: str) -> HttpResponse:
    """``inspect:retrieval``, the retrieval explorer (#141)."""
    return inspect_page(request, story_id, Tab.RETRIEVAL, RETRIEVAL_TEMPLATE, _retrieval)
