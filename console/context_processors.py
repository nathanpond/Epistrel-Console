"""The shell's Engine badge: one health probe per request, and only when a page shows it (#43).

``engine_badge`` is lazy: the probe runs the first time a template reads it. ``base.html`` reads
it only for a signed-in user, so the login page, the admin, HTMX partials and ``/health`` (JSON)
never probe. A view that already probed (``/``) hands its result over with :func:`remember_probe`,
so the badge and the page agree and the Engine is asked once.
"""

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from console.engine_probe import ProbeResult, probe
from console.version import VERSION

PROBE_ATTR = "engine_probe"


def remember_probe(request: HttpRequest, result: ProbeResult) -> None:
    """Let the badge reuse ``result`` for this request."""
    setattr(request, PROBE_ATTR, result)


def probe_for_badge() -> ProbeResult:
    """The badge's own probe (the health probe only, never the API or key check)."""
    return probe()


def badge_probe(request: HttpRequest) -> ProbeResult:
    """This request's probe result: the one a view remembered, else one probe, cached."""
    result = getattr(request, PROBE_ATTR, None)
    if not isinstance(result, ProbeResult):
        result = probe_for_badge()
        remember_probe(request, result)
    return result


def engine_badge(request: HttpRequest) -> dict[str, object]:
    return {"engine_badge": SimpleLazyObject(lambda: badge_probe(request))}


# (label, link, active when request.path starts with)
NAV = (
    ("Stories", "/stories/", "/stories/"),
    ("Operations", "/operations/", "/operations/"),
    ("Models", "/models/", "/models/"),
    ("Guardrails", "/guardrails/ratings/", "/guardrails/"),
    ("Engine", "/engine/", "/engine/"),
)


def shell(request: HttpRequest) -> dict[str, object]:
    """The shell's sections (the current one marked ``active``) and the Console version."""
    items = [
        {"label": label, "url": url, "active": request.path.startswith(prefix)}
        for label, url, prefix in NAV
    ]
    return {"nav_items": items, "console_version": VERSION}
