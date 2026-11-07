"""Request a certification run for a profile or a story, only after seeing its estimate (#314,
FR-PROF-17, owner answer 8).

A certification run plays the eval suites on scratch stories with the deployment's real models and
records a certification when a floor passes; it never changes the configuration. Two subjects,
one URL each (E8's "one URL per write": a POST carrying ``twostage.CONFIRM_FIELD`` is the
confirm):

- ``profiles:certify`` (``/profiles/<id>/certify/``, :func:`certify_profile`): a profile at its
  latest version or a chosen one, over ``request_certification_run_profile``. Every render reads
  the profile and its versions (one read each); a 404 profile read is :data:`NOT_FOUND_TEXT` in
  the page at 404, any other failed read #43's panel with no form, and a failed versions read
  leaves "Latest" only with :data:`VERSIONS_UNREAD`. The page extends ``base.html`` through
  ``console/profiles/certify_base.html``; its title is "Request a certification run · <name>".
- ``stories:certify`` (``/stories/<id>/certify/``, :func:`certify_story`): the story's effective
  configuration, overrides included, over ``request_certification_run_story`` (``version`` is
  never sent), framed by #296's ``story_page`` with the label :data:`LABEL`.

The stages (E8, E21):

1. **GET**: :class:`~console.forms.certification.CertifyForm` and "Estimate", no certification
   call.
2. **Estimate** (a valid POST without the token): ``confirm=False`` with only the set members
   (``request_kwargs()``) under the form's per-render key. The answer renders
   ``_certification_estimate.html`` under the still-editable form, then ``_two_stage.html``
   ("Start the run" / Cancel to the bare GET) carrying ``twostage.sign(<kind>, <id>, body,
   base)``: ``body`` is ``{confirm: true, **request_kwargs(), suites: <the estimate's suites>}``
   (left out when the estimate named none), ``base`` ``{fingerprint, estimate, suites}``. An
   estimate that carries a ``run_id`` is a misbehaving Engine: #43's ``bad_response`` panel,
   never followed (the key kept: something may have been queued).
3. **Start the run** (the token): unsigned first; an expired, tampered, other-kind or
   other-subject token is :data:`~console.twostage.EXPIRED_TEXT` and a 303 to the form with zero
   Engine calls. Otherwise the signed body is re-sent under the partial's key. A 202 with a
   ``run_id`` (``replayed`` too) is a 303 to ``engine:certification-run`` (#313, which polls it
   and settles the #44 ``queued`` row by its job id) with :data:`QUEUED_TEXT` and E21's key tail.
   A 2xx without ``run_id`` is ``bad_response``; it and any unknown outcome re-render the same
   confirm (same token and key) under #43's panel.

**Refusals** (either stage, 200, on the form; none retries): ``certification_run_in_progress``
(:data:`IN_PROGRESS_TEXT`, linked to the fingerprint's certifications and the run in progress),
``certification_unavailable`` (:data:`UNAVAILABLE_TEXT`), ``validation_error`` at
``body.<suites|seeds|sample|version>`` on that field (E21; anything unmapped is #43's panel,
logged), for a story ``story_busy`` (C5's busy notice, " · retry after <n> s") and
``story_importing`` (E10's text and job link), a 404 (:data:`NOT_FOUND_TEXT` for a profile, #43's
panel for a story) and anything else #43's panel. A confirm-stage refusal re-renders the form
bound from the signed body, with the estimate from ``base`` and no Start (re-estimate first).

Recorded (#44) against the profile (``entity_type="engine_profile"``) or the story (E19).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods

from console import twostage
from console.certification import certification_run_url, certifications_url
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClient, EngineClientError, EngineError, EngineUnreachable
from console.engine.models import CertificationRunRequested, EngineProfile, ProfileVersion
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.certification import CertifyForm, bound_data
from console.held import is_held, retry_seconds
from console.ids import is_ulid, new_ulid
from console.impact import duration_text
from console.portability import importing_refusal, is_importing
from console.views.profiles import VERSION_PARAM, detail_url
from console.views.story_pages import StoryPageContext, story_page
from console.wording import busy_notice

TEMPLATE = "console/certify.html"
PROFILE_FRAME = "console/profiles/certify_base.html"
STORY_FRAME = "console/stories/page_base.html"
PROFILE_KIND = "certify-profile"
STORY_KIND = "certify-story"
LABEL = "Request a certification run"
LINK_TEXT = "Request a certification run…"

ESTIMATE_NOTE = (
    "The run plays these suites with the deployment's real models; it records results and never "
    "changes the configuration"
)
INTRO_TEXT = (
    "Estimate first: nothing is written until you start the run. The estimate says what the run "
    "would cost in model calls, tokens and time"
)
ESTIMATE_LABEL = "Estimate"
START_LABEL = "Start the run"
NOT_PRICED_TEXT = "Cost: not priced by this deployment's model matrix"
NO_SUITES = "none"
QUEUED_TEXT = "Certification run queued — {calls:,} model calls estimated"
IN_PROGRESS_TEXT = (
    "A run for this configuration is already queued or running — wait for it to finish"
)
RUN_IN_PROGRESS_LINK = "The run in progress"
UNAVAILABLE_TEXT = "This Engine has no eval harness, so certification runs can't be requested here"
NOT_FOUND_TEXT = "No such profile"
MALFORMED_TEXT = "No such profile — the id in the address isn't an Engine id"
VERSIONS_UNREAD = "Couldn't read the profile's versions — only Latest can be chosen"
REESTIMATE_TEXT = "Nothing was started — estimate again before starting the run"
RUN_ID_AT_ESTIMATE = "the Engine answered an estimate with a run id"
NO_RUN_ID = "the Engine accepted the run without a run id"
CERTIFICATIONS_LINK = "Certifications for this configuration"

IN_PROGRESS = "certification_run_in_progress"
UNAVAILABLE = "certification_unavailable"
VALIDATION_ERROR = "validation_error"
BAD_RESPONSE = "bad_response"
STORY_BUSY = "story_busy"


# --- the estimate block ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimateView:
    """``_certification_estimate.html``'s lines."""

    numbers: str
    cost: str
    fingerprint: str
    certifications_url: str | None
    suites: str
    note: str = ESTIMATE_NOTE


def numbers_text(estimate: Mapping[str, Any]) -> str:
    """ "<calls> model calls · ~<tokens> tokens · ~<duration> with the calls one after another"."""
    return (
        f"{int(estimate['model_calls']):,} model calls · ~{int(estimate['tokens']):,} tokens · "
        f"~{duration_text(float(estimate['duration_s']))} with the calls one after another"
    )


def cost_text(cost: object) -> str:
    """ "Cost: USD <cost, 2 dp>" (0 is "USD 0.00"); ``None`` is :data:`NOT_PRICED_TEXT`."""
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        return f"Cost: USD {cost:,.2f}"
    return NOT_PRICED_TEXT


def suites_text(suites: object) -> str:
    names = [str(s) for s in suites] if isinstance(suites, list) else []
    return ", ".join(names) if names else NO_SUITES


def estimate_view(base: Mapping[str, Any]) -> EstimateView | None:
    """The block from a signed ``base`` (or an answer's :func:`answer_base`); ``None`` when it
    isn't one."""
    estimate = base.get("estimate")
    fingerprint = base.get("fingerprint")
    if (
        not isinstance(estimate, dict)
        or not isinstance(fingerprint, str)
        or not all(isinstance(estimate.get(k), int | float) for k in ("model_calls", "tokens"))
        or not isinstance(estimate.get("duration_s"), int | float)
    ):
        return None
    return EstimateView(
        numbers_text(estimate),
        cost_text(estimate.get("cost")),
        fingerprint,
        certifications_url(fingerprint=fingerprint) if fingerprint else None,
        suites_text(base.get("suites")),
    )


def answer_base(answer: CertificationRunRequested) -> dict[str, Any]:
    """The token's ``base``: the fingerprint, the estimate (as JSON) and the suites."""
    estimate = answer.estimate
    return {
        "fingerprint": answer.fingerprint,
        "estimate": {
            "model_calls": estimate.model_calls,
            "tokens": estimate.tokens,
            "duration_s": estimate.duration_s,
            "cost": estimate.cost,
        },
        "suites": list(answer.suites),
    }


def signed_body(kwargs: Mapping[str, Any], answer: CertificationRunRequested) -> dict[str, Any]:
    """What "Start the run" sends: ``confirm: true``, the set members, and the estimate's suites
    pinned (left out when it named none: the Engine refuses an empty list)."""
    body: dict[str, Any] = {"confirm": True, **kwargs}
    body.pop("suites", None)
    if answer.suites:
        body["suites"] = list(answer.suites)
    return body


def queued_text(calls: int, key: str) -> SafeString:
    return format_html("{} — idempotency key <code>{}</code>", QUEUED_TEXT.format(calls=calls), key)


# --- refusals -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """A refusal's line on the form: its text, the text's link, and extra ``(label, url)``
    links after it."""

    text: str
    url: str | None = None
    links: tuple[tuple[str, str], ...] = ()


@dataclass
class Outcome:
    """What a re-render shows above or on the form."""

    refusal: Refusal | None = None
    panel: ErrorPanel | None = None
    key: str | None = None  # the posted key, kept on an unknown outcome
    field_errors: list[EngineError] = field(default_factory=list)


def _unknown(exc: EngineClientError) -> bool:
    """D8's unknown outcome: no answer, or one that didn't parse (the write may have run)."""
    return isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )


def in_progress_refusal(exc: EngineError, fingerprint: str | None) -> Refusal:
    details = exc.details
    found = details.get("fingerprint")
    fingerprint = found if isinstance(found, str) and found else fingerprint
    run_id = details.get("run_id")
    run_url = certification_run_url(run_id) if isinstance(run_id, str) else None
    return Refusal(
        IN_PROGRESS_TEXT,
        certifications_url(fingerprint=fingerprint) if fingerprint else None,
        ((RUN_IN_PROGRESS_LINK, run_url),) if run_url else (),
    )


def busy_text(exc: EngineError) -> str:
    text = busy_notice(exc.details)
    seconds = retry_seconds(exc.retry_after)
    return f"{text} · retry after {seconds} s" if seconds is not None else text


def outcome_for(
    request: HttpRequest, exc: EngineClientError, *, story: bool, fingerprint: str | None, key: str
) -> Outcome:
    """The refusal's re-render (module docstring); logs it."""
    log_engine_error(request, exc)
    if _unknown(exc) or not isinstance(exc, EngineError):
        return Outcome(panel=panel_for(exc), key=key)
    if exc.code == IN_PROGRESS:
        return Outcome(refusal=in_progress_refusal(exc, fingerprint))
    if exc.code == UNAVAILABLE:
        return Outcome(refusal=Refusal(UNAVAILABLE_TEXT))
    if exc.code == VALIDATION_ERROR:
        return Outcome(field_errors=[exc])
    if story and exc.code == STORY_BUSY:
        return Outcome(refusal=Refusal(busy_text(exc)))
    if story and is_importing(exc):
        found = importing_refusal(exc)
        return Outcome(refusal=Refusal(found.text, found.url or None))
    if not story and exc.http_status == 404:
        return Outcome(refusal=Refusal(NOT_FOUND_TEXT))
    return Outcome(panel=panel_for(exc))


def _place(request: HttpRequest, form: CertifyForm, outcome: Outcome) -> Outcome:
    """Put ``validation_error`` failures on their fields; anything unmapped is #43's panel (a
    Console bug, logged)."""
    for exc in outcome.field_errors:
        form.is_valid()
        if form.apply_engine_error(exc):
            outcome.panel = panel_for(exc)
            log_engine_error(request, exc)
    return outcome


# --- the subject ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Subject:
    """One certify page: what it certifies and how to ask."""

    kind: str
    target_id: str
    url: str
    versions: list[ProfileVersion] | None  # ``None`` for a story (no version field)
    latest: int | None
    request: Callable[..., CertificationRunRequested]

    @property
    def story(self) -> bool:
        return self.kind == STORY_KIND


def _form(
    subject: Subject,
    data: Mapping[str, Any] | None = None,
    initial: Mapping[str, Any] | None = None,
) -> CertifyForm:
    return CertifyForm(data, versions=subject.versions, latest=subject.latest, initial=initial)


def page_context(
    subject: Subject,
    form: CertifyForm,
    *,
    key: str | None = None,
    outcome: Outcome | None = None,
    estimate: EstimateView | None = None,
    token: str = "",
    confirm_key: str | None = None,
    warnings: tuple[str, ...] = (),
    note: str = "",
) -> dict[str, Any]:
    outcome = outcome or Outcome()
    return {
        "form": form,
        "form_url": subject.url,
        "idempotency_key": key or outcome.key or new_ulid(),
        "intro_text": INTRO_TEXT,
        "estimate_label": ESTIMATE_LABEL,
        "refusal": outcome.refusal,
        "engine_error": outcome.panel,
        "versions_warnings": warnings,
        "estimate": estimate,
        "estimate_note": note,
        "token": token,
        "confirm_url": subject.url,
        "confirm_label": START_LABEL,
        "cancel_url": subject.url,
        "confirm_key": confirm_key or new_ulid(),
    }


def _estimate(
    request: HttpRequest, subject: Subject, warnings: tuple[str, ...]
) -> dict[str, Any] | HttpResponse:
    """The first stage: a valid form is estimated (``confirm=False``) under the posted key."""
    form = _form(subject, request.POST)
    if not form.is_valid():  # E21: a local-validation failure mints a fresh key; no call
        return page_context(subject, form, warnings=warnings)
    kwargs = form.request_kwargs()
    key = posted_idempotency_key(request)
    try:
        answer = subject.request(confirm=False, idempotency_key=key, **kwargs)
    except EngineClientError as exc:
        if is_held(exc):
            raise
        outcome = outcome_for(request, exc, story=subject.story, fingerprint=None, key=key)
        return page_context(
            subject, form, outcome=_place(request, form, outcome), warnings=warnings
        )
    if answer.run_id is not None:  # a misbehaving Engine: never followed
        bad = EngineError(BAD_RESPONSE, RUN_ID_AT_ESTIMATE, {"run_id": answer.run_id}, 200)
        log_engine_error(request, bad)
        outcome = Outcome(panel=panel_for(bad), key=key)
        return page_context(subject, form, outcome=outcome, warnings=warnings)
    base = answer_base(answer)
    token = twostage.sign(subject.kind, subject.target_id, signed_body(kwargs, answer), base)
    return page_context(subject, form, estimate=estimate_view(base), token=token, warnings=warnings)


@dataclass(frozen=True)
class Signed:
    token: str
    body: dict[str, Any]
    base: dict[str, Any]


def unsigned(request: HttpRequest, kind: str, target_id: str) -> Signed | None:
    """The confirm token's body and base; ``None`` when it is expired, tampered, of another kind,
    for another subject or not a body this view signed."""
    token = request.POST.get(twostage.CONFIRM_FIELD, "")
    try:
        payload = twostage.unsign(kind, token)
    except twostage.ConfirmationExpired:
        return None
    body, base = payload.body, payload.base
    if (
        payload.target_id != target_id
        or not isinstance(body, dict)
        or body.get("confirm") is not True
        or not isinstance(base, dict)
    ):
        return None
    return Signed(token, body, base)


def _expired(request: HttpRequest, url: str) -> HttpResponse:
    messages.error(request, twostage.EXPIRED_TEXT)
    return _see_other(url)


def _see_other(url: str) -> HttpResponse:
    response = redirect(url)
    response.status_code = 303
    return response


def _start(
    request: HttpRequest, subject: Subject, signed: Signed, warnings: tuple[str, ...]
) -> dict[str, Any] | HttpResponse:
    """The confirm: the signed body re-sent under the partial's key."""
    key = posted_idempotency_key(request)
    kwargs = {k: v for k, v in signed.body.items() if k != "confirm"}
    shown = estimate_view(signed.base)
    fingerprint = signed.base.get("fingerprint")
    fingerprint = fingerprint if isinstance(fingerprint, str) else None
    try:
        answer = subject.request(confirm=True, idempotency_key=key, **kwargs)
    except EngineClientError as exc:
        if is_held(exc):
            raise
        outcome = outcome_for(request, exc, story=subject.story, fingerprint=fingerprint, key=key)
        if outcome.key is not None:  # E21: the same confirm, same token and key, under the panel
            return _same_confirm(subject, signed, key, outcome, shown, warnings)
        form = _form(subject, bound_data(signed.body))
        return page_context(
            subject,
            form,
            outcome=_place(request, form, outcome),
            estimate=shown,
            warnings=warnings,
            note=REESTIMATE_TEXT,
        )
    if answer.run_id is None:
        bad = EngineError(BAD_RESPONSE, NO_RUN_ID, {"job_id": answer.job_id}, 202)
        log_engine_error(request, bad)
        outcome = Outcome(panel=panel_for(bad), key=key)
        return _same_confirm(subject, signed, key, outcome, shown, warnings)
    run_url = certification_run_url(answer.run_id) or subject.url
    messages.success(
        request, queued_text(answer.estimate.model_calls, answer.idempotency_key or key)
    )
    return _see_other(run_url)


def _same_confirm(
    subject: Subject,
    signed: Signed,
    key: str,
    outcome: Outcome,
    shown: EstimateView | None,
    warnings: tuple[str, ...],
) -> dict[str, Any]:
    form = _form(subject, bound_data(signed.body))
    return page_context(
        subject,
        form,
        outcome=Outcome(panel=outcome.panel),
        estimate=shown,
        token=signed.token,
        confirm_key=key,
        warnings=warnings,
    )


# --- the profile page -----------------------------------------------------------------------------


def certify_url(profile_id: str, version: int | None = None) -> str:
    """``profiles:certify``, with ``?version=`` when given (the profile page's older version)."""
    url = reverse("profiles:certify", args=[profile_id])
    return f"{url}?{VERSION_PARAM}={version}" if version is not None else url


def _profile_frame(profile_id: str, name: str) -> dict[str, Any]:
    return {
        "frame": PROFILE_FRAME,
        "page_label": LABEL,
        "profile_id": profile_id,
        "profile_name": name or profile_id,
        "detail_url": detail_url(profile_id),
    }


def _profile_page(
    request: HttpRequest, context: Mapping[str, Any], status: int = 200
) -> HttpResponse:
    return render(request, TEMPLATE, dict(context), status=status)


def _profile_reads(
    request: HttpRequest, client: EngineClient, profile_id: str
) -> tuple[EngineProfile, list[ProfileVersion], tuple[str, ...]] | HttpResponse:
    """The profile and its versions (a failed versions read: none, with the warning), or the
    page a failed profile read shows."""
    try:
        profile = client.get_engine_profile(profile_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        frame = _profile_frame(profile_id, "")
        if isinstance(exc, EngineError) and exc.http_status == 404:
            return _profile_page(request, {**frame, "not_found_text": NOT_FOUND_TEXT}, 404)
        return _profile_page(request, {**frame, "profile_error": panel_for(exc)})
    try:
        versions = client.list_engine_profile_versions(profile_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return profile, [], (VERSIONS_UNREAD,)
    return profile, versions, ()


def _version_param(request: HttpRequest, versions: list[ProfileVersion]) -> str | None:
    raw = request.GET.get(VERSION_PARAM, "")
    known = {str(v.version) for v in versions}
    return raw if raw in known else None


@require_http_methods(["GET", "HEAD", "POST"])
def certify_profile(request: HttpRequest, profile_id: str) -> HttpResponse:
    """``profiles:certify`` (module docstring)."""
    pid = profile_id.strip().upper()
    if not is_ulid(pid):
        context = {"engine_error": ErrorPanel(MALFORMED_TEXT)}
        return render(request, "console/engine_error_page.html", context, status=404)
    url = reverse("profiles:certify", args=[pid])
    confirming = request.method == "POST" and twostage.CONFIRM_FIELD in request.POST
    signed = unsigned(request, PROFILE_KIND, pid) if confirming else None
    if confirming and signed is None:
        return _expired(request, url)
    client = engine_client_for(request)
    read = _profile_reads(request, client, pid)
    if isinstance(read, HttpResponse):
        return read
    profile, versions, warnings = read

    def ask(**kwargs: Any) -> CertificationRunRequested:
        return client.request_certification_run_profile(pid, **kwargs)

    subject = Subject(PROFILE_KIND, pid, url, versions, profile.current_version, ask)
    frame = _profile_frame(pid, profile.name)
    built: Mapping[str, Any] | HttpResponse
    if signed is not None:
        built = _start(request, subject, signed, warnings)
    elif request.method == "POST":
        built = _estimate(request, subject, warnings)
    else:
        chosen = _version_param(request, versions)
        form = _form(subject, initial={"version": chosen} if chosen else None)
        built = page_context(subject, form, warnings=warnings)
    if isinstance(built, HttpResponse):
        return built
    return _profile_page(request, {**frame, **built})


# --- the story page -------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def certify_story(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:certify`` (module docstring)."""
    url = reverse("stories:certify", args=[story_id])
    confirming = request.method == "POST" and twostage.CONFIRM_FIELD in request.POST
    signed = unsigned(request, STORY_KIND, story_id) if confirming else None
    if confirming and signed is None:  # E24: zero calls, not even the frame's reads
        return _expired(request, url)

    def build(ctx: StoryPageContext) -> Mapping[str, Any] | HttpResponse:
        def ask(**kwargs: Any) -> CertificationRunRequested:
            kwargs.pop("version", None)  # never sent for a story
            return ctx.client.request_certification_run_story(story_id, **kwargs)

        subject = Subject(STORY_KIND, story_id, url, None, None, ask)
        built: Mapping[str, Any] | HttpResponse
        if signed is not None:
            built = _start(request, subject, signed, ())
        elif request.method == "POST":
            built = _estimate(request, subject, ())
        else:
            built = page_context(subject, _form(subject))
        if isinstance(built, HttpResponse):
            return built
        return {**built, "frame": STORY_FRAME}

    return story_page(request, story_id, TEMPLATE, build, label=LABEL)
