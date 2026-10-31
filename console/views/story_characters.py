"""Add a character to a running story, and promote one of its characters to the catalog (#72).

``/stories/<id>/characters/new/`` (``stories:character-new``) reads the story once
(``viewpoint=omniscient``: the header, its archived state, its cast count and its revision) and
shows the add form: name, definition, true age, safe fallback text, deflection style and the
images (#71's slots, one ordered list). Submit POSTs ``/stories/{id}/characters`` with the
revision the form was opened at as ``X-Epistrel-Expected-Revision``, and lands on the play view
with "Added <name> (cast #<ordinal>) — timeline revision <n> — key <key>". A refusal re-renders
the form with the operator's input and a one-line message per code (the rest go to #43's panel).
An archived story's GET redirects to the play view; a forced POST still reaches the Engine.

``/stories/<id>/characters/<cid>/promote/`` (``stories:character-promote``) reads the story once
(omniscient; the character's name, ``is_player`` and the story's rating) and the ratings, and
shows the promote form: name (the character's), owner (the acting principal) and content rating
(first option "The story's rating (<name>)", which leaves the field out). Submit POSTs
``…/promote`` (no re-read, no expected revision: a promotion never moves the revision, so it is
allowed on archived stories and during a turn) and redirects to the new draft's edit page (#66)
with "Promoted <name> → draft <catalog_id> (revision <n>)" and one flash line per warning. A
player character's GET redirects to the play view; a forced POST shows the Engine's 422.

Both pages carry a signed snapshot of what they read, so a refused POST re-renders without an
Engine read.

#61 (C8): the cast panel's role form POSTs ``…/characters/<cid>/participation/``
(``stories:character-participation``: ``PATCH …/participation``) and its weight button
``…/characters/<cid>/weight/`` (``stories:character-weight``: ``POST …/weight``). Both
POST-redirect-GET to the play view's ``#cast-<cid>`` with a flash (convention 23): the revision
on success, C5's wording (verb phrase "change roles and weights") on a refusal, else "Couldn't
change <name>: <status> <code>". A role equal to the row's validly signed ``shown`` makes no call.
The add form sends ``participation`` only when a role is chosen.
"""

import html
from dataclasses import asdict, dataclass, field
from typing import Any

from django.contrib import messages
from django.core import signing
from django.forms.utils import ErrorDict
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.http import require_http_methods, require_POST

from console.acting import acting_principal
from console.cast import OMNISCIENT
from console.cast_panel import CAST_MAX, Shown
from console.confirm import posted_idempotency_key
from console.engine.choices import RETIRED_SUFFIX, RatingChoices, error_text, rating_choices
from console.engine.client import EngineClientError, EngineError
from console.engine.models import ReportEntry, Story
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors, engine_field_errors
from console.forms.media_picker import (
    MEDIA_RETIRED,
    RETIRED_TEXT,
    STORY_CHARACTER_SECTIONS,
    STORY_IMAGES,
    MediaSections,
    media_op,
)
from console.forms.principal import remember_principals
from console.forms.story_character import AddCharacterForm, ParticipationForm, PromoteForm
from console.ids import new_ulid
from console.inspect import short_id
from console.participation import (
    CHOOSE_ROLE,
    PLAYER_NO_ROLE,
    is_weight,
    role_text,
    weight_label,
)
from console.turn_reports import violation_rows
from console.views.play import error_inspector
from console.views.story_settings import Refusal
from console.wording import UNKNOWN_CHARACTER, write_refusal

SNAPSHOT_FIELD = "snapshot"
ADD_SALT = "console.story_characters.add"
PROMOTE_SALT = "console.story_characters.promote"
ADD_TEMPLATE = "console/stories/character_new.html"
PROMOTE_TEMPLATE = "console/stories/promote.html"
EMPTY = "—"

ARCHIVED_ADD = "The story is archived — unarchive to add characters"
PAUSED_ADD = "The story is paused — resume it to add characters"
CAST_FULL = f"The cast is full ({CAST_MAX} characters)"
DUPLICATE_NAME = "This story already has a character named {name}"
GUARDRAIL_ADD = "Rejected by a guardrail — the character was not added"
BUDGET_ADD = "The story's budget is below the safety minimum for the input gate"
ERASED = "Refers to an erased subject"
REVISION_CHANGED = (
    "The story changed since this form opened (now revision {now}) — check and submit again"
)
PLAYER_PROMOTE = "Player characters play a persona and can't be promoted"
NOT_IN_CAST = "This story has no character {id}"
RATING_RETIRED = "That rating is retired — choose another"
STORY_RATING = "The story's rating"
PLAYER_CHARACTER = "player_character"
# ``details`` the Engine sets on the two cast refusals (core.stories._cast_full/_name_taken).
CAST_FULL_REASONS = frozenset({"cast_full"})
CAST_MAX_CAP = "cast_max"
DUPLICATE_REASONS = frozenset({"duplicate", "duplicate_name"})
# Promote warnings worded by the Console (the rest keep the Engine's text).
DEFINITION_TRUNCATED = "definition_truncated"
TRUNCATED_TEXT = "Definition cut to 4000 characters in background flavor"
IMAGE_LEFT_OUT = "Image {id} was retired and left out"
# #61: the add body's ``participation`` failures land on the role select.
PARTICIPATION_PATHS = {
    path: "participation_role"
    for path in ("participation", "participation.role", "participation.fixed")
}
CAST_VERB = "change roles and weights"
SAME_ROLE = "{name} already has that role"
CHOOSE_WEIGHT = "Choose a weight."
IS_PLAYER = "is_player"


def _line(exc: EngineError) -> str:
    return f"HTTP {exc.http_status} {exc.code}: {exc.message}"


def _busy(exc: EngineError, key: str) -> Refusal:
    """#48's busy line: the notice, the operation and Retry-After."""
    busy = error_inspector(exc, key)
    return Refusal(notice=busy.notice, line=_line(exc), facts=busy.facts)


def _erased(exc: EngineError) -> Refusal:
    """ "Refers to an erased subject" with the subject's id when the details name one, else
    the details as a definition list."""
    details = exc.details
    subject = next(
        (details[k] for k in ("subject_id", "id") if isinstance(details.get(k), str)), None
    )
    if subject is not None:
        return Refusal(notice=f"{ERASED} ({subject})", line=_line(exc))
    facts = tuple((str(k), EMPTY if v is None else str(v)) for k, v in details.items())
    return Refusal(notice=ERASED, line=_line(exc), facts=facts)


def _detail(details: dict[str, Any], key: str) -> str:
    value = details.get(key)
    return EMPTY if value is None else str(value)


# --- the story as the pages carry it ----------------------------------------------------------


@dataclass(frozen=True)
class StoryHeader:
    """What the add page read of the story (``name`` ``None`` when unnamed or not read)."""

    story_id: str
    name: str | None = None
    revision: int | None = None
    cast_count: int | None = None
    status: str = ""

    @classmethod
    def of(cls, story: Story) -> StoryHeader:
        return cls(story.id, story.name, story.timeline_revision, len(story.cast), story.status)

    @property
    def archived(self) -> bool:
        return self.status == "archived"

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=ADD_SALT)

    @classmethod
    def unsigned(cls, value: str | None, story_id: str) -> StoryHeader | None:
        try:
            raw = signing.loads(value or "", salt=ADD_SALT)
            found = cls(**raw)
        except signing.BadSignature, TypeError:
            return None
        return found if found.story_id == story_id else None


def _play_url(story_id: str) -> str:
    return reverse("stories:detail", args=[story_id])


def _read_story(request: HttpRequest, story_id: str) -> Story:
    return engine_client_for(request).get_story(story_id, viewpoint=OMNISCIENT)


# --- add --------------------------------------------------------------------------------------


def _sections(data: QueryDict | None = None) -> MediaSections:
    sections = MediaSections(
        data if data is not None else QueryDict(),
        STORY_CHARACTER_SECTIONS,
        original={STORY_IMAGES: []},
        infos={},
    )
    if data is None:
        sections.quiet()
    return sections


def _quiet(form: AddCharacterForm) -> AddCharacterForm:
    """Show a bound form's state without validating it (an image row button)."""
    quieted: Any = form
    quieted._errors = ErrorDict()
    return form


def _render_add(
    request: HttpRequest,
    header: StoryHeader,
    form: AddCharacterForm | None,
    sections: MediaSections | None = None,
    *,
    refusal: Refusal | None = None,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    action_url = reverse("stories:character-new", args=[header.story_id])
    context: dict[str, Any] = {
        "header": header,
        "form": form,
        "media": (sections or _sections()).by_path(),
        "snapshot": header.signed(),
        "idempotency_key": new_ulid(),
        "action_url": action_url,
        "play_url": _play_url(header.story_id),
        "cast_max": CAST_MAX,
        "refusal": refusal,
        "engine_error": engine_error,
    }
    return render(request, ADD_TEMPLATE, context)


@require_http_methods(["GET", "HEAD", "POST"])
def character_new(request: HttpRequest, story_id: str) -> HttpResponse:
    """The add-character form (GET), or the add itself (POST)."""
    if request.method == "POST":
        return _add(request, story_id)
    try:
        story = _read_story(request, story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return _render_add(request, StoryHeader(story_id), None, engine_error=panel_for(exc))
    header = StoryHeader.of(story)
    if header.archived:
        messages.error(request, ARCHIVED_ADD)
        return redirect(_play_url(story_id))
    form = AddCharacterForm(initial={"expected_revision": story.timeline_revision})
    return _render_add(request, header, form)


def _add(request: HttpRequest, story_id: str) -> HttpResponse:
    header = StoryHeader.unsigned(request.POST.get(SNAPSHOT_FIELD), story_id) or StoryHeader(
        story_id
    )
    edited = media_op(request.POST, STORY_CHARACTER_SECTIONS)
    if edited is not None:
        sections = _sections(edited)
        sections.quiet()
        return _render_add(request, header, _quiet(AddCharacterForm(edited)), sections)
    form = AddCharacterForm(request.POST)
    sections = _sections(request.POST)
    if not all([form.is_valid(), sections.is_valid()]):
        return _render_add(request, header, form, sections)
    images = sections.formsets[STORY_IMAGES].values()
    key = posted_idempotency_key(request)
    try:
        added = engine_client_for(request).add_story_character(
            story_id, **form.add_kwargs(images), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        form, refusal = _add_refused(exc, form, sections, key, request.POST)
        if isinstance(refusal, ErrorPanel):
            return _render_add(request, header, form, sections, engine_error=refusal)
        return _render_add(request, header, form, sections, refusal=refusal)
    role = form.participation
    messages.success(
        request,
        f"Added {added.name} (cast #{added.ordinal}) — timeline revision "
        f"{added.timeline_revision} — key {added.idempotency_key or key}"
        + (f" — role {role_text(*role)}" if role is not None else ""),
    )
    return redirect(_play_url(story_id))


def _add_refused(
    exc: EngineClientError,
    form: AddCharacterForm,
    sections: MediaSections,
    key: str,
    posted: QueryDict,
) -> tuple[AddCharacterForm, Refusal | ErrorPanel | None]:
    """The form to re-render and what to show above it: a refusal in the AC's words, the
    panel, or nothing (the message sits beside a field)."""
    if not isinstance(exc, EngineError):
        return form, panel_for(exc)
    details = exc.details
    if exc.code == "validation_error":
        _place_validation(exc, form, sections)
        return form, None
    if exc.code == MEDIA_RETIRED:
        _place_retired(exc, form, sections)
        return form, None
    if exc.code == "guardrail_violation":
        found = details.get("violations")
        raw = [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []
        return form, Refusal(
            notice=GUARDRAIL_ADD, line=_line(exc), violations=tuple(violation_rows(raw))
        )
    if exc.code == "budget_below_safety_minimum":
        facts = (
            ("Minimum required", _detail(details, "minimum_required")),
            ("Max calls", _detail(details, "max_calls")),
        )
        return form, Refusal(notice=BUDGET_ADD, line=_line(exc), facts=facts)
    if exc.code == "story_archived":
        return form, Refusal(notice=ARCHIVED_ADD, line=_line(exc))
    if exc.code == "story_paused":
        return form, Refusal(notice=PAUSED_ADD, line=_line(exc))
    if exc.code == "erased_subject_referenced":
        return form, _erased(exc)
    if exc.code == "story_busy":
        return form, _busy(exc, key)
    if exc.code == "revision_conflict":
        now = details.get("current_revision")
        shown = now if isinstance(now, int) else "?"
        return _at_revision(form, posted, now), Refusal(notice=REVISION_CHANGED.format(now=shown))
    return form, panel_for(exc)


def _at_revision(form: AddCharacterForm, posted: QueryDict, revision: object) -> AddCharacterForm:
    """The form re-bound with the story's current revision as the expected one, the input
    kept: a second submit is checked against the revision the operator now knows of."""
    if not isinstance(revision, int):
        return form
    data = posted.copy()
    data["expected_revision"] = str(revision)
    rebound = AddCharacterForm(data)
    rebound.is_valid()
    return rebound


def _place_validation(exc: EngineError, form: AddCharacterForm, sections: MediaSections) -> None:
    """A full cast or a name the story has, in the AC's words; otherwise each failure beside
    its field (``images[i]`` at that row), else the Engine's message above the form."""
    details = exc.details
    reason = details.get("reason")
    if reason in CAST_FULL_REASONS or details.get("cap") == CAST_MAX_CAP:
        form.add_error(None, CAST_FULL)
        return
    if reason in DUPLICATE_REASONS:
        form.add_error("name", DUPLICATE_NAME.format(name=form.cleaned_data.get("name", "")))
        return
    rows = sections.formsets[STORY_IMAGES].sent_forms()
    if not apply_engine_errors(exc, form, {STORY_IMAGES: rows}, PARTICIPATION_PATHS):
        form.add_error(None, exc.message)


def _place_retired(exc: EngineError, form: AddCharacterForm, sections: MediaSections) -> None:
    """``media_retired`` at its image row ("This asset is retired — choose another")."""
    where = exc.details.get("field")
    sent = sections.formsets[STORY_IMAGES].sent_forms()
    if where == f"body.{STORY_IMAGES}" and len(sent) == 1:  # one image: no index
        where = f"body.{STORY_IMAGES}[0]"
    if isinstance(where, str) and sections.place(where, exc.message, exc.code):
        return
    media_id = exc.details.get("media_id")
    if isinstance(media_id, str) and sections.place_media_id(media_id, RETIRED_TEXT):
        return
    form.add_error(None, RETIRED_TEXT)


# --- promote ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class PromoteSnapshot:
    """What the promote page read: the story and character it names, the rating select's
    options (the default's label first) and why the ratings could not be read."""

    story_id: str
    character_id: str
    story_name: str | None = None
    character_name: str = ""
    choices: list[tuple[str, str]] = field(default_factory=list)
    ratings_note: str = ""

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=PROMOTE_SALT)

    @classmethod
    def unsigned(
        cls, value: str | None, story_id: str, character_id: str
    ) -> PromoteSnapshot | None:
        try:
            raw = signing.loads(value or "", salt=PROMOTE_SALT)
            raw["choices"] = [(str(k), str(v)) for k, v in raw["choices"]]
            found = cls(**raw)
        except signing.BadSignature, KeyError, TypeError, ValueError:
            return None
        if (found.story_id, found.character_id) != (story_id, character_id):
            return None
        return found


def _rating_select(
    ratings: RatingChoices, story_rating: tuple[str, str] | None
) -> tuple[list[tuple[str, str]], str]:
    """The rating select's options and the note beside it. The first option (value ``""``)
    is the story's rating: "(<name>)", "(<name>, retired)" when retired, "(<id>)" when the
    list does not hold it; then the non-retired ratings. A failed read offers only the default
    ("The story's rating") with the ratings note."""
    if ratings.error is not None:
        return [("", STORY_RATING)], ratings.unavailable_text
    listed = dict(ratings.choices)
    default = STORY_RATING
    if story_rating is not None:
        rating_id, name = story_rating
        label = listed.get(rating_id)
        if label is None:
            default = f"{STORY_RATING} ({rating_id})"
        elif label.endswith(RETIRED_SUFFIX):
            default = f"{STORY_RATING} ({name}, retired)"
        else:
            default = f"{STORY_RATING} ({name})"
    live = [(k, v) for k, v in ratings.choices if not v.endswith(RETIRED_SUFFIX)]
    return [("", default), *live], ""


def _promote_url(story_id: str, character_id: str) -> str:
    return reverse("stories:character-promote", args=[story_id, character_id])


def _render_promote(
    request: HttpRequest,
    snap: PromoteSnapshot,
    form: PromoteForm | None,
    *,
    refusal: Refusal | None = None,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    context: dict[str, Any] = {
        "snap": snap,
        "form": form,
        "snapshot": snap.signed(),
        "idempotency_key": new_ulid(),
        "action_url": _promote_url(snap.story_id, snap.character_id),
        "play_url": _play_url(snap.story_id),
        "refusal": refusal,
        "engine_error": engine_error,
    }
    return render(request, PROMOTE_TEMPLATE, context)


@require_http_methods(["GET", "HEAD", "POST"])
def character_promote(request: HttpRequest, story_id: str, character_id: str) -> HttpResponse:
    """The promote form (GET), or the promotion itself (POST)."""
    if request.method == "POST":
        return _promote(request, story_id, character_id)
    client = engine_client_for(request)
    try:
        story = client.get_story(story_id, viewpoint=OMNISCIENT)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        snap = PromoteSnapshot(story_id, character_id)
        return _render_promote(request, snap, None, engine_error=panel_for(exc))
    character = next((c for c in story.cast if c.id == character_id), None)
    if character is None:
        messages.error(request, NOT_IN_CAST.format(id=character_id))
        return redirect(_play_url(story_id))
    if character.is_player:
        messages.error(request, PLAYER_PROMOTE)
        return redirect(_play_url(story_id))
    rating = story.content_rating
    choices, note = _rating_select(
        rating_choices(client, include_retired=True), (rating.id, rating.name)
    )
    snap = PromoteSnapshot(story_id, character_id, story.name, character.name, choices, note)
    initial = {"name": character.name, "owner": acting_principal(request), "content_rating_id": ""}
    return _render_promote(request, snap, PromoteForm(initial=initial, rating_choices=choices))


def _promote(request: HttpRequest, story_id: str, character_id: str) -> HttpResponse:
    snap = PromoteSnapshot.unsigned(request.POST.get(SNAPSHOT_FIELD), story_id, character_id)
    client = engine_client_for(request)
    if snap is None:  # not from the page: the ratings again (never the story)
        choices, note = _rating_select(rating_choices(client, include_retired=True), None)
        snap = PromoteSnapshot(story_id, character_id, choices=choices, ratings_note=note)
    form = PromoteForm(request.POST, rating_choices=snap.choices)
    if not form.is_valid():
        return _render_promote(request, snap, form)
    remember_principals(form)
    key = posted_idempotency_key(request)
    try:
        promoted = client.promote_story_character(
            story_id, character_id, **form.promote_kwargs(), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusal = _promote_refused(exc, form, key)
        if isinstance(refusal, ErrorPanel):
            return _render_promote(request, snap, form, engine_error=refusal)
        return _render_promote(request, snap, form, refusal=refusal)
    messages.success(
        request,
        f"Promoted {form.cleaned_data['name']} → draft {promoted.catalog_id} "
        f"(revision {promoted.draft_revision})",
    )
    for warning in promoted.warnings:
        messages.warning(request, warning_line(warning))
    return redirect(reverse("catalog_characters:edit", args=[promoted.catalog_id]))


def _promote_refused(
    exc: EngineClientError, form: PromoteForm, key: str
) -> Refusal | ErrorPanel | None:
    if not isinstance(exc, EngineError):
        return panel_for(exc)
    if exc.code == "rating_retired":
        form.add_error("content_rating_id", RATING_RETIRED)
        return None
    if exc.code == "story_busy":
        return _busy(exc, key)
    if exc.code == "erased_subject_referenced":
        return _erased(exc)
    if exc.http_status == 422 and exc.details.get("reason") == PLAYER_CHARACTER:
        return Refusal(notice=exc.message, line=_line(exc))
    if exc.code == "validation_error":  # beside the form's own fields; the rest: the panel
        paths = [failure.path.removeprefix("body.") for failure in engine_field_errors(exc)]
        if any(path in form.fields for path in paths):
            apply_engine_errors(exc, form)
            return None
    return panel_for(exc)


def warning_line(entry: ReportEntry) -> str:
    """A promotion warning as a flash line, "Warning <code>: <text> (<location>)" in plain
    text: ``definition_truncated`` and ``media_retired`` worded by the Console, any other code
    with the Engine's text (its ``text``, else its ``rule``)."""
    if entry.code == DEFINITION_TRUNCATED:
        text = TRUNCATED_TEXT
    elif entry.code == MEDIA_RETIRED:
        media_id = entry.source or entry.location.media_id or EMPTY
        text = IMAGE_LEFT_OUT.format(id=media_id)
    else:
        text = entry.text or entry.rule or EMPTY
    location = render_to_string("console/_location.html", {"location": entry.location})
    return f"Warning {entry.code}: {text} ({html.unescape(strip_tags(location)).strip()})"


# --- role and weight (#61) ----------------------------------------------------------------------


def cast_row_url(story_id: str, character_id: str) -> str:
    """The play view at the character's cast row (``#cast-<cid>``), no query."""
    return f"{_play_url(story_id)}#cast-{character_id}"


def cast_refusal_text(exc: EngineClientError, story_id: str, name: str) -> str:
    """A refused role or weight write's flash: 404 → ``UNKNOWN_CHARACTER``, 422 ``is_player`` →
    ``PLAYER_NO_ROLE``, C5's wording, else "Couldn't change <name>: <status> <code>"."""
    if isinstance(exc, EngineError):
        if exc.http_status == 404:
            return UNKNOWN_CHARACTER
        if exc.http_status == 422 and exc.details.get("reason") == IS_PLAYER:
            return PLAYER_NO_ROLE
    refusal = write_refusal(exc, story_id, CAST_VERB)
    if refusal is not None:
        return refusal.text  # a PAUSED link can't survive the flash
    return f"Couldn't change {name}: {error_text(exc)}"


def _cast_refused(
    request: HttpRequest, exc: EngineClientError, story_id: str, character_id: str, name: str
) -> HttpResponse:
    log_engine_error(request, exc)
    messages.error(request, cast_refusal_text(exc, story_id, name))
    return redirect(cast_row_url(story_id, character_id))


def _shown(request: HttpRequest, story_id: str, character_id: str) -> tuple[Shown | None, str]:
    shown = Shown.unsigned(request.POST.get("shown"), story_id, character_id)
    return shown, shown.name if shown is not None else short_id(character_id)


@require_POST
def character_participation(request: HttpRequest, story_id: str, character_id: str) -> HttpResponse:
    """Set the character's participation role (the cast panel's role form)."""
    back = cast_row_url(story_id, character_id)
    shown, name = _shown(request, story_id, character_id)
    form = ParticipationForm(request.POST)
    if not form.is_valid():
        messages.error(request, CHOOSE_ROLE)
        return redirect(back)
    role, fixed = form.role_and_fixed
    if shown is not None and shown.same_role(role, fixed):
        messages.info(request, SAME_ROLE.format(name=name))
        return redirect(back)
    try:
        result = engine_client_for(request).set_participation(
            story_id,
            character_id,
            role=role,
            fixed=fixed,
            idempotency_key=posted_idempotency_key(request),
        )
    except EngineClientError as exc:
        return _cast_refused(request, exc, story_id, character_id, name)
    character = result.character
    stored = character.participation
    text = role_text(stored.role, stored.fixed) if stored is not None else role_text(role, fixed)
    messages.success(
        request,
        f"Set {character.name or name}'s role to {text} — config revision {result.config_revision}",
    )
    return redirect(back)


@require_POST
def character_weight(request: HttpRequest, story_id: str, character_id: str) -> HttpResponse:
    """Make the character minor or major (the cast panel's weight button). Always calls."""
    back = cast_row_url(story_id, character_id)
    _, name = _shown(request, story_id, character_id)
    weight = request.POST.get("weight")
    if not is_weight(weight):
        messages.error(request, CHOOSE_WEIGHT)
        return redirect(back)
    try:
        result = engine_client_for(request).set_weight(
            story_id, character_id, weight, idempotency_key=posted_idempotency_key(request)
        )
    except EngineClientError as exc:
        return _cast_refused(request, exc, story_id, character_id, name)
    character = result.character
    messages.success(
        request,
        f"{character.name or name} is now {weight_label(character.weight or weight)} — "
        f"timeline revision {result.timeline_revision}",
    )
    return redirect(back)
