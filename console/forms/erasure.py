"""The erasure form (#86): who to erase (a story character, a catalog character or a persona) and
where (one story, or the catalog).

One GET-driven page, ``/erasure/new/?kind=&story=&subject=&scope=``. The kind and story selects
re-render the subject section (HTMX, or a "Next" submit without JS); the hidden ``shown_kind``
and ``shown_story`` say what the page was rendered with, so :func:`settle` can tell a changed
kind (subject cleared, scope reset to the kind's default) or a changed story (a story-character
subject cleared) from a pre-filled link.

:class:`ErasureForm` makes every check the Console can before any Engine call: a subject chosen
(trimmed, 1 to 64 characters), a story chosen for the story scope, and a story id that is a ULID. A
story character with the catalog scope is not offered, but is not refused here either: the
Engine's 422 ``invalid_erasure_scope`` shows at the scope.

The option loaders (:func:`story_options`, :func:`character_options`,
:func:`catalog_character_options`, :func:`persona_options`) never raise: a failed read degrades
its select into a free-text id field.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from django import forms

from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError
from console.engine.models import CatalogScope, StoryScope, SubjectKind, SubjectRef
from console.entities import unnamed_label
from console.erasure import SCOPE_CATALOG, SCOPE_LABELS, SCOPE_STORY, SUBJECT_KINDS
from console.ids import is_ulid

LIST_MAX = 500  # "All X" selects stop here (#64 convention 6)
LIST_PAGE = 200  # the Engine's largest page
HELD = "blocked_by_erasure"
CATALOG_STATUSES = ("published", "draft", "archived")
DEFAULT_SCOPE: dict[str, str] = {
    "character": SCOPE_STORY,
    "catalog_character": SCOPE_CATALOG,
    "persona": SCOPE_CATALOG,
}

CHOOSE_SUBJECT = "Choose a subject"
CHOOSE_STORY = "Choose a story"
NOT_A_STORY_ID = "Not a story id (a 26-character ULID)"
SUBJECT_TOO_LONG = "At most 64 characters"
CHOOSE_KIND = "Choose what to erase"
UNKNOWN_SCOPE = "Choose a scope"
TOO_MANY = "Too many to list — enter the id"
NO_CHARACTERS = "This story has no characters"
STORY_FIRST = "Choose a story first"
NOT_LISTED = " (not listed)"


def load_failed(exc: EngineClientError) -> str:
    return f"Couldn't load the list ({error_text(exc)}) — enter the id"


# --- the options ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Options:
    """One select's options as ``(id, label)``; ``notice`` set means a free-text id field instead
    (the read failed, or found more than :data:`LIST_MAX`). ``empty`` is shown in place of an
    option list that is empty for a reason (a story without characters)."""

    choices: list[tuple[str, str]] = field(default_factory=list)
    notice: str = ""
    empty: str = ""

    @property
    def free_text(self) -> bool:
        return bool(self.notice)

    def keeping(self, value: str) -> Options:
        """These options with ``value`` kept as a selected "<id> (not listed)" when it is not
        among them (a pre-filled subject: the Engine decides)."""
        if not value or self.free_text or any(v == value for v, _ in self.choices):
            return self
        return Options([*self.choices, (value, f"{value}{NOT_LISTED}")], self.notice, "")


def _short(entity_id: str) -> str:
    return entity_id[:8]


def story_options(client: EngineClient) -> Options:
    """Every story but those held by an erasure (they'd answer 503), as "<name or '(unnamed)'> ·
    <status> · <id[:8]>"."""
    try:
        stories = client.list_stories()
    except EngineClientError as exc:
        return Options(notice=load_failed(exc))
    shown = [s for s in stories if s.status != HELD]
    if len(shown) > LIST_MAX:
        return Options(notice=TOO_MANY)
    return Options(
        [(s.id, f"{s.name or '(unnamed)'} · {s.status} · {_short(s.id)}") for s in shown]
    )


def character_options(client: EngineClient, story_id: str) -> Options:
    """The story's characters (``GET /stories/{id}``), as "<name> (catalog <id[:8]> | ad-hoc) ·
    <id[:8]>"; "This story has no characters" when it has none."""
    try:
        story = client.get_story(story_id)
    except EngineClientError as exc:
        return Options(notice=load_failed(exc))
    choices = []
    for character in story.cast:
        source = character.source
        origin = f"catalog {_short(source.catalog_id)}" if source is not None else "ad-hoc"
        choices.append((character.id, f"{character.name} ({origin}) · {_short(character.id)}"))
    return Options(choices, empty="" if choices else NO_CHARACTERS)


def catalog_character_options(client: EngineClient) -> Options:
    """Catalog characters of every status: one list per status (published, draft, archived),
    each following its cursors, merged and de-duplicated by id, as "<name> — <owner> · <status>
    · <id[:8]>"."""
    found: dict[str, str] = {}
    read = 0
    try:
        for status in CATALOG_STATUSES:
            cursor: str | None = None
            while True:
                page = client.list_catalog_characters(status=status, cursor=cursor, limit=LIST_PAGE)
                read += len(page.items)
                for item in page.items:
                    name = item.name or unnamed_label(item.id)
                    found.setdefault(
                        item.id,
                        f"{name} — {item.owner_principal} · {item.status} · {_short(item.id)}",
                    )
                if read > LIST_MAX:
                    return Options(notice=TOO_MANY)
                cursor = page.next_cursor
                if not cursor:
                    break
    except EngineClientError as exc:
        return Options(notice=load_failed(exc))
    return Options(sorted(found.items(), key=lambda item: (item[1].casefold(), item[0])))


def persona_options(client: EngineClient) -> Options:
    """Every owner's personas (following the cursor pages), as "<name> — <owner> · <id[:8]>"
    (a persona has no status)."""
    found: dict[str, str] = {}
    read = 0
    cursor: str | None = None
    try:
        while True:
            items, cursor = client.list_personas(cursor=cursor, limit=LIST_PAGE)
            read += len(items)
            for p in items:
                found.setdefault(p.id, f"{p.name} — {p.owner_principal} · {_short(p.id)}")
            if read > LIST_MAX:
                return Options(notice=TOO_MANY)
            if not cursor:
                break
    except EngineClientError as exc:
        return Options(notice=load_failed(exc))
    return Options(sorted(found.items(), key=lambda item: (item[1].casefold(), item[0])))


# --- the state of the page ------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """What the page shows as chosen (strings as posted, possibly blank)."""

    kind: str = ""
    story: str = ""
    subject: str = ""
    scope: str = ""

    def query(self) -> dict[str, str]:
        """The non-blank values as the form's query parameters."""
        values = {"kind": self.kind, "story": self.story, "subject": self.subject}
        values["scope"] = self.scope
        return {k: v for k, v in values.items() if v}


def scopes_for(kind: str) -> list[tuple[str, str]]:
    """The scopes offered for ``kind``: a story character only "This story"."""
    if kind == "character":
        return [(SCOPE_STORY, SCOPE_LABELS[SCOPE_STORY])]
    return [(SCOPE_STORY, SCOPE_LABELS[SCOPE_STORY]), (SCOPE_CATALOG, SCOPE_LABELS[SCOPE_CATALOG])]


def settle(data: Mapping[str, str]) -> Choice:
    """The choice a GET shows: changing the kind clears the subject and resets the scope to the
    kind's default (the story is kept); changing the story clears a story-character subject; a
    scope the kind does not offer falls back to its default."""
    kind = (data.get("kind") or "").strip()
    if kind not in SUBJECT_KINDS:
        kind = ""
    story = (data.get("story") or "").strip()
    subject = (data.get("subject") or "").strip()
    scope = (data.get("scope") or "").strip()
    shown_kind = data.get("shown_kind")
    shown_story = data.get("shown_story")
    if shown_kind is not None and shown_kind != kind:
        subject = ""
        scope = ""
    elif shown_story is not None and shown_story != story and kind == "character":
        subject = ""
    if kind and scope not in dict(scopes_for(kind)):
        scope = DEFAULT_SCOPE[kind]
    if not kind:
        scope = ""
    return Choice(kind, story, subject, scope)


# --- the checks before any call -------------------------------------------------------------------


class ErasureForm(forms.Form):
    """The Console's checks of a preview request. Plain fields: the options are the page's, the
    Engine decides on ids it does not list."""

    kind = forms.ChoiceField(
        choices=list(SUBJECT_KINDS.items()), error_messages={"required": CHOOSE_KIND}
    )
    story = forms.CharField(required=False, strip=True)
    subject = forms.CharField(
        required=True,
        strip=True,
        max_length=64,
        error_messages={"required": CHOOSE_SUBJECT, "max_length": SUBJECT_TOO_LONG},
    )
    scope = forms.ChoiceField(
        choices=[(SCOPE_STORY, "This story"), (SCOPE_CATALOG, "The catalog")],
        error_messages={"required": UNKNOWN_SCOPE, "invalid_choice": UNKNOWN_SCOPE},
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        story = cleaned.get("story") or ""
        if cleaned.get("scope") == SCOPE_STORY:
            if not story:
                self.add_error("story", forms.ValidationError(CHOOSE_STORY, "required"))
            elif not is_ulid(story):
                self.add_error("story", forms.ValidationError(NOT_A_STORY_ID, "invalid"))
        return cleaned

    def subject_ref(self) -> SubjectRef:
        kind = cast(SubjectKind, self.cleaned_data["kind"])
        return SubjectRef(kind, self.cleaned_data["subject"])

    def scope_ref(self) -> StoryScope | CatalogScope:
        if self.cleaned_data["scope"] == SCOPE_STORY:
            return StoryScope(self.cleaned_data["story"])
        return CatalogScope()

    def choice(self) -> Choice:
        """The posted values (raw, so an invalid form re-renders what was sent)."""
        return Choice(
            str(self.data.get("kind", "")),
            str(self.data.get("story", "")).strip(),
            str(self.data.get("subject", "")).strip(),
            str(self.data.get("scope", "")),
        )


def choice_of(subject: SubjectRef, scope: StoryScope | CatalogScope) -> Choice:
    """The form's values for a request sent before ("Preview again", the form after an error)."""
    if isinstance(scope, StoryScope):
        return Choice(subject.kind, scope.story, subject.id, SCOPE_STORY)
    return Choice(subject.kind, "", subject.id, SCOPE_CATALOG)
