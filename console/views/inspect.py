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

from console.engine.client import EngineClient, EngineClientError
from console.engine.models import EffectiveConfig, Story
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.held import is_held, render_held
from console.inspect import (
    CAPABILITY_MAP_LABEL,
    CAPABILITY_MAP_URL,
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
    setting,
    tab_availability,
)

OVERVIEW_TEMPLATE = "console/inspect/overview.html"


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
