"""The shell's Engine badge: one health probe per request, and only when a page shows it (#43).

``engine_badge`` is lazy: the probe runs the first time a template reads it. ``base.html`` reads
it only for a signed-in user, so the login page, the admin, HTMX partials and ``/health`` (JSON)
never probe. A view that already probed (``/``) hands its result over with :func:`remember_probe`,
so the badge and the page agree and the Engine is asked once.
"""

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.functional import SimpleLazyObject

from console.acting import acting_principal
from console.engine.client import principal_for
from console.engine_probe import ProbeResult, probe
from console.forms.principal import known_principals, principal_choices
from console.operators import is_operator
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
    ("Models", "/models/", "/models/"),
    ("Guardrails", "/guardrails/ratings/", "/guardrails/"),
    ("Engine", "/engine/", "/engine/"),
)
# The M3 sections (#64): (label, URL name, active prefixes). Each shows only once its URL exists.
CATALOG = (
    ("Characters", "catalog_characters:index", ("/catalog/characters/",)),
    ("Scenarios", "scenarios:index", ("/catalog/scenarios/",)),
    ("Personas", "personas:index", ("/personas/",)),
    ("Media", "media:index", ("/media/",)),
)
CATALOG_PREFIXES = ("/catalog/", "/personas/", "/media/")
M3_SECTIONS = (
    ("Profiles", "profiles:index", ("/profiles/",)),
    ("Principals", "principals:index", ("/principals/",)),
    ("Offboarding", "offboarding:index", ("/offboarding/",)),
    ("Erasure", "erasure:index", ("/erasure/",)),
)
LAST = (
    ("Operations", "/operations/", "/operations/"),
    ("Admin", "/admin/", "/admin/"),
)


def _resolved(
    request: HttpRequest, links: tuple[tuple[str, str, tuple[str, ...]], ...]
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for label, name, prefixes in links:
        try:
            url = reverse(name)
        except NoReverseMatch:
            continue  # that section's story has not landed yet: no stub pages
        active = any(request.path.startswith(prefix) for prefix in prefixes)
        items.append({"label": label, "url": url, "active": active})
    return items


def _fixed(
    request: HttpRequest, links: tuple[tuple[str, str, str], ...]
) -> list[dict[str, object]]:
    return [
        {"label": label, "url": url, "active": request.path.startswith(prefix)}
        for label, url, prefix in links
    ]


def shell(request: HttpRequest) -> dict[str, object]:
    """The shell's sections (the current one marked ``active``) and the Console version.

    Order (#64): the M2 links, the Catalog group (``nav_catalog``: its label shows only when at
    least one child resolves; active under ``/catalog/``, ``/personas/`` or ``/media/``), the
    other M3 sections, then Operations and Admin (``nav_last``)."""
    return {
        "nav_items": _fixed(request, NAV),
        "nav_catalog": {
            "items": _resolved(request, CATALOG),
            "active": request.path.startswith(CATALOG_PREFIXES),
        },
        "nav_sections": _resolved(request, M3_SECTIONS),
        "nav_last": _fixed(request, LAST),
        "console_version": VERSION,
    }


def acting(request: HttpRequest) -> dict[str, object]:
    """The nav's "Acting as" selector and banner (#64): ``acting_principal``, ``acting_is_self``,
    ``acting_own`` (``console:<username>``) and ``acting_choices`` (the select's options).

    All are lazy callables (templates call them), so nothing is queried unless a signed-in page
    renders the selector: never the login page, HTMX partials or the admin. The acting principal
    itself is one query per request (``console.acting`` memoizes it), and the known-principal
    query runs only for ``acting_choices``."""
    if not is_operator(getattr(request, "user", None)):
        return {}

    def own() -> str:
        return principal_for(request.user)

    def current() -> str:
        return acting_principal(request)

    def is_self() -> bool:
        return current() == own()

    def choices() -> list[tuple[str, str]]:
        return principal_choices(own(), current(), known_principals())

    return {
        "acting_principal": current,
        "acting_is_self": is_self,
        "acting_own": own,
        "acting_choices": choices,
    }
