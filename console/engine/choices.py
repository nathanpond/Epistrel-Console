"""Shared lookups for M3 forms and pages (#64, M3 conventions 13 and 14).

- :func:`rating_choices` is the one content-rating select every M3 form uses (rating ids come
  only from it; a "default" option omits the field). One ``GET /admin/content-ratings`` per call;
  read it at most once per request and never cache it across requests.
- :func:`engine_profile_choices` is the engine-profile select (#68's recommendation): every
  non-deleted profile, labelled by name, following cursors up to :data:`PROFILES_MAX`.
- :func:`story_labels` names the stories a page mentions by id (at most ``cap`` reads, on the
  shared fan-out pool, ``console.engine.pool``).
- :func:`subject_choices` is the depicted-subject picker's options (#70): catalog characters
  (published and draft-only) and every persona, both read on the fan-out pool.
- :func:`media_lookups` reads each asset's metadata on the fan-out pool (#70; #71 shows the
  status of attached media with it).
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from console.engine.client import (
    ENGINE_PROFILES_PAGE,
    EngineClient,
    EngineClientError,
    EngineError,
    EngineUnreachable,
)
from console.engine.models import ContentRatingSummary, EngineProfile, MediaAsset
from console.engine.pool import fan_out
from console.entities import label

RETIRED_SUFFIX = " (retired)"
MISSING_SUFFIX = " (missing)"
DELETED_SUFFIX = " (deleted)"
PROFILES_MAX = 500  # "All X" selects stop here (#64 convention 6)
TOO_MANY = "Too many to list — enter the id"
STORY_LABEL_CAP = 25
ERASURE_PENDING = "erasure_pending"


def error_text(exc: EngineClientError) -> str:
    """``<status> <code>`` for an Engine error, ``unreachable (<reason>)`` without an answer."""
    if isinstance(exc, EngineError):
        return f"{exc.http_status} {exc.code}"
    if isinstance(exc, EngineUnreachable):
        return f"unreachable ({exc.reason})"
    return type(exc).__name__


@dataclass(frozen=True)
class RatingChoices:
    """A rating select's options, ``(rating id, label)`` in ``display_order``, or the read's
    ``error``. When ``error`` is set the form shows :attr:`unavailable_text` in place of the
    select (and offers no rating field)."""

    choices: list[tuple[str, str]]
    error: EngineClientError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def unavailable_text(self) -> str:
        if self.error is None:
            return ""
        return f"Ratings unavailable: {error_text(self.error)} — reload to retry"


def rating_choices(
    client: EngineClient, *, current: str | None = None, include_retired: bool = False
) -> RatingChoices:
    """The content-rating select's options, in ``display_order``, labelled by name.

    Non-retired ratings only, unless ``include_retired`` (list filters), where retired ones are
    labelled "<name> (retired)". ``current`` (the saved rating id) is always kept: as
    "<name> (retired)" when it is retired, as "<id> (missing)" when the Engine no longer lists
    it. One read (retired included, so a retired ``current`` keeps its name). A failed read
    returns its error and no choices; it never raises.
    """
    try:
        ratings: list[ContentRatingSummary] = client.list_content_ratings(include_retired=True)
    except EngineClientError as exc:
        return RatingChoices([], exc)
    ordered = sorted(ratings, key=lambda r: (r.display_order, r.name, r.id))
    choices: list[tuple[str, str]] = []
    for rating in ordered:
        if not rating.retired:
            choices.append((rating.id, rating.name))
        elif include_retired or rating.id == current:
            choices.append((rating.id, f"{rating.name}{RETIRED_SUFFIX}"))
    if current and current not in {rating_id for rating_id, _ in choices}:
        choices.append((current, f"{current}{MISSING_SUFFIX}"))
    return RatingChoices(choices)


@dataclass(frozen=True)
class ProfileChoices:
    """An engine-profile select's options, ``(profile id, label)``, or why there is no select:
    the read's ``error``, or ``too_many`` (more than :data:`PROFILES_MAX` profiles). Either way
    the form offers a free-text id input with :attr:`unavailable_text` beside it. ``note``
    carries that text across a signed snapshot (the read is not repeated)."""

    choices: list[tuple[str, str]]
    error: EngineClientError | None = None
    too_many: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and not self.too_many and not self.note

    @property
    def unavailable_text(self) -> str:
        if self.note:
            return self.note
        if self.error is not None:
            return f"Engine profiles unavailable: {error_text(self.error)} — enter the id"
        return TOO_MANY if self.too_many else ""


def engine_profile_choices(client: EngineClient, *, current: str | None = None) -> ProfileChoices:
    """The engine-profile select's options: every non-deleted profile, by name (then id), as
    the Engine lists them; ``current`` (the saved id) is kept as "<name> (deleted)" or "<id>
    (missing)". Reads ``GET /engine-profiles`` (deleted included, so a deleted ``current``
    keeps its name) page by page, stopping after :data:`PROFILES_MAX` profiles (``too_many``).
    Never raises."""
    found: list[EngineProfile] = []
    cursor: str | None = None
    try:
        while True:
            items, cursor = client.list_engine_profiles(
                include_deleted=True, cursor=cursor, limit=ENGINE_PROFILES_PAGE
            )
            found.extend(items)
            if len(found) > PROFILES_MAX:
                return ProfileChoices([], too_many=True)
            if not cursor:
                break
    except EngineClientError as exc:
        return ProfileChoices([], exc)
    choices = [(p.id, p.name) for p in found if p.deleted_at is None]
    if current and current not in {profile_id for profile_id, _ in choices}:
        deleted = next((p for p in found if p.id == current), None)
        shown = f"{deleted.name}{DELETED_SUFFIX}" if deleted else f"{current}{MISSING_SUFFIX}"
        choices.append((current, shown))
    return ProfileChoices(choices)


@dataclass(frozen=True)
class StoryLabel:
    """How a page names one story: ``text`` is its name, "(unnamed) …<last 6>", "held …<last
    6>" (a story held by a pending erasure: 503 ``erasure_pending``) or, when the read failed or
    was not made (over the cap), the id itself with ``error`` saying why."""

    story_id: str
    text: str
    error: str | None = None
    held: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


def held_label(story_id: str) -> str:
    return f"held …{story_id[-6:]}"


def story_labels(
    client: EngineClient, story_ids: Iterable[str], *, cap: int = STORY_LABEL_CAP
) -> dict[str, StoryLabel]:
    """Labels for ``story_ids`` (de-duplicated, in order): one ``GET /stories/{id}`` each for
    the first ``cap``, run on the fan-out pool under its shared deadline; the rest are labelled
    by id with ``error="not looked up"``. Never raises for a failed read."""
    distinct = [story_id for story_id in dict.fromkeys(story_ids) if story_id]
    looked_up, skipped = distinct[:cap], distinct[cap:]
    results = fan_out(lambda story_id: client.get_story(story_id).name, looked_up)
    labels: dict[str, StoryLabel] = {}
    for story_id in looked_up:
        result = results[story_id]
        if isinstance(result, EngineError) and result.code == ERASURE_PENDING:
            labels[story_id] = StoryLabel(story_id, held_label(story_id), held=True)
        elif isinstance(result, EngineClientError):
            labels[story_id] = StoryLabel(story_id, story_id, error_text(result))
        else:
            labels[story_id] = StoryLabel(story_id, label(result, story_id))
    for story_id in skipped:
        labels[story_id] = StoryLabel(story_id, story_id, "not looked up")
    return labels


# --- depicted subjects (#70) ----------------------------------------------------------------------

SUBJECTS_MAX = 500  # each picker source follows cursors up to this many items
SUBJECTS_PAGE = 200  # the Engine's largest page
SUBJECTS_TOO_MANY = "Too many to list — enter the id below"
DRAFT_ONLY = " (draft only)"
CATALOG_CHARACTERS = "catalog characters"
PERSONAS = "personas"


@dataclass(frozen=True)
class SubjectGroup:
    """One source of the subject picker: an optgroup ``label``, its ``(kind:id, label)``
    options, and the ``notice`` shown instead when its read failed or found too many."""

    label: str
    choices: list[tuple[str, str]] = field(default_factory=list)
    notice: str = ""


@dataclass(frozen=True)
class SubjectChoices:
    """The picker's groups: "Catalog characters" then "Personas"."""

    groups: list[SubjectGroup]

    @property
    def choices(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """Django optgroup choices, groups without options left out."""
        return [(g.label, g.choices) for g in self.groups if g.choices]

    @property
    def notices(self) -> list[str]:
        return [g.notice for g in self.groups if g.notice]

    @property
    def values(self) -> list[str]:
        """Every option value, in option order."""
        return [value for g in self.groups for value, _ in g.choices]


def _ordered(items: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """``(id, label, suffix)`` sorted by label (case-insensitive) then id, as ``(id, shown)``: a
    label shared by several gets its last 6 id characters (#43), then the suffix."""
    counts = Counter(name for _, name, _ in items)
    ordered = sorted(items, key=lambda item: (item[1].casefold(), item[0]))
    return [
        (item_id, f"{name} (…{item_id[-6:]}){suffix}" if counts[name] > 1 else f"{name}{suffix}")
        for item_id, name, suffix in ordered
    ]


def _catalog_subjects(client: EngineClient) -> list[tuple[str, str]] | None:
    """Published and draft-only catalog characters (archived ones are not listed), de-duplicated
    by id; ``None`` past :data:`SUBJECTS_MAX`."""
    found: dict[str, tuple[str, str, str]] = {}
    read = 0  # items read, so an answer that repeats itself still stops
    for status in ("published", "draft"):
        cursor: str | None = None
        while True:
            page = client.list_catalog_characters(status=status, cursor=cursor, limit=SUBJECTS_PAGE)
            read += len(page.items)
            for item in page.items:
                suffix = DRAFT_ONLY if status == "draft" else ""
                found.setdefault(item.id, (item.id, label(item.name, item.id), suffix))
            if read > SUBJECTS_MAX:
                return None
            cursor = page.next_cursor
            if not cursor:
                break
    return [(f"catalog_character:{i}", shown) for i, shown in _ordered(list(found.values()))]


def _persona_subjects(client: EngineClient) -> list[tuple[str, str]] | None:
    """Every owner's personas as "name (owner)"; ``None`` past :data:`SUBJECTS_MAX`."""
    found: dict[str, tuple[str, str, str]] = {}
    read = 0
    cursor: str | None = None
    while True:
        items, cursor = client.list_personas(cursor=cursor, limit=SUBJECTS_PAGE)
        read += len(items)
        for p in items:
            found.setdefault(p.id, (p.id, f"{p.name} ({p.owner_principal})", ""))
        if read > SUBJECTS_MAX:
            return None
        if not cursor:
            break
    return [(f"persona:{i}", shown) for i, shown in _ordered(list(found.values()))]


def subject_choices(client: EngineClient) -> SubjectChoices:
    """The depicted-subject picker's options: catalog characters (published and draft-only, a
    draft-only one labelled "<name> (draft only)") and every persona ("<name> (<owner>)"), by
    name then id, values ``catalog_character:<id>`` / ``persona:<id>``. Both sources are read
    at once on the fan-out pool, each following cursors up to :data:`SUBJECTS_MAX` items. A
    source whose read failed, or that holds more, offers no options and a notice instead
    ("Couldn't load personas: 503 …" / "Too many to list — enter the id below"). Never raises."""
    reads = {CATALOG_CHARACTERS: _catalog_subjects, PERSONAS: _persona_subjects}
    results = fan_out(lambda source: reads[source](client), list(reads))
    groups: list[SubjectGroup] = []
    for source, heading in ((CATALOG_CHARACTERS, "Catalog characters"), (PERSONAS, "Personas")):
        result = results[source]
        if isinstance(result, EngineClientError):
            notice = f"Couldn't load {source}: {error_text(result)}"
            groups.append(SubjectGroup(heading, notice=notice))
        elif result is None:
            groups.append(SubjectGroup(heading, notice=f"{heading}: {SUBJECTS_TOO_MANY}"))
        else:
            groups.append(SubjectGroup(heading, result))
    return SubjectChoices(groups)


# --- media lookups (#70; #71) ---------------------------------------------------------------------


@dataclass(frozen=True)
class MediaLookup:
    """One asset's metadata as a page looked it up: ``asset``, or the read's ``error`` text."""

    media_id: str
    asset: MediaAsset | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        """The asset's state (``live``, ``retired``…), or "unavailable (<status> <code>)"."""
        if self.asset is not None:
            return self.asset.state
        return f"unavailable ({self.error})"


def media_lookups(client: EngineClient, media_ids: Iterable[str]) -> dict[str, MediaLookup]:
    """``GET /media/{id}`` for each distinct id, on the fan-out pool under its shared deadline.
    Never raises for a failed read."""
    results = fan_out(client.get_media, [m for m in media_ids if m])
    return {
        media_id: (
            MediaLookup(media_id, error=error_text(result))
            if isinstance(result, EngineClientError)
            else MediaLookup(media_id, result)
        )
        for media_id, result in results.items()
    }


# --- role characters (#73) ------------------------------------------------------------------------

NOT_LISTED_SUFFIX = " (missing)"


@dataclass(frozen=True)
class CharacterChoices:
    """A role's character select (#73): published catalog characters as ``(id, label)``, or why
    there is none (the read's ``error``, or ``too_many``), in which case the form offers a raw
    catalog-id input with :attr:`unavailable_text` beside it. ``note`` carries that text across
    a signed snapshot."""

    choices: list[tuple[str, str]]
    error: EngineClientError | None = None
    too_many: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and not self.too_many and not self.note

    @property
    def unavailable_text(self) -> str:
        if self.note:
            return self.note
        if self.error is not None:
            return f"Catalog characters unavailable: {error_text(self.error)} — enter the id"
        return TOO_MANY if self.too_many else ""


def published_character_choices(
    client: EngineClient, *, current: Iterable[str] = ()
) -> CharacterChoices:
    """Published catalog characters by name (then id; a shared name gets its last 6 id
    characters), following cursors up to :data:`SUBJECTS_MAX`. Each of ``current`` (the saved
    ids) the list lacks — archived, never published or gone — is kept as "<id> (missing)".
    Never raises."""
    found: dict[str, tuple[str, str, str]] = {}
    read = 0
    cursor: str | None = None
    try:
        while True:
            page = client.list_catalog_characters(
                status="published", cursor=cursor, limit=SUBJECTS_PAGE
            )
            read += len(page.items)
            for item in page.items:
                found.setdefault(item.id, (item.id, label(item.name, item.id), ""))
            if read > SUBJECTS_MAX:
                return CharacterChoices([], too_many=True)
            cursor = page.next_cursor
            if not cursor:
                break
    except EngineClientError as exc:
        return CharacterChoices([], exc)
    choices = _ordered(list(found.values()))
    known = set(found)
    choices.extend(
        (value, f"{value}{NOT_LISTED_SUFFIX}")
        for value in dict.fromkeys(current)
        if value and value not in known
    )
    return CharacterChoices(choices)


def kept_ratings(ratings: RatingChoices, keep: Iterable[str]) -> list[tuple[str, str]]:
    """``ratings`` read with ``include_retired=True`` narrowed to a select's options: every
    non-retired rating, plus each of ``keep`` (saved ids) as "<name> (retired)" or, when the
    Engine does not list it, "<id> (missing)" (#73: one ratings read serves the main rating
    select and each role's eligibility)."""
    wanted = {value for value in keep if value}
    options = [
        (value, shown)
        for value, shown in ratings.choices
        if not shown.endswith(RETIRED_SUFFIX) or value in wanted
    ]
    listed = {value for value, _ in options}
    options.extend(
        (v, f"{v}{MISSING_SUFFIX}") for v in dict.fromkeys(keep) if v and v not in listed
    )
    return options
