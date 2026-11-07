"""M7's page frames (E11, #296): every story-level M7 page renders through :func:`story_page`
and every Engine-panel M7 page through :func:`engine_page`. Each is one call per view::

    @require_safe
    def rewrites(request: HttpRequest, story_id: str) -> HttpResponse:
        return story_page(request, story_id, "console/stories/rewrites.html", build)

where ``build(ctx) -> Mapping | HttpResponse`` makes the page's own reads and returns its
context (or a response of its own: a redirect, a POST result, a polling fragment), and the
template extends ``console/stories/page_base.html`` and fills ``{% block story_page %}``
(``console/engine/page_base.html`` and ``{% block engine_page %}`` for the Engine panel). A
``build`` can't swap the template through a context key: a page that needs another template
returns its own ``HttpResponse``.

:func:`story_page`:

1. reads ``GET /stories/{id}``, then ``GET /stories/{id}/effective-config`` (the two reads every
   M7 story page needs: the header, the pause and the status; the pipeline, ``pending_backfill``
   and the quality badge). A 503 ``erasure_pending`` or a 409 ``story_importing`` on either is
   re-raised to ``HeldStoryMiddleware`` / ``ImportingStoryMiddleware`` (#87, E10). A 404 on the
   story read is convention 10's "Not found (it may have been deleted or truncated)" in the
   frame, status 404, with no nav; the config read and ``build`` are skipped. Any other failed
   read is logged and becomes ``ctx.story_error`` / ``ctx.config_error`` (a 404 on the config
   read alone is a ``config_error``); the frame still renders, a failed story read showing the
   id alone with #43's panel under it, and ``build`` still runs (``story=None``).
2. calls ``build(StoryPageContext)``. An :class:`EngineClientError` escaping it is re-raised to
   its middleware when held or importing; anything else is logged and rendered as #43's panel
   (``page_error``) inside the frame, status 200, in place of the page block; an HTMX request
   gets the panel alone.
3. renders with ``story``, ``story_id``, ``story_name`` (the id when unnamed), ``status_text``
   (``console.forms.stories.status_label``), ``page_links`` (:func:`console.nav.story_links`),
   ``pause_banner`` (#80/E10, from the story read; ``base.html`` renders it above the header, so
   the frame never includes it a second time), ``page_label`` and ``reload_url``.

:func:`engine_page` is the same frame with no story: it reads nothing itself. A 503
``erasure_pending`` escaping ``build`` (D17's storyless rule) renders #87's ``_held.html`` inside
the frame (no Delete; Retry is this URL on GET/HEAD, else the page's own GET path), status 503
with ``Retry-After`` copied; on HTMX the fragment alone. Any other error is handled as in
:func:`story_page`.

**Labels.** ``current`` (keyword-only) defaults to the request's URL name and marks its nav row
through :data:`console.nav.PAGE_PARENT`. ``page_label`` is the ``label=`` keyword when given (a
detail page's "Certification …<short id>"), else the marked nav row's label, else the URL name
humanised (``stories:prune-confirm`` → "Prune confirm"). The ``<title>`` is
"<page_label> · <story_name>" ("<page_label> · Engine" on the panel), the ``<h1>`` the story name
("Engine"), and the label an ``<h2>``.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import EffectiveConfig, Story
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.forms.stories import status_label
from console.held import FRAGMENT_TEMPLATE as HELD_FRAGMENT
from console.held import _ledger_url, held_text, is_held, retry_seconds
from console.nav import ENGINE_PAGES, STORY_PAGES, engine_links, row_label, story_links
from console.portability import is_importing
from console.story_pause import pause_banner
from console.wording import humanised

ERROR_FRAGMENT = "console/_engine_error.html"


@dataclass(frozen=True)
class StoryPageContext:
    """What a story page's ``build`` gets: the request, its Engine client, and the frame's two
    reads (each ``None`` with its error when it failed)."""

    request: HttpRequest
    client: EngineClient
    story_id: str
    story: Story | None
    story_name: str
    config: EffectiveConfig | None
    story_error: EngineClientError | None = None
    config_error: EngineClientError | None = None


@dataclass(frozen=True)
class EnginePageContext:
    """What an Engine-panel page's ``build`` gets."""

    request: HttpRequest
    client: EngineClient


StoryBuild = Callable[[StoryPageContext], Mapping[str, Any] | HttpResponse]
EngineBuild = Callable[[EnginePageContext], Mapping[str, Any] | HttpResponse]


def _current(request: HttpRequest, current: str | None) -> str | None:
    if current is None and request.resolver_match is not None:
        return request.resolver_match.view_name
    return current


def page_label(rows: tuple[tuple[str, str], ...], current: str | None, label: str | None) -> str:
    """``label`` when given, else ``current``'s nav row label, else ``current`` humanised (the
    namespace dropped, "-" → " ")."""
    if label is not None:
        return label
    found = row_label(rows, current)
    if found is not None:
        return found
    name = (current or "").rpartition(":")[2]
    return humanised(name.replace("-", " "))


def _passed_on(exc: EngineClientError) -> bool:
    """A held (#87) or importing (E10) story's answer: its middleware renders it."""
    return is_held(exc) or is_importing(exc)


def _is_404(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 404


def _build_error(
    request: HttpRequest, exc: EngineClientError
) -> tuple[ErrorPanel, HttpResponse | None]:
    """#43's panel for an error escaping ``build``, and on HTMX the response of it alone."""
    log_engine_error(request, exc)
    panel = panel_for(exc)
    if is_htmx(request):
        return panel, render(request, ERROR_FRAGMENT, {"engine_error": panel})
    return panel, None


def story_page(
    request: HttpRequest,
    story_id: str,
    template: str,
    build: StoryBuild,
    *,
    current: str | None = None,
    label: str | None = None,
) -> HttpResponse:
    """Render story page ``template`` for ``story_id`` in the shared frame (module docstring)."""
    client = engine_client_for(request)
    current = _current(request, current)
    context: dict[str, Any] = {
        "story_id": story_id,
        "story_name": story_id,
        "story": None,
        "page_label": page_label(STORY_PAGES, current, label),
        "reload_url": request.get_full_path(),
    }
    story: Story | None = None
    story_error: EngineClientError | None = None
    try:
        story = client.get_story(story_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        if _is_404(exc):
            context.update(story_not_found=True, page_links=[])  # convention 10
            return render(request, template, context, status=404)
        story_error = exc
        context["story_error"] = panel_for(exc)
    config: EffectiveConfig | None = None
    config_error: EngineClientError | None = None
    try:
        config = client.get_effective_config(story_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        config_error = exc
    story_name = story.name if story is not None and story.name else story_id
    context.update(
        story=story,
        story_name=story_name,
        status_text=status_label(story.status) if story is not None else "",
        page_links=story_links(story_id, current),
        pause_banner=pause_banner(story) if story is not None else None,
    )
    ctx = StoryPageContext(
        request, client, story_id, story, story_name, config, story_error, config_error
    )
    try:
        built = build(ctx)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        panel, fragment = _build_error(request, exc)
        if fragment is not None:
            return fragment
        context["page_error"] = panel
        return render(request, template, context)
    if isinstance(built, HttpResponse):
        return built
    context.update(built)
    return render(request, template, context)


def _held(request: HttpRequest, exc: EngineError) -> dict[str, str]:
    """#87's held fragment without story links (D17): the text, Retry and the ledger link."""
    retry_url = request.get_full_path() if request.method in ("GET", "HEAD") else request.path
    return {
        "held_text": held_text(exc.retry_after),
        "retry_url": retry_url,
        "ledger_url": _ledger_url(),
    }


def engine_page(
    request: HttpRequest,
    template: str,
    build: EngineBuild,
    *,
    current: str | None = None,
    label: str | None = None,
) -> HttpResponse:
    """Render Engine-panel page ``template`` in the shared frame (module docstring)."""
    client = engine_client_for(request)
    current = _current(request, current)
    context: dict[str, Any] = {
        "page_label": page_label(ENGINE_PAGES, current, label),
        "page_links": engine_links(current),
        "reload_url": request.get_full_path(),
    }
    try:
        built = build(EnginePageContext(request, client))
    except EngineClientError as exc:
        if is_held(exc):
            log_engine_error(request, exc)
            held = _held(request, exc)
            if is_htmx(request):
                response = render(request, HELD_FRAGMENT, held, status=503)
            else:
                response = render(request, template, {**context, "held": held}, status=503)
            seconds = retry_seconds(exc.retry_after)
            if seconds is not None:
                response["Retry-After"] = str(seconds)
            return response
        panel, fragment = _build_error(request, exc)
        if fragment is not None:
            return fragment
        context["page_error"] = panel
        return render(request, template, context)
    if isinstance(built, HttpResponse):
        return built
    context.update(built)
    return render(request, template, context)
