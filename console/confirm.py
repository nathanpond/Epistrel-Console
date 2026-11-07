"""Server-rendered confirm pages for destructive actions (#43's convention; first used by #45).

The action link GETs ``<object path>/<action>/`` with display data in the query string (e.g.
``?name=Harbour&next=/stories/``). The GET renders a page naming what will happen, a Confirm POST
button and a Cancel link back to ``next``, and makes **no** Engine call. The Confirm POST performs
the action. ``next`` is validated with ``url_has_allowed_host_and_scheme`` (an off-site or
missing value falls back to the view's default). Display data is never trusted for the action:
the POST acts on the URL's id.

**D8's extensions** (#235, M6), each optional so every other page renders as before:
``blocked`` (a ``<p class="warning">`` above the buttons, after ``error``, and the submit
rendered ``disabled``; a POST then re-renders the page, 200, and never calls ``perform``, in
plain and confirmed modes alike), ``fact_urls`` (a fact whose label is a key renders its value in
``<a href>``) and ``error_url``/``error_link`` (" — <a href>error_link</a>" inside the error
paragraph, only when ``error``, ``error_url`` and ``error_link`` are all set).

Each render mints a fresh ``idempotency_key`` (hidden field), so a double-submit of one page
replays at the Engine and a new visit is a new attempt.

**Confirmed mode** (#134, M4's destructive writes): ``confirm_action(..., require_confirmed=True)``
adds a hidden ``confirmed=1`` to the page, and only a POST carrying it performs. A POST without
it (the originating form posting straight to the action) re-renders the confirm page with no
Engine call and carries that POST's key forward, so the eventual Confirm sends the key minted
when the operator's first page rendered. A confirmed POST whose key is missing or malformed
re-renders with a fresh key and an error instead of performing under a key nobody saw.

**E9's typed-name confirm** (#295, M7; prune #317 is the first user): ``typed_name`` (with
``typed_label``; :func:`typed_name_for` sets both) renders ``console/_typed_confirm.html``
right after ``fields_template``: "Type <strong>NAME</strong> to confirm" and an
``autocomplete="off"`` input with no ``value`` (the typed text is never echoed back). A POST
that would otherwise perform (after ``blocked`` and the confirmed check) performs only when
``POST["typed_name"].strip() == typed_name``: exact and case-sensitive. Otherwise the page
re-renders (200) with :data:`TYPED_NAME_MISMATCH`, keeping the posted key, and ``perform`` is
never called. A story with no usable name types its id, and the label reads "Type the story's
id <strong>ID</strong> to confirm". With ``typed_name`` "" (the default) every page renders byte
for byte as before.

Usage in a view::

    def archive(request, story_id):
        page = ConfirmPage(title=..., question=..., confirm_label="Archive", ...)
        return confirm_action(request, page, lambda key: perform(key))

A page with extra inputs (#53's regenerate parameters) sets ``fields_template`` to a partial
rendered inside the Confirm form, and passes what it needs as ``extra`` context. The page is
``console/confirm_page.html`` around the shared ``console/_confirm.html``.
"""

import dataclasses
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
CONFIRMED_FIELD = "confirmed"
INCOMPLETE = "This confirmation was incomplete — confirm again"
TYPED_NAME_FIELD = "typed_name"
TYPED_NAME_MISMATCH = "That isn't the story's name — type it exactly as shown"  # E9
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
    return carried_idempotency_key(request.POST, field)


def carried_idempotency_key(data: Mapping[str, Any], field: str = KEY_FIELD) -> str:
    """The key ``data`` carries in ``field`` (a POST, or a GET form's query string that carries
    its page's key forward, #145), or a fresh one when it is missing or malformed."""
    key = data.get(field, "")
    return key if isinstance(key, str) and _KEY.match(key) else new_ulid()


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
    buttons_template: str = ""  # extra buttons after Confirm (#144's Edit)
    error: str = ""  # shown above the buttons (#134)
    # D8's shared extensions (#235); each default renders the page exactly as before.
    blocked: str = ""  # why the page can't proceed: a warning and a disabled submit, no perform
    fact_urls: Mapping[str, str] = field(default_factory=dict)  # fact label → its value's link
    error_url: str = ""  # a link after ``error`` (a ``WorldRefusal.url``) ...
    error_link: str = ""  # ... labelled "Config", "Providers" or "Reconciliation"
    # E9 (#295): the text to type before Confirm performs, and the name the label shows ("": the
    # label reads "the story's id" and shows ``typed_name``).
    typed_name: str = ""
    typed_label: str = ""

    @property
    def fact_rows(self) -> tuple[tuple[str, str, str], ...]:
        """``facts`` as (label, value, link): the link "" unless ``fact_urls`` names the label."""
        return tuple((label, value, self.fact_urls.get(label, "")) for label, value in self.facts)


def typed_name_for(page: ConfirmPage, name: str | None, story_id: str) -> ConfirmPage:
    """``page`` asking for the story's name typed out (E9): its name (stripped), or its id when
    the name is ``None``, blank or whitespace (the label then names the id)."""
    shown = (name or "").strip()
    if shown:
        return dataclasses.replace(page, typed_name=shown, typed_label=shown)
    return dataclasses.replace(page, typed_name=story_id, typed_label="")


def _typed_matches(request: HttpRequest, page: ConfirmPage) -> bool:
    if not page.typed_name:
        return True
    typed = request.POST.get(TYPED_NAME_FIELD)
    return isinstance(typed, str) and typed.strip() == page.typed_name


def _mismatch(
    request: HttpRequest, page: ConfirmPage, key: str, extra: Mapping[str, Any] | None
) -> HttpResponse:
    page = dataclasses.replace(page, error=TYPED_NAME_MISMATCH)
    return render_confirm(request, page, extra=extra, idempotency_key=key)


def render_confirm(
    request: HttpRequest,
    page: ConfirmPage,
    *,
    extra: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> HttpResponse:
    """The confirm page itself (no Engine call), carrying ``idempotency_key`` when given, else a
    freshly minted one."""
    before, _, after = page.question.partition("{name}")
    context: dict[str, Any] = {
        "page": page,
        "question_before": before,
        "question_after": after,
        "has_emphasis": "{name}" in page.question,
        "idempotency_key": idempotency_key or new_ulid(),
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
    require_confirmed: bool = False,
) -> HttpResponse:
    """GET renders ``page``; POST calls ``perform(idempotency_key)`` and returns its response
    (an Engine error it lets escape renders the shared error panel). Other methods are 405.

    With ``require_confirmed`` only a POST carrying ``confirmed=1`` performs (see the module
    docstring); any other POST re-renders the page (200) and calls nothing."""
    if require_confirmed:
        page = dataclasses.replace(page, hidden={**page.hidden, CONFIRMED_FIELD: "1"})
    if request.method == "POST":
        if page.blocked:  # a stale or hand-made POST: re-render, never perform
            key = request.POST.get(KEY_FIELD, "")
            return render_confirm(
                request, page, extra=extra, idempotency_key=key if _KEY.match(key) else None
            )
        if not require_confirmed:
            key = posted_idempotency_key(request)
            if not _typed_matches(request, page):
                return _mismatch(request, page, key, extra)
            return perform(key)
        key = request.POST.get(KEY_FIELD, "")
        valid = bool(_KEY.match(key))
        if request.POST.get(CONFIRMED_FIELD) != "1":
            return render_confirm(
                request, page, extra=extra, idempotency_key=key if valid else None
            )
        if not valid:
            return render_confirm(request, dataclasses.replace(page, error=INCOMPLETE), extra=extra)
        if not _typed_matches(request, page):
            return _mismatch(request, page, key, extra)
        return perform(key)
    if request.method in ("GET", "HEAD"):
        return render_confirm(request, page, extra=extra)
    return HttpResponseNotAllowed(["GET", "HEAD", "POST"])
