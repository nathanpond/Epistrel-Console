"""Server-rendered confirm pages for destructive actions (#43's convention; first used by #45).

The action link GETs ``<object path>/<action>/`` with display data in the query string (e.g.
``?name=Harbour&next=/stories/``). The GET renders a page naming what will happen, a Confirm POST
button and a Cancel link back to ``next``, and makes **no** Engine call. The Confirm POST performs
the action. ``next`` is validated with ``url_has_allowed_host_and_scheme`` (an off-site or
missing value falls back to the view's default). Display data is never trusted for the action:
the POST acts on the URL's id.

Each render mints a fresh ``idempotency_key`` (hidden field), so a double-submit of one page
replays at the Engine and a new visit is a new attempt.

Usage in a view::

    def archive(request, story_id):
        page = ConfirmPage(title=..., question=..., confirm_label="Archive", ...)
        return confirm_action(request, page, lambda key: perform(key))

A page with extra inputs (#53's regenerate parameters) sets ``fields_template`` to a partial
rendered inside the Confirm form, and passes what it needs as ``extra`` context. The page is
``console/confirm_page.html`` around the shared ``console/_confirm.html``.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

from console.ids import new_ulid

PAGE_TEMPLATE = "console/confirm_page.html"
KEY_FIELD = "idempotency_key"
NEXT_FIELD = "next"
# The Engine's Idempotency-Key rule.
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")


def safe_next(request: HttpRequest, fallback: str) -> str:
    """The request's ``next`` (POST first, then GET) when it is a local URL, else ``fallback``."""
    candidate = request.POST.get(NEXT_FIELD) or request.GET.get(NEXT_FIELD) or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return fallback


def confirm_query(name: str | None, next_url: str) -> str:
    """The query string an action link carries to its confirm page: the display-only ``name``
    and ``next`` (where Cancel and the action return)."""
    return urlencode({"name": name or "", "next": next_url})


def shown_name(request: HttpRequest) -> str:
    """The display-only ``name`` (query string on GET, hidden field on POST); never trusted
    for the action, which acts on the URL's id."""
    posted = request.POST.get("name") if request.method == "POST" else None
    return posted or request.GET.get("name", "")


def posted_idempotency_key(request: HttpRequest, field: str = KEY_FIELD) -> str:
    """The key the rendered page carried (in ``field``; a page with two writes carries two,
    #66), or a fresh one when it is missing or malformed (a POST that did not come from the
    page)."""
    key = request.POST.get(field, "")
    return key if _KEY.match(key) else new_ulid()


@dataclass(frozen=True)
class ConfirmPage:
    """What a confirm page shows. ``question`` and ``lines`` are plain text (escaped);
    ``emphasis`` is shown in italics inside the question where ``{name}`` appears."""

    title: str
    question: str
    confirm_label: str
    cancel_url: str
    emphasis: str = ""
    lines: tuple[str, ...] = ()
    facts: tuple[tuple[str, str], ...] = ()  # (label, value) rows, e.g. ("Story id", id)
    hidden: Mapping[str, str] = field(default_factory=dict)  # posted back with Confirm
    danger: bool = False  # a permanent action: the Confirm button is styled as such
    fields_template: str = ""  # extra inputs inside the Confirm form


def render_confirm(
    request: HttpRequest,
    page: ConfirmPage,
    *,
    extra: Mapping[str, Any] | None = None,
) -> HttpResponse:
    """The confirm page itself (no Engine call), with a freshly minted idempotency key."""
    before, _, after = page.question.partition("{name}")
    context: dict[str, Any] = {
        "page": page,
        "question_before": before,
        "question_after": after,
        "has_emphasis": "{name}" in page.question,
        "idempotency_key": new_ulid(),
        "next_url": page.cancel_url,
        **(extra or {}),
    }
    return render(request, PAGE_TEMPLATE, context)


def confirm_action(
    request: HttpRequest,
    page: ConfirmPage,
    perform: Callable[[str], HttpResponse],
    *,
    extra: Mapping[str, Any] | None = None,
) -> HttpResponse:
    """GET renders ``page``; POST calls ``perform(idempotency_key)`` and returns its response
    (an Engine error it lets escape renders the shared error panel). Other methods are 405."""
    if request.method == "POST":
        return perform(posted_idempotency_key(request))
    if request.method in ("GET", "HEAD"):
        return render_confirm(request, page, extra=extra)
    return HttpResponseNotAllowed(["GET", "HEAD", "POST"])
