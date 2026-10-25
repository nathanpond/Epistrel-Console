"""A story's settings page (#54): ``/stories/<id>/settings/``.

One page with sections, each read separately so a failed read degrades only its section (#43):

- **Settings**: the seven settable story settings with their "story" / "default" markers
  (:mod:`console.story_settings`), a reset-to-default per field, and old keys the read still
  returns under "Other settings". Save sends a merge patch of the changed fields only. The six
  engine dials that moved to engine-profile overrides are a note linking to the story's Config
  page (M3 #85).
- **Budget**: ``guardrail_calls`` and ``effective_budget`` as the Engine sends them.
- **Effective policies** and **inactive policies**, read-only.
- **Story policies**: the story- and character-scoped ones, an add / edit form (one
  ``policies`` upsert per submit) and no Remove (owner).
- **Pinned memories** and **custom instructions**, each saved with ``PUT``.
- **Config changes**: #50's grouped table, filtered by GET parameters on this page.

Each form POSTs to its own URL (``settings/``, ``settings/policies/``, ``settings/pinned/``,
``settings/instructions/``) with the page's signed snapshot (story, cast, settings as read and
the deployment's defaults and caps). A refused submit re-renders only that section, from the
snapshot, with the input kept: an invalid form makes no Engine call. A save redirects back to
the page with a flash (PRG). No call sends an expected revision (#43).
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from django import forms
from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from console.cast import CastMember, members, viewpoint_label
from console.config_changes import RevisionGroup, revision_groups, setting_filter
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    DeploymentSettings,
    EffectivePolicy,
    Policy,
    Story,
    StorySettings,
)
from console.engine_access import engine_client_for
from console.engine_api_check import RECORDED
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.config_changes import ConfigChangeFilterForm
from console.forms.story_settings import (
    PINNED_PREFIX,
    SETTINGS_ERROR_PATHS,
    STORY_POLICY_ERROR_PATHS,
    BasePinnedFormSet,
    CustomInstructionsForm,
    StoryPolicyForm,
    StorySettingsForm,
    pinned_formset,
    story_policy_initial,
)
from console.formset_rows import edit_rows, wants_edit
from console.history import NAME_WITHHELD
from console.ids import new_ulid
from console.ingest import BatchErrors
from console.story_pause import pause_banner, paused_refusal
from console.story_settings import (
    DEPLOYMENT_DEFAULTS,
    FALLBACK_MAX_INSTRUCTIONS,
    FALLBACK_MAX_PINNED,
    SUPERSEDED,
    budget_source,
    config_url,
    other_settings,
    setting_groups,
)
from console.turn_reports import ViolationRow, violation_rows
from console.views.play import error_inspector

log = logging.getLogger(__name__)

PAGE_TEMPLATE = "console/settings/page.html"
SECTION_TEMPLATE = "console/settings/section.html"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.story_settings"
CHANGES_PATH = "/stories/{story_id}/config-changes"
FILTER_KEYS = ("setting", "prefixed", "change_class", "after")
EDIT_POLICY = "policy"  # ?policy=<id>: the policy form edits that policy
EMPTY = "—"
ARCHIVED_NOTE = "Archived — unarchive to change the settings"
SUPERSEDED_REASON = "superseded"
CANNOT_RELAX = "A story policy can only add restrictions"
ERASED_TEXT = "This text refers to an erased subject — nothing was saved"
GUARDRAIL_TEXT = "Rejected by a guardrail — nothing was saved"
CAP_TEXT = "Over a cap — nothing was saved"
PIN_LIMIT_TEXT = "Pin limit exceeded — nothing was saved"
NOT_EDITABLE = "That policy is not one of this story's editable policies."
# The deployment settings the page uses: the nullable settings' defaults and the form caps.
DEPLOYMENT_KEYS = (*DEPLOYMENT_DEFAULTS, "max_pinned_memories", "max_custom_instructions_chars")
SECTIONS = {
    "settings": ("Settings", "console/settings/_settings_form.html"),
    "policy": ("Story policy", "console/settings/_policy_form.html"),
    "pinned": ("Pinned memories", "console/settings/_pinned.html"),
    "instructions": ("Custom instructions", "console/settings/_instructions.html"),
}


# --- the snapshot ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """What every section form posts back, signed: the story, its cast, its settings as read
    (``None`` when that read failed) and the deployment's defaults and caps (``None``: unread)."""

    story_id: str
    name: str
    status: str
    cast: list[CastMember]
    settings: dict[str, Any] | None
    deployment: dict[str, Any] | None

    @classmethod
    def of(
        cls,
        story: Story,
        settings: StorySettings | None,
        deployment: DeploymentSettings | None,
    ) -> Snapshot:
        dep = None
        if deployment is not None:
            dep = {key: getattr(deployment, key) for key in DEPLOYMENT_KEYS}
        return cls(
            story.id,
            story.name or "",
            story.status,
            members(story.cast),
            dict(settings.settings) if settings is not None else None,
            dep,
        )

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=SNAPSHOT_SALT, compress=True)

    @classmethod
    def unsigned(cls, value: str | None, story_id: str) -> Snapshot | None:
        try:
            raw = signing.loads(value or "", salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict) or raw.get("story_id") != story_id:
            return None
        try:
            cast = [CastMember(**member) for member in raw["cast"]]
            return cls(
                story_id,
                str(raw["name"]),
                str(raw["status"]),
                cast,
                raw["settings"],
                raw["deployment"],
            )
        except KeyError, TypeError:
            return None

    @property
    def label(self) -> str:
        return self.name or self.story_id

    @property
    def archived(self) -> bool:
        return self.status == "archived"

    def cap(self, key: str, fallback: int) -> int:
        value = (self.deployment or {}).get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else fallback

    @property
    def max_pinned(self) -> int:
        return self.cap("max_pinned_memories", FALLBACK_MAX_PINNED)

    @property
    def max_instructions(self) -> int:
        return self.cap("max_custom_instructions_chars", FALLBACK_MAX_INSTRUCTIONS)


def _read_snapshot(
    request: HttpRequest, client: EngineClient, story_id: str
) -> tuple[Story, Snapshot, StorySettings | None, ErrorPanel | None, ErrorPanel | None]:
    """The story (errors escape to the page's error panel), its settings and the deployment's
    (each failure degrades its section)."""
    story = client.get_story(story_id)
    settings, settings_error = _attempt(request, lambda: client.get_story_settings(story_id))
    deployment, deployment_error = _attempt(request, client.get_deployment_settings)
    return (
        story,
        Snapshot.of(story, settings, deployment),
        settings,
        settings_error,
        deployment_error,
    )


def _attempt[T](request: HttpRequest, read: Callable[[], T]) -> tuple[T | None, ErrorPanel | None]:
    try:
        return read(), None
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None, panel_for(exc)


def _posted_snapshot(request: HttpRequest, story_id: str) -> Snapshot:
    found = Snapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), story_id)
    if found is not None:
        return found
    client = engine_client_for(request)
    return _read_snapshot(request, client, story_id)[1]


# --- refusals -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    """What a section shows about a refused save, with the form kept."""

    notice: str = ""
    notice_url: str = ""  # links the notice (#80: the check a 409 story_paused names)
    line: str = ""  # "HTTP 423 story_busy: …"
    facts: tuple[tuple[str, str], ...] = ()
    violations: tuple[ViolationRow, ...] = ()
    panel: ErrorPanel | None = None


def _detail(details: Mapping[str, Any], key: str) -> str:
    value = details.get(key)
    return EMPTY if value is None else str(value)


def refusal_for(exc: EngineClientError, key: str) -> Refusal:
    """A refusal in #48's wording where it has one (busy line, guardrail violations, erased
    subject), the AC's for caps and pin limits, else the error panel."""
    if not isinstance(exc, EngineError):
        return Refusal(panel=panel_for(exc))
    details = exc.details
    line = f"HTTP {exc.http_status} {exc.code}: {exc.message}"
    if exc.code == "story_paused":
        paused = paused_refusal(exc)
        return Refusal(notice=paused.text, notice_url=paused.url, line=line)
    if exc.code == "story_busy":
        busy = error_inspector(exc, key)  # #48's busy line: notice, operation, Retry-After
        return Refusal(notice=busy.notice, line=line, facts=busy.facts)
    if exc.code == "guardrail_violation":
        found = details.get("violations")
        raw = [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []
        return Refusal(notice=GUARDRAIL_TEXT, line=line, violations=tuple(violation_rows(raw)))
    facts: tuple[tuple[str, str], ...]
    if exc.code == "erased_subject_referenced":
        facts = (
            ("Erasure", _detail(details, "erasure_id")),
            ("Matches", _detail(details, "match_count")),
        )
        return Refusal(notice=ERASED_TEXT, line=line, facts=facts)
    if exc.code == "pin_limit_exceeded":
        facts = (
            ("Scope", _detail(details, "scope")),
            ("Limit", _detail(details, "limit")),
            ("Actual", _detail(details, "actual")),
        )
        return Refusal(notice=PIN_LIMIT_TEXT, line=line, facts=facts)
    if exc.http_status == 422 and "cap" in details:
        facts = (
            ("Cap", _detail(details, "cap")),
            ("Limit", _detail(details, "limit")),
            ("Actual", _detail(details, "actual")),
        )
        return Refusal(notice=CAP_TEXT, line=line, facts=facts)
    return Refusal(panel=panel_for(exc))


ITEM_INDEX = re.compile(r"^(?:body\.)?items\[(?P<index>\d+)\]")


def item_index(value: object) -> int | None:
    """The row an Engine path or locator names (``items[2].text`` → 2)."""
    if isinstance(value, str):
        match = ITEM_INDEX.match(value)
        if match:
            return int(match["index"])
    return None


# --- the page -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyRow:
    """One effective or inactive policy as the tables show it."""

    policy: EffectivePolicy
    name: str
    scope: str
    applies_to: str
    editable: bool
    edit_url: str = ""


def policy_rows(
    policies: Sequence[EffectivePolicy], story_id: str, *, editable_only: bool = False
) -> list[PolicyRow]:
    rows = []
    for policy in policies:
        own = policy.scope in ("story", "character")
        if editable_only and not own:
            continue
        editable = own and policy.name is not None
        url = ""
        if editable:
            base = reverse("stories:settings", args=[story_id])
            url = f"{base}?{EDIT_POLICY}={policy.id}#story-policies"
        rows.append(
            PolicyRow(
                policy=policy,
                name=policy.name if policy.name is not None else NAME_WITHHELD,
                scope=str(policy.scope),
                applies_to=", ".join(str(t) for t in policy.applies_to) or EMPTY,
                editable=editable,
                edit_url=url,
            )
        )
    return rows


@dataclass
class Page:
    """The full page's context, section by section."""

    story: Story
    snap: Snapshot
    sections: dict[str, Any] = field(default_factory=dict)

    def context(self) -> dict[str, Any]:
        return {
            "story": self.story,
            "snap": self.snap,
            "snapshot": self.snap.signed(),
            "archived_note": ARCHIVED_NOTE if self.snap.archived else "",
            "pause_banner": pause_banner(self.story),  # #80
            "rating_url": reverse("stories:rating", args=[self.story.id]),
            **self.sections,
        }


def _settings_context(
    snap: Snapshot,
    *,
    form: StorySettingsForm | None = None,
    refusal: Refusal | None = None,
    deployment_error: ErrorPanel | None = None,
) -> dict[str, Any]:
    if snap.settings is not None and form is None:
        form = StorySettingsForm(original=snap.settings, cast=snap.cast, deployment=snap.deployment)
    return {
        "settings_form": form,
        "settings_refusal": refusal,
        "deployment_error": deployment_error,
        "others": other_settings(snap.settings or {}),
        "superseded": SUPERSEDED,
        "config_url": config_url(snap.story_id),
        "settings_key": new_ulid(),
    }


def _policy_context(
    snap: Snapshot,
    *,
    form: StoryPolicyForm | None = None,
    editing: str = "",
    refusal: Refusal | None = None,
    policy_error: ErrorPanel | None = None,
) -> dict[str, Any]:
    return {
        "policy_form": form if form is not None else StoryPolicyForm(cast=snap.cast),
        "policy_editing": editing,
        "policy_refusal": refusal,
        "policy_error": policy_error,
        "policy_key": new_ulid(),
    }


def _pinned_context(
    snap: Snapshot,
    formset: BasePinnedFormSet | None,
    *,
    revision: int | None = None,
    refusal: Refusal | None = None,
    holder: forms.Form | None = None,
    show_errors: bool = True,
) -> dict[str, Any]:
    return {
        "pinned": formset,
        "pinned_revision": revision,
        "pinned_refusal": refusal,
        "pinned_holder": holder,
        "pinned_show_errors": show_errors,
        "pinned_can_add": formset is not None and formset.total_form_count() < snap.max_pinned,
        "max_pinned": snap.max_pinned,
        "pinned_key": new_ulid(),
    }


def _instructions_context(
    snap: Snapshot,
    form: CustomInstructionsForm | None,
    *,
    revision: int | None = None,
    refusal: Refusal | None = None,
) -> dict[str, Any]:
    return {
        "instructions_form": form,
        "instructions_revision": revision,
        "instructions_refusal": refusal,
        "max_instructions": snap.max_instructions,
        "instructions_key": new_ulid(),
    }


def _changes_context(request: HttpRequest, client: EngineClient, story_id: str) -> dict[str, Any]:
    """``GET …/config-changes`` with this page's filter parameters; an invalid filter makes no
    call."""
    allowed = setting_filter(RECORDED, CHANGES_PATH)
    filtered = any(key in request.GET for key in FILTER_KEYS)
    form = ConfigChangeFilterForm(
        request.GET if filtered else None,
        settings=allowed,
        groups=setting_groups(allowed.names),
        with_since=False,
    )
    groups: list[RevisionGroup] = []
    next_query = ""
    error: ErrorPanel | None = None
    if not filtered or form.is_valid():
        query = form.query() if filtered else {}
        try:
            page = client.list_story_config_changes(
                story_id,
                setting=query.get("setting"),
                change_class=query.get("change_class"),
                after=query.get("after"),
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            error = panel_for(exc)
        else:
            groups = revision_groups(page.items)
            if page.next:
                params = request.GET.copy()
                params["after"] = page.next
                next_query = params.urlencode()
    first = request.GET.copy()
    first.pop("after", None)
    return {
        "change_form": form,
        "change_groups": groups,
        "changes_error": error,
        "next_query": next_query,
        "first_query": first.urlencode(),
        "paged": bool(request.GET.get("after")),
    }


def _policy_to_edit(
    request: HttpRequest,
    client: EngineClient,
    snap: Snapshot,
    editable: Sequence[PolicyRow],
) -> dict[str, Any]:
    wanted = request.GET.get(EDIT_POLICY, "")
    if not wanted:
        return _policy_context(snap)
    if wanted not in {row.policy.id for row in editable if row.editable}:
        messages.error(request, NOT_EDITABLE)
        return _policy_context(snap)
    policy, error = _attempt(request, lambda: client.get_policy(wanted))
    if policy is None:
        return _policy_context(snap, policy_error=error)
    return _policy_context(
        snap,
        form=_edit_form(policy, snap),
        editing=policy.name,
    )


def _edit_form(policy: Policy, snap: Snapshot) -> StoryPolicyForm:
    return StoryPolicyForm(cast=snap.cast, initial=story_policy_initial(policy))


def _read_page(request: HttpRequest, story_id: str) -> Page:
    client = engine_client_for(request)
    story, snap, settings, settings_error, deployment_error = _read_snapshot(
        request, client, story_id
    )
    page = Page(story, snap)
    sections = page.sections
    sections.update(_settings_context(snap, deployment_error=deployment_error))
    sections["settings_error"] = settings_error
    effective = settings.effective if settings is not None else []
    inactive = settings.inactive if settings is not None else []
    sections["settings_read"] = settings
    sections["budget_source"] = (
        budget_source(settings.effective_budget.source) if settings is not None else ""
    )
    sections["effective_rows"] = policy_rows(effective, story_id)
    sections["inactive_rows"] = policy_rows(inactive, story_id)
    own = policy_rows([*effective, *inactive], story_id, editable_only=True)
    sections["story_policy_rows"] = own
    if settings is not None:
        sections.update(_policy_to_edit(request, client, snap, own))
    pinned, pinned_error = _attempt(request, lambda: client.get_pinned_memories(story_id))
    formset = None
    if pinned is not None:
        initial = [{"id": item.id, "text": item.text} for item in pinned.items]
        formset = pinned_formset(None, cap=snap.max_pinned, initial=initial)
    sections.update(
        _pinned_context(snap, formset, revision=pinned.timeline_revision if pinned else None)
    )
    sections["pinned_error"] = pinned_error
    instructions, instructions_error = _attempt(
        request, lambda: client.get_custom_instructions(story_id)
    )
    form = None
    if instructions is not None:
        form = CustomInstructionsForm(
            cap=snap.max_instructions, initial={"text": instructions.text}
        )
    sections.update(
        _instructions_context(
            snap, form, revision=instructions.timeline_revision if instructions else None
        )
    )
    sections["instructions_error"] = instructions_error
    sections.update(_changes_context(request, client, story_id))
    sections["viewpoint_text"] = viewpoint_label(
        (snap.settings or {}).get("default_output_viewpoint"), snap.cast
    )
    return page


def _render_section(
    request: HttpRequest, snap: Snapshot, section: str, context: dict[str, Any]
) -> HttpResponse:
    """One section after a refused or edited submit: no Engine read."""
    title, template = SECTIONS[section]
    full = {
        "snap": snap,
        "snapshot": snap.signed(),
        "archived_note": ARCHIVED_NOTE if snap.archived else "",
        "section_title": title,
        "section_template": template,
        **context,
    }
    return render(request, SECTION_TEMPLATE, full)


def _page_url(story_id: str, anchor: str) -> str:
    return f"{reverse('stories:settings', args=[story_id])}#{anchor}"


# --- settings -------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def settings_page(request: HttpRequest, story_id: str) -> HttpResponse:
    """GET: the whole page. POST: save the settings form (a merge patch of changed fields)."""
    if request.method == "POST":
        return _save_settings(request, story_id)
    page = _read_page(request, story_id)
    return render(request, PAGE_TEMPLATE, page.context())


def _shown(value: Any) -> str:
    return "null" if value is None else str(value)


def _place_settings_error(exc: EngineError, form: StorySettingsForm) -> bool:
    """Put a 422 on the form (``superseded`` at the non-field errors); ``False`` otherwise."""
    if exc.http_status != 422:
        return False
    if exc.details.get("reason") == SUPERSEDED_REASON:
        replacement = exc.details.get("replacement", EMPTY)
        form.add_error(None, f"Moved to engine profile overrides ({replacement})")
        return True
    apply_engine_errors(exc, form, aliases=SETTINGS_ERROR_PATHS)
    return True


def _save_settings(request: HttpRequest, story_id: str) -> HttpResponse:
    snap = _posted_snapshot(request, story_id)
    if snap.settings is None:
        messages.error(request, "The story's settings could not be read; nothing was sent.")
        return redirect(_page_url(story_id, "settings"))
    form = StorySettingsForm(
        request.POST, original=snap.settings, cast=snap.cast, deployment=snap.deployment
    )
    if not form.is_valid():
        return _render_section(request, snap, "settings", _settings_context(snap, form=form))
    patch = form.patch()
    if not patch:
        messages.info(request, "No changes — nothing was sent.")
        return redirect(_page_url(story_id, "settings"))
    key = posted_idempotency_key(request)
    try:
        saved = engine_client_for(request).patch_story_settings(
            story_id, patch, idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusal = None
        if not (isinstance(exc, EngineError) and _place_settings_error(exc, form)):
            refusal = refusal_for(exc, key)
        context = _settings_context(snap, form=form, refusal=refusal)
        return _render_section(request, snap, "settings", context)
    changed = ", ".join(f"{name}={_shown(value)}" for name, value in patch.items())
    messages.success(
        request,
        f"Saved — config revision {saved.config_revision} ({changed}), idempotency key "
        f"{saved.idempotency_key or key}",
    )
    return redirect(_page_url(story_id, "settings"))


# --- story policies -------------------------------------------------------------------------------


@require_POST
def save_policy(request: HttpRequest, story_id: str) -> HttpResponse:
    """Upsert one story or character policy: ``PATCH …/settings`` with ``{"policies": [it]}``."""
    snap = _posted_snapshot(request, story_id)
    form = StoryPolicyForm(request.POST, cast=snap.cast)
    editing = request.POST.get("policy_name", "")
    if not form.is_valid():
        context = _policy_context(snap, form=form, editing=editing)
        return _render_section(request, snap, "policy", context)
    item = form.item()
    key = posted_idempotency_key(request)
    try:
        saved = engine_client_for(request).patch_story_settings(
            story_id, {"policies": [item]}, idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusal = None
        if isinstance(exc, EngineError) and exc.code == "cannot_relax":
            form.add_error(None, f"{CANNOT_RELAX} — {exc.message}")
        elif isinstance(exc, EngineError) and exc.http_status == 422:
            apply_engine_errors(exc, form, aliases=STORY_POLICY_ERROR_PATHS)
        else:
            refusal = refusal_for(exc, key)
        context = _policy_context(snap, form=form, editing=editing, refusal=refusal)
        return _render_section(request, snap, "policy", context)
    verb = "Updated" if item.get("id") else "Added"
    messages.success(
        request,
        f"{verb} story policy “{item['name']}” — config revision {saved.config_revision}, "
        f"idempotency key {saved.idempotency_key or key}",
    )
    return redirect(_page_url(story_id, "story-policies"))


# --- pinned memories ------------------------------------------------------------------------------


def _mark_rows(exc: EngineError, rows: Sequence[forms.BaseForm], holder: forms.Form) -> None:
    """Place a pinned save's refusal on the rows it names."""
    if exc.code == "guardrail_violation":
        found = exc.details.get("violations")
        raw = [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []
        for violation in violation_rows(raw):
            for locator in violation.locators:
                index = item_index(locator)
                if index is not None and index < len(rows):
                    rows[index].add_error(None, f"Guardrail {violation.name}: {violation.message}")
    elif exc.code == "erased_subject_referenced":
        for key in ("field", "locator", "path"):
            index = item_index(exc.details.get(key))
            if index is not None and index < len(rows):
                rows[index].add_error(None, ERASED_TEXT)
                break
    elif exc.http_status == 422 and "cap" not in exc.details:
        apply_engine_errors(exc, holder, formsets={"items": list(rows)})


@require_POST
def save_pinned(request: HttpRequest, story_id: str) -> HttpResponse:
    """Add / remove / move a row (re-render, no call) or save the list (``PUT``)."""
    snap = _posted_snapshot(request, story_id)
    if wants_edit(request.POST):
        data = edit_rows(
            request.POST,
            PINNED_PREFIX,
            max_rows=snap.max_pinned,
            min_rows=0,
            new_row={"id": [""], "text": [""]},
        )
        edited = pinned_formset(data, cap=snap.max_pinned)
        context = _pinned_context(snap, edited, show_errors=False)
        return _render_section(request, snap, "pinned", context)
    formset = pinned_formset(request.POST, cap=snap.max_pinned)
    if not formset.is_valid():
        return _render_section(request, snap, "pinned", _pinned_context(snap, formset))
    key = posted_idempotency_key(request)
    try:
        written = engine_client_for(request).put_pinned_memories(
            story_id, formset.items(), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        holder = BatchErrors()
        if isinstance(exc, EngineError):
            _mark_rows(exc, formset.forms, holder)
        refusal = None if _field_error(exc) else refusal_for(exc, key)
        context = _pinned_context(snap, formset, refusal=refusal, holder=holder)
        return _render_section(request, snap, "pinned", context)
    _flash_text_write(
        request,
        "pinned memories",
        written.timeline_revision,
        written.operation_id,
        written.idempotency_key or key,
    )
    return redirect(_page_url(story_id, "pinned"))


def _field_error(exc: EngineClientError) -> bool:
    """A 422 ``validation_error`` placed on the fields (a cap's is a refusal with its facts)."""
    return (
        isinstance(exc, EngineError) and exc.code == "validation_error" and "cap" not in exc.details
    )


def _flash_text_write(
    request: HttpRequest, what: str, revision: int, operation_id: str | None, key: str
) -> None:
    if operation_id is None:
        messages.info(
            request,
            f"No change to the {what} — nothing was written (timeline revision {revision})",
        )
        return
    messages.success(
        request,
        f"Saved {what} — timeline revision {revision}, operation {operation_id}, "
        f"idempotency key {key}",
    )


# --- custom instructions --------------------------------------------------------------------------


@require_POST
def save_instructions(request: HttpRequest, story_id: str) -> HttpResponse:
    """Save the custom instructions (``PUT``; empty clears them)."""
    snap = _posted_snapshot(request, story_id)
    form = CustomInstructionsForm(request.POST, cap=snap.max_instructions)
    if not form.is_valid():
        return _render_section(request, snap, "instructions", _instructions_context(snap, form))
    key = posted_idempotency_key(request)
    try:
        written = engine_client_for(request).put_custom_instructions(
            story_id, form.cleaned_data["text"], idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusal = None
        if isinstance(exc, EngineError) and _field_error(exc):
            apply_engine_errors(exc, form)
        else:
            refusal = refusal_for(exc, key)
        context = _instructions_context(snap, form, refusal=refusal)
        return _render_section(request, snap, "instructions", context)
    _flash_text_write(
        request,
        "custom instructions",
        written.timeline_revision,
        written.operation_id,
        written.idempotency_key or key,
    )
    return redirect(_page_url(story_id, "instructions"))
