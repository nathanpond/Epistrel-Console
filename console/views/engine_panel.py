"""The Engine operations panel (#50).

- ``/engine/``: worker and timer health in detail (from #43's probe, no extra call), a story
  select for "Rebuild projections", and links to the settings and the change history.
- ``/engine/rebuild/<story id>/``: the rebuild's confirm page (GET, no Engine call) and the
  rebuild itself (POST), which redirects to ``/engine/rebuild/<console operation id>/``, the
  result rendered from the operation log's row (#44).
- ``/engine/settings/``: every deployment setting, editable (merge patch, only changed settings
  sent), and the deployment's profile defaults, read-only (``/engine/profile-defaults/edit/``,
  #84, edits them).
- ``/engine/config-changes/``: the deployment's configuration changes, grouped by revision.
"""

import logging
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.config_changes import revision_groups, setting_filter
from console.confirm import (
    ConfirmPage,
    confirm_action,
    confirm_query,
    posted_idempotency_key,
    safe_next,
    shown_name,
)
from console.context_processors import badge_probe
from console.engine.client import EngineClientError, EngineError
from console.engine.models import (
    ContentRatingSummary,
    DeploymentSettings,
    RebuildQueued,
    RebuildResult,
    StorySummary,
)
from console.engine_access import engine_client_for
from console.engine_api_check import RECORDED
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.engine_probe import HEARTBEAT_LATE_S, OVERDUE_ALERT_S, ProbeResult
from console.forms.config_changes import ConfigChangeFilterForm
from console.forms.deployment import DeploymentSettingsForm, profile_default_rows
from console.ids import new_ulid
from console.models import EngineOperation
from console.operation_log import REBUILD_KIND, queued_job_id, queued_job_text

log = logging.getLogger(__name__)

EMPTY = "—"
HOME_URL = "/engine/"
HELD = "blocked_by_erasure"
NAMES_FIELD = "story_names"  # the rebuild select's id → name map, signed
NAMES_SALT = "console.engine.story_names"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.engine.settings"
CONFIG_CHANGES_PATH = "/admin/config-changes"
# M3 #84's profile-defaults editor (``engine:profile-defaults``).
PROFILE_DEFAULTS_EDIT_URL = "/engine/profile-defaults/edit/"
BUSY_HEADING = "Story busy — a turn is running; try again"
SUPERSEDED = "superseded"
SETTINGS_ALIASES = {
    "rating_retired": "default_content_rating_id",
    "profile_deleted": "default_engine_profile_id",
}


# --- /engine/ -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class HealthRow:
    label: str
    text: str
    degraded: bool = False  # highlighted (only while the Engine is not ok)
    threshold: str = ""  # muted, beside a highlighted value


def _number(value: object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def health_detail(result: ProbeResult) -> list[HealthRow]:
    """The probe's checks as rows, each value that makes the Engine degraded (the reasons of
    #43's badge, same thresholds) marked. Nothing is marked while the Engine says ``ok``: the
    state is the Engine's."""
    checks = result.checks or {}
    flag = result.state != "healthy"
    rows: list[HealthRow] = []
    workers = checks.get("workers")
    if isinstance(workers, dict):
        alive = workers.get("alive")
        counted = isinstance(alive, int) and not isinstance(alive, bool)
        rows.append(
            HealthRow(
                "Workers alive",
                str(alive) if counted else EMPTY,
                degraded=flag and counted and alive == 0,
            )
        )
        heartbeat = _number(workers.get("last_heartbeat_s"))
        late = heartbeat is None or heartbeat > HEARTBEAT_LATE_S
        rows.append(
            HealthRow(
                "Last heartbeat",
                "never" if heartbeat is None else f"{int(heartbeat)} s ago",
                degraded=flag and late,
                threshold=f"> {HEARTBEAT_LATE_S} s",
            )
        )
    for key, label in (
        ("oldest_overdue_timer_s", "Oldest overdue timer"),
        ("oldest_overdue_job_s", "Oldest overdue job"),
    ):
        if key in checks:
            overdue = _number(checks[key])
            rows.append(
                HealthRow(
                    label,
                    "none" if overdue is None else f"{int(overdue)} s",
                    degraded=flag and overdue is not None and overdue > OVERDUE_ALERT_S,
                    threshold=f"> {OVERDUE_ALERT_S} s",
                )
            )
    pending = checks.get("erasure_pending")
    if isinstance(pending, int) and not isinstance(pending, bool):
        rows.append(HealthRow("Erasures pending", str(pending), degraded=flag and pending > 0))
    if "database" in checks:
        database = str(checks["database"])
        rows.append(HealthRow("Database", database, degraded=database not in ("ok", "skipped")))
    if result.content_logging is not None:
        rows.append(HealthRow("Content logging", "on" if result.content_logging else "off"))
    return rows


@dataclass(frozen=True)
class StoryOption:
    id: str
    name: str  # the story's name ("" when it has none)
    label: str
    disabled: bool


def story_option(story: StorySummary) -> StoryOption:
    """A rebuild-select option: held stories are listed without a name and cannot be rebuilt
    (503 ``erasure_pending``); archived ones can (maintenance is allowed there)."""
    if story.status == HELD:
        return StoryOption(story.id, "", f"(held) …{story.id[-6:]}", True)
    label = story.name or story.id
    if story.status != "active":
        label = f"{label} ({story.status})"
    return StoryOption(story.id, story.name or "", label, False)


@require_safe
def engine_home(request: HttpRequest) -> HttpResponse:
    result = badge_probe(request)  # #43's probe, shared with the badge: no new call
    stories: list[StoryOption] = []
    stories_error: ErrorPanel | None = None
    try:
        stories = [story_option(s) for s in engine_client_for(request).list_stories()]
    except EngineClientError as exc:
        log_engine_error(request, exc)
        stories_error = panel_for(exc)
    names = {s.id: s.name for s in stories if not s.disabled}
    context = {
        "result": result,
        "health_rows": health_detail(result),
        "stories": stories,
        "stories_error": stories_error,
        "story_names": signing.dumps(names, salt=NAMES_SALT, compress=True),
    }
    return render(request, "console/engine/home.html", context)


# --- rebuild ------------------------------------------------------------------------------------


@require_safe
def rebuild_pick(request: HttpRequest) -> HttpResponse:
    """The story select's GET target: on to that story's confirm page (no Engine call)."""
    story_id = request.GET.get("story", "").strip()
    if not story_id:
        messages.error(request, "Choose a story to rebuild.")
        return redirect(HOME_URL)
    try:
        names = signing.loads(request.GET.get(NAMES_FIELD, ""), salt=NAMES_SALT)
    except signing.BadSignature:
        names = {}
    name = names.get(story_id, "") if isinstance(names, dict) else ""
    url = reverse("engine:rebuild", args=[story_id])
    return redirect(f"{url}?{confirm_query(name, HOME_URL)}")


def _result_url(pk: int, name: str, next_url: str) -> str:
    query = {"name": name, **({"next": next_url} if next_url else {})}
    return f"{reverse('engine:rebuild-result', args=[pk])}?{urlencode(query)}"


@require_http_methods(["GET", "HEAD", "POST"])
def rebuild(request: HttpRequest, story_id: str) -> HttpResponse:
    name = shown_name(request)
    label = name or story_id
    back = safe_next(request, HOME_URL)
    page = ConfirmPage(
        title="Rebuild projections",
        question="Rebuild {name}'s projections from its event log. Turns are refused while it "
        "runs.",
        emphasis=label,
        confirm_label="Rebuild projections",
        cancel_url=back,
        facts=(("Story id", story_id),),
        hidden={"name": name},
    )

    def perform(key: str) -> HttpResponse:
        try:
            outcome = engine_client_for(request).rebuild_story(story_id, idempotency_key=key)
        except EngineError as exc:
            if exc.http_status != 423:
                raise
            log_engine_error(request, exc)
            context = {
                "heading": BUSY_HEADING,
                "engine_error": panel_for(exc),
                "retry_after": exc.retry_after,
                "label": label,
                "back": back,
            }
            return render(request, "console/engine/rebuild_busy.html", context)
        if isinstance(outcome, RebuildQueued):
            messages.info(request, f"{label}: {queued_job_text(REBUILD_KIND, outcome.job_id)}.")
        else:
            messages.success(
                request,
                f"Rebuilt {label}'s projections — operation {outcome.operation_id}, revision "
                f"{outcome.timeline_revision}, idempotency key {outcome.idempotency_key or key}",
            )
        row = (
            EngineOperation.objects.filter(kind=REBUILD_KIND, idempotency_key=key)
            .order_by("-id")
            .first()
        )
        next_url = back if back != HOME_URL else ""
        if row is None:  # the operation log could not write its row: show the answer here
            return _render_result(request, label, next_url, outcome=outcome)
        return redirect(_result_url(row.pk, name, next_url))

    return confirm_action(request, page, perform)


def _render_result(
    request: HttpRequest,
    label: str,
    next_url: str,
    *,
    outcome: RebuildResult | RebuildQueued | None,
    row: EngineOperation | None = None,
) -> HttpResponse:
    context: dict[str, Any] = {
        "label": label,
        "row": row,
        "result": outcome if isinstance(outcome, RebuildResult) else None,
        "job_text": (
            queued_job_text(REBUILD_KIND, outcome.job_id)
            if isinstance(outcome, RebuildQueued)
            else ""
        ),
        "back_url": next_url or HOME_URL,
        "back_label": "Back to story" if next_url else "Back to Engine",
    }
    return render(request, "console/engine/rebuild_result.html", context)


@require_safe
def rebuild_result(request: HttpRequest, pk: int) -> HttpResponse:
    """One rebuild's outcome, from the operation log's row (no Engine call)."""
    row = get_object_or_404(EngineOperation, pk=pk, kind=REBUILD_KIND)
    outcome: RebuildResult | RebuildQueued | None = None
    job_id = queued_job_id(row)
    if job_id is not None:
        outcome = RebuildQueued.from_json(row.response)
    elif row.http_status == 200:
        outcome = RebuildResult.from_json(row.response)
    next_url = safe_next(request, "")
    label = request.GET.get("name") or row.story_id or EMPTY
    return _render_result(request, label, next_url, outcome=outcome, row=row)


# --- deployment settings ------------------------------------------------------------------------


def rating_options(ratings: list[ContentRatingSummary], current: str) -> list[tuple[str, str]]:
    """The default-rating select: the non-retired ratings; a retired (or unknown) current
    default is one extra option, labelled so."""
    options = [(r.id, r.name) for r in ratings if not r.retired]
    if current and current not in {value for value, _ in options}:
        retired = next((r for r in ratings if r.id == current), None)
        label = f"{retired.name} (retired)" if retired else f"{current} (unknown)"
        options.append((current, label))
    return options


@dataclass(frozen=True)
class SettingsPage:
    settings: dict[str, Any]  # ``SettingsBody`` as read
    ratings: list[tuple[str, str]] | None  # None: the ratings read failed

    def signed(self) -> str:
        return signing.dumps(
            {"settings": self.settings, "ratings": self.ratings}, salt=SNAPSHOT_SALT, compress=True
        )

    @classmethod
    def unsigned(cls, value: str) -> SettingsPage | None:
        try:
            raw = signing.loads(value, salt=SNAPSHOT_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(raw, dict) or DeploymentSettings.from_json(raw.get("settings")) is None:
            return None
        ratings = raw.get("ratings")
        options = [(str(v), str(label)) for v, label in ratings] if ratings is not None else None
        return cls(raw["settings"], options)

    @property
    def revision(self) -> int:
        value = self.settings.get("config_revision")
        return value if isinstance(value, int) else 0


def _read_settings(request: HttpRequest) -> SettingsPage:
    """``GET /admin/settings`` (errors escape) and the ratings (a failure degrades the field)."""
    client = engine_client_for(request)
    settings = client.get_deployment_settings()
    try:
        ratings: list[tuple[str, str]] | None = rating_options(
            client.list_content_ratings(include_retired=True), settings.default_content_rating_id
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        ratings = None
    return SettingsPage(asdict(settings), ratings)


def _render_settings(
    request: HttpRequest,
    page: SettingsPage | None,
    *,
    form: DeploymentSettingsForm | None = None,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    if page is not None and form is None:
        form = DeploymentSettingsForm(original=page.settings, ratings=page.ratings)
    context: dict[str, Any] = {
        "page": page,
        "form": form,
        "engine_error": engine_error,
        "idempotency_key": new_ulid(),  # minted at each render (#43)
        "snapshot": page.signed() if page is not None else "",
        "profile_defaults": profile_default_rows(page.settings.get("profile_defaults"))
        if page is not None
        else [],
        "profile_defaults_edit_url": PROFILE_DEFAULTS_EDIT_URL,
    }
    return render(request, "console/engine/settings.html", context)


def _place_settings_error(exc: EngineError, form: DeploymentSettingsForm) -> bool:
    """Put a 422/409 on the form's fields; ``False`` when it is not a form error."""
    details = exc.details
    if exc.http_status == 422 and details.get("reason") == SUPERSEDED:
        form.add_error(None, f"Moved to profile defaults: {details.get('replacement', EMPTY)}")
        return True
    if exc.http_status == 422:
        apply_engine_errors(exc, form, aliases=SETTINGS_ALIASES)
        return True
    if exc.code == "listing_status_in_use":
        count = details.get("count")
        held = f" by {count} catalog entries" if count is not None else ""
        form.add_error(
            "listing_status_values",
            f"“{details.get('value', EMPTY)}” is still held{held}: {exc.message}",
        )
        return True
    if exc.code == "feedback_tag_in_use":
        form.add_error("feedback_tags", f"{_tags_in_use(details.get('in_use'))}: {exc.message}")
        return True
    return False


def _tags_in_use(in_use: object) -> str:
    """``details.in_use`` (each tag with its ``current`` and ``history`` counts) as text."""
    entries: list[str] = []
    items = in_use.items() if isinstance(in_use, dict) else []
    if isinstance(in_use, list):
        items = [(i.get("tag"), i) for i in in_use if isinstance(i, dict)]
    for tag, counts in items:
        counts = counts if isinstance(counts, dict) else {}
        entries.append(
            f"{tag} (current {counts.get('current', EMPTY)}, history "
            f"{counts.get('history', EMPTY)})"
        )
    return "Still in use: " + (", ".join(entries) if entries else EMPTY)


@require_http_methods(["GET", "HEAD", "POST"])
def deployment_settings(request: HttpRequest) -> HttpResponse:
    """GET: every setting from ``GET /admin/settings`` (a failed read shows the error panel,
    never a stale form). POST: validate, then ``PUT`` only the changed settings."""
    if request.method != "POST":
        try:
            page = _read_settings(request)
        except EngineClientError as exc:
            log_engine_error(request, exc)
            return _render_settings(request, None, engine_error=panel_for(exc))
        return _render_settings(request, page)

    page = SettingsPage.unsigned(request.POST.get(SNAPSHOT_FIELD, "")) or _read_settings(request)
    form = DeploymentSettingsForm(request.POST, original=page.settings, ratings=page.ratings)
    if not form.is_valid():
        return _render_settings(request, page, form=form)
    patch = form.patch()
    if not patch:
        messages.info(request, "No changes — nothing was sent.")
        return redirect(reverse("engine:settings"))
    key = posted_idempotency_key(request)
    try:
        saved = engine_client_for(request).update_admin_settings(patch, idempotency_key=key)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and _place_settings_error(exc, form):
            return _render_settings(request, page, form=form)
        return _render_settings(request, page, form=form, engine_error=panel_for(exc))
    text = (
        f"Saved — config revision {saved.config_revision} "
        f"({', '.join(patch)}), idempotency key {saved.idempotency_key or key}"
    )
    if saved.config_revision > page.revision + 1:
        text += (
            ". Settings were also changed elsewhere since you loaded this page; see Config changes"
        )
    messages.success(request, text)
    return redirect(reverse("engine:settings"))


# --- configuration changes ----------------------------------------------------------------------


@require_safe
def config_changes(request: HttpRequest) -> HttpResponse:
    """``GET /admin/config-changes`` with the filters; an invalid filter makes no call."""
    form = ConfigChangeFilterForm(
        request.GET or None, settings=setting_filter(RECORDED, CONFIG_CHANGES_PATH)
    )
    groups: list[Any] = []
    next_query = ""
    engine_error: ErrorPanel | None = None
    valid = form.is_valid() if request.GET else True
    if valid:
        query = form.query() if request.GET else {}
        try:
            page = engine_client_for(request).list_admin_config_changes(
                setting=query.get("setting"),
                change_class=query.get("change_class"),
                since=query.get("since"),
                after=query.get("after"),
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            groups = revision_groups(page.items)
            if page.next:
                params = request.GET.copy()
                params["after"] = page.next
                next_query = params.urlencode()
    first = request.GET.copy()
    first.pop("after", None)
    context = {
        "form": form,
        "change_groups": groups,
        "engine_error": engine_error,
        "next_query": next_query,
        "first_query": first.urlencode(),
        "paged": bool(request.GET.get("after")),
    }
    return render(request, "console/engine/config_changes.html", context)
