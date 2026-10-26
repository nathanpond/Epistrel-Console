"""The author canon forms (#146): commit a fact, and supersede a current version.

:class:`FactForm` builds the exact ``FactRequest`` of ``POST /admin/stories/{id}/facts``;
:class:`SupersedeForm` the ``SupersedeRequest`` of ``POST …/facts/{fid}/supersede``. Both
validate everything they can before any call (a refused form makes no fact write):

- **Ids** (subject, object, set members, anchored to, characters, ``id``/``ids`` qualifiers)
  are trimmed, upper-cased and checked against the Engine's id pattern.
- **The object** is one of the forms the predicate allows (:func:`object_forms`): ``none``
  for an ``object_type: none`` predicate; a literal for a literal kind (the kind select
  pre-set, another kind refused); an entity id, or a set when ``set_object``, for an entity
  kind; any of them for ``any``. Non-blank inputs of another form than the chosen radio are an
  error. Without the registry no gating applies (the Engine's 422 lands on the field).
- **Literals** coerce by kind (:func:`literal_value`); a unit belongs to a ``quantity`` alone.
- **Qualifiers** are key / type / value rows. A predicate whose ``qualifiers`` is a map gets
  exactly its declared rows (key and type fixed; a blank value is not sent); one whose
  ``qualifiers`` is ``null`` (an auto predicate's), and a form without the registry, get free
  rows with no-JS add/remove (``console.formset_rows``). Values are checked as the Engine's
  ``Predicate.qualifier_errors`` / ``_qualifier_ok`` do, with its wording.
- **Valid-time** is always sent as ``proposition.valid_time``: an ``Interval`` whose bounds are
  ``{inner, outer}`` (inner = outer when the inner field is blank), both ends ``null`` when all
  four fields are blank. Top-level ``valid_from``/``valid_to`` are never sent.
- **Visibility**: a scope, and the characters (sorted, de-duplicated) only under the
  ``characters`` scope, which needs at least one.

The "Advanced: edit as JSON" box (#64's :class:`JsonBoxField`) holds the whole body: when it is
non-empty it is sent as is and every structured field is ignored (not even validated).

A supersede's subject, predicate, key qualifiers and ``event_id`` come from a #64 two-stage
signed snapshot of the version (:func:`sign_snapshot` / :func:`read_snapshot`, kind
``canon_supersede``), never from an editable input; its visibility and lock selects start on
"(carry over: …)", which leaves the field out of the request.
"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from typing import cast as cast_type

from django import forms
from django.forms.utils import ErrorDict
from django.http import QueryDict

from console import twostage
from console.canon import PLAYER_SUFFIX
from console.cast import Character, character_labels
from console.engine.client import EngineError
from console.engine.models import FactBody, Instant, Predicate
from console.engine_form_errors import BODY_PREFIX, engine_field_errors
from console.forms.fields import JsonBoxField
from console.formset_rows import rows as posted_rows
from console.ids import is_ulid

LITERAL_KINDS = ("int", "float", "str", "bool", "date", "duration", "quantity")
QUALIFIER_KINDS = ("str", "int", "float", "bool", "date", "id", "ids")
SCOPES = ("objective", "public", "engine", "characters")
CHARACTERS = "characters"
POLARITIES = ("affirmed", "negated")
ENTITY, LITERAL, SET, NONE = "entity", "literal", "set", "none"
OBJECT_LABELS = {ENTITY: "Entity id", LITERAL: "Literal", SET: "Set of entity ids", NONE: "None"}
ANY = "any"
ROWS_PREFIX = "q"
ROWS_MAX = 20

BAD_ID = "Enter a 26-character id (digits and capital letters, no I, L, O or U)"
ENTER_A_VALUE = "Enter a value"
WHOLE_MINUTES = "Enter a whole number of minutes"
OUTER_TOO = "Give the outer bound too"
PICK_A_CHARACTER = "Pick at least one character"
KEY_ONCE = "Each qualifier key once"
GIVE_A_KEY = "Give this value a key"
UNIT_ONLY_QUANTITY = "Only a quantity has a unit"
QUANTITY_UNIT = "A quantity needs a unit"
EMPTY_SET = "A set needs at least one entity id"
CHOOSE_OBJECT = "Choose the object's form"
KEY_FIXED = "Key qualifiers can't change on a supersede"
NO_QUALIFIERS = "This predicate declares no qualifiers"
UNKNOWN_KIND = "Choose a qualifier type"
NOT_A_KIND = "Choose a literal kind"
UNKNOWN_PREDICATE = "unknown_predicate"
LITERAL_INVALID = {
    "int": "Enter a whole number",
    "duration": "Enter a whole number of minutes",
    "float": "Enter a number",
    "quantity": "Enter a number",
    "bool": "Enter true or false",
    "date": "Enter a date (YYYY-MM-DD)",
    "str": ENTER_A_VALUE,
}
SNAPSHOT_KIND = "canon_supersede"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_EXPIRED = "This form expired or was changed — reopen Supersede from the Canon tab"
INSTANT_NOTE = (
    "This version's valid-time was an instant (granularity {granularity}); the successor is "
    "sent as the interval shown"
)

_INTEGER = re.compile(r"^[+-]?\d+$")
_TRUE, _FALSE = "true", "false"


def only_text(form: str) -> str:
    return f"Only the {form} object is sent — clear the other object fields"


def takes_no_object(name: str) -> str:
    return f"{name} takes no object"


def takes_literal(name: str, kind: str) -> str:
    return f"{name} takes a {kind} literal"


def not_a_qualifier(key: str, name: str) -> str:
    """The Engine's ``qualifier_errors`` wording."""
    return f"{key!r} is not a qualifier of {name}"


def wrong_kind(key: str, kind: str) -> str:
    return f"{key!r} must be {kind}"


# --- coercion ------------------------------------------------------------------------------------


def entity_id(typed: str) -> str | None:
    """A typed id trimmed and upper-cased, or ``None`` when it fails the Engine's pattern."""
    value = typed.strip().upper()
    return value if is_ulid(value) else None


def _integer(typed: str) -> int | None:
    return int(typed) if _INTEGER.match(typed) else None


def _finite(typed: str) -> float | None:
    try:
        value = float(typed)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _boolean(typed: str) -> bool | None:
    lowered = typed.lower()
    return True if lowered == _TRUE else False if lowered == _FALSE else None


def _date(typed: str) -> str | None:
    try:
        return date.fromisoformat(typed).isoformat()
    except ValueError:
        return None


def literal_value(kind: str, typed: str) -> int | float | str | bool | None:
    """A literal's value by kind (``None`` when it doesn't coerce): ``int``/``duration`` an
    integer (sign allowed), ``float`` a finite float (``3`` → ``3.0``), ``bool``
    ``true``/``false`` (any case), ``date`` an ISO date (normalised), ``str`` as typed,
    ``quantity`` an integer when it parses as one, else a finite float."""
    stripped = typed.strip()
    if kind == "str":
        return typed
    if kind in ("int", "duration"):
        return _integer(stripped)
    if kind == "float":
        return _finite(stripped)
    if kind == "bool":
        return _boolean(stripped)
    if kind == "date":
        return _date(stripped)
    if kind == "quantity":
        whole = _integer(stripped)
        return whole if whole is not None else _finite(stripped)
    return None


def qualifier_value(kind: str, typed: str) -> Any:
    """A qualifier value by :data:`QUALIFIER_KINDS` (``None`` when it doesn't coerce), as the
    Engine's ``_qualifier_ok`` accepts it: ``id`` one id, ``ids`` a comma-separated non-empty
    list of ids, ``float`` a JSON float, ``date`` an ISO date string."""
    stripped = typed.strip()
    if kind == "str":
        return typed
    if kind == "int":
        return _integer(stripped)
    if kind == "float":
        return _finite(stripped)
    if kind == "bool":
        return _boolean(stripped)
    if kind == "date":
        return _date(stripped)
    if kind == "id":
        return entity_id(stripped)
    if kind == "ids":
        members = [m.strip() for m in stripped.split(",") if m.strip()]
        ids = [entity_id(m) for m in members]
        if not ids or any(i is None for i in ids):
            return None
        return ids
    return None


def typed_text(value: object) -> str:
    """A stored literal or qualifier value as the form's text (lists comma-joined)."""
    if isinstance(value, bool):
        return _TRUE if value else _FALSE
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def inferred_kind(value: object) -> str:
    """The :data:`QUALIFIER_KINDS` value a stored qualifier of an undeclared predicate shows
    as (its JSON type; an id-shaped string ``id``)."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "ids"
    if isinstance(value, str) and is_ulid(value):
        return "id"
    return "str"


def object_forms(predicate: Predicate | None) -> tuple[str, ...]:
    """The object forms ``predicate`` allows (the radio's options; every form without the
    registry). An entity kind is any ``object_type`` that is neither a literal kind, ``any``
    nor ``none``."""
    if predicate is None:
        return (ENTITY, LITERAL, SET, NONE)
    kind = predicate.object_type
    if kind == NONE:
        return (NONE,)
    if kind in LITERAL_KINDS:
        return (LITERAL,)
    entity = (ENTITY, SET) if predicate.set_object else (ENTITY,)
    return (*entity[:1], LITERAL, *entity[1:]) if kind == ANY else entity


# --- qualifier rows ------------------------------------------------------------------------------


@dataclass(frozen=True)
class QualifierRow:
    """One rendered qualifier row (``q-<index>-key|type|value``). ``fixed``: the key and type
    come from the predicate's declaration (shown, posted hidden)."""

    index: int
    key: str
    kind: str
    value: str
    fixed: bool


@dataclass(frozen=True)
class FixedQualifier:
    """A supersede's read-only qualifier (a key qualifier, or any qualifier without the
    registry): shown, never posted; it comes from the snapshot."""

    key: str
    text: str


@dataclass
class RowSet:
    """The form's qualifier rows. ``mode``: ``declared`` (the predicate's map: exactly its
    rows), ``free`` (add/remove, a free type) or ``off`` (no rows: a supersede without the
    registry)."""

    mode: str
    rows: list[QualifierRow] = field(default_factory=list)
    declared_none: bool = False  # a declared map that is empty ("declares no qualifiers")

    @property
    def free(self) -> bool:
        return self.mode == "free"


def _declared_rows(
    declared: Mapping[str, str], values: Mapping[str, str], skip: Sequence[str]
) -> list[QualifierRow]:
    names = [k for k in declared if k not in skip]
    return [QualifierRow(i, k, declared[k], values.get(k, ""), True) for i, k in enumerate(names)]


def _posted(data: QueryDict) -> list[tuple[str, str, str]]:
    found = []
    for row in posted_rows(data, ROWS_PREFIX, ROWS_MAX):
        first = {name: (values[0] if values else "") for name, values in row.items()}
        found.append((first.get("key", ""), first.get("type", ""), first.get("value", "")))
    return found


# --- the snapshot --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """What a supersede page signed (#64's two-stage token, kind ``canon_supersede``): the
    version's identity and fixed fields (``body``), and what the page shows of it (``shown``:
    its visibility, lock and ``anchored_to``, the Instant note, and the pre-fill request
    body)."""

    story_id: str
    fact_id: str
    subject_id: str
    predicate_id: str
    key_qualifiers: dict[str, Any]
    event_id: str | None
    scope: str
    characters: list[str]
    locked: bool
    anchored_to: str | None
    instant_note: str | None
    prefill_body: dict[str, Any] | None
    rows_allowed: bool


def key_qualifier_names(fact: FactBody, predicate: Predicate | None) -> list[str]:
    """The version's read-only qualifiers: the registry's ``key_qualifiers`` it carries, or
    every qualifier it carries without the registry."""
    carried = fact.proposition.qualifiers
    if predicate is None:
        return sorted(carried)
    return [k for k in predicate.key_qualifiers if k in carried]


def snapshot_of(
    story_id: str, fact: FactBody, predicate: Predicate | None, prefill_body: dict[str, Any] | None
) -> Snapshot:
    proposition = fact.proposition
    keys = key_qualifier_names(fact, predicate)
    note = None
    if isinstance(proposition.valid_time, Instant):
        note = INSTANT_NOTE.format(granularity=proposition.valid_time.granularity)
    return Snapshot(
        story_id=story_id,
        fact_id=fact.id,
        subject_id=proposition.subject_id,
        predicate_id=proposition.predicate_id,
        key_qualifiers={k: proposition.qualifiers[k] for k in keys},
        event_id=proposition.event_id,
        scope=fact.visibility.scope,
        characters=list(fact.visibility.characters or []),
        locked=fact.author_locked,
        anchored_to=fact.anchored_to,
        instant_note=note,
        prefill_body=prefill_body,
        rows_allowed=predicate is not None,
    )


def sign_snapshot(snapshot: Snapshot) -> str:
    body = {
        "story_id": snapshot.story_id,
        "fact_id": snapshot.fact_id,
        "subject_id": snapshot.subject_id,
        "predicate_id": snapshot.predicate_id,
        "key_qualifiers": snapshot.key_qualifiers,
        "event_id": snapshot.event_id,
    }
    shown = {
        "scope": snapshot.scope,
        "characters": snapshot.characters,
        "locked": snapshot.locked,
        "anchored_to": snapshot.anchored_to,
        "instant_note": snapshot.instant_note,
        "prefill_body": snapshot.prefill_body,
        "rows_allowed": snapshot.rows_allowed,
    }
    return twostage.sign(SNAPSHOT_KIND, snapshot.fact_id, body, base=shown)


def read_snapshot(token: str, story_id: str, fact_id: str) -> Snapshot:
    """The snapshot ``token`` carries for this story's ``fact_id``, or
    :class:`console.twostage.ConfirmationExpired` (tampered, expired, another version's)."""
    payload = twostage.unsign(SNAPSHOT_KIND, token, max_age=twostage.MAX_AGE_S)
    body, shown = payload.body, payload.base
    try:
        if not (isinstance(body, dict) and isinstance(shown, dict)):
            raise TypeError
        snapshot = Snapshot(
            story_id=str(body["story_id"]),
            fact_id=str(body["fact_id"]),
            subject_id=str(body["subject_id"]),
            predicate_id=str(body["predicate_id"]),
            key_qualifiers=dict(body["key_qualifiers"]),
            event_id=body["event_id"],
            scope=str(shown["scope"]),
            characters=[str(c) for c in shown["characters"]],
            locked=bool(shown["locked"]),
            anchored_to=shown["anchored_to"],
            instant_note=shown["instant_note"],
            prefill_body=shown["prefill_body"],
            rows_allowed=bool(shown["rows_allowed"]),
        )
    except KeyError, TypeError, ValueError:
        raise twostage.ConfirmationExpired(SNAPSHOT_EXPIRED) from None
    if (snapshot.story_id, snapshot.fact_id, payload.target_id) != (story_id, fact_id, fact_id):
        raise twostage.ConfirmationExpired(SNAPSHOT_EXPIRED)
    return snapshot


# --- the 422 placement ---------------------------------------------------------------------------

# Engine paths (after ``body.``) → the form field they land on; a prefix matches its sub-paths.
FIELD_PATHS: tuple[tuple[str, str], ...] = (
    ("predicate_id", "predicate"),
    ("proposition.predicate_id", "predicate"),
    ("proposition.subject_id", "subject"),
    ("object", "object_id"),
    ("proposition.object_id", "object_id"),
    ("proposition.literal", "literal_value"),
    ("object_set", "object_set"),
    ("proposition.object_set", "object_set"),
    ("qualifiers", "qualifiers"),
    ("proposition.qualifiers", "qualifiers"),
    ("proposition.valid_time", "valid_time"),
    ("visibility.characters", "characters"),
    ("visibility", "scope"),
    ("anchored_to", "anchored_to"),
)


def field_for(path: str) -> str | None:
    """The form field an Engine error path names, or ``None``."""
    stripped = path.removeprefix(BODY_PREFIX)
    for prefix, name in FIELD_PATHS:
        if stripped == prefix or stripped.startswith((f"{prefix}.", f"{prefix}[")):
            return name
    return None


# --- the forms -----------------------------------------------------------------------------------


def _minutes(label: str) -> forms.CharField:
    return forms.CharField(
        required=False,
        label=label,
        widget=forms.TextInput(attrs={"size": 8, "inputmode": "numeric"}),
    )


class FactForm(forms.Form):
    """An author fact (see the module docstring). ``predicate`` is the predicate that gates the
    form (``None``: no gating, the registry read failed); ``cast`` the story's cast for the
    characters multi-select (``None``: a one-per-line id field). A valid form's request body is
    :attr:`body`."""

    subject = forms.CharField(
        required=False,
        label="Subject id",
        widget=forms.TextInput(attrs={"size": 30, "list": "fact-cast"}),
    )
    predicate = forms.CharField(
        required=False, label="Predicate id", widget=forms.TextInput(attrs={"size": 30})
    )
    object_form = forms.ChoiceField(required=False, label="Object", widget=forms.RadioSelect)
    object_id = forms.CharField(
        required=False,
        label="Entity id",
        widget=forms.TextInput(attrs={"size": 30, "list": "fact-cast"}),
    )
    literal_kind = forms.ChoiceField(
        required=False, label="Kind", choices=[(k, k) for k in LITERAL_KINDS]
    )
    literal_value = forms.CharField(required=False, label="Value", strip=False)
    literal_unit = forms.CharField(
        required=False, label="Unit", widget=forms.TextInput(attrs={"size": 10})
    )
    object_set = forms.CharField(
        required=False,
        label="Entity ids (one per line)",
        widget=forms.Textarea(attrs={"rows": 3, "cols": 30}),
    )
    polarity = forms.ChoiceField(
        label="Polarity",
        choices=[(p, p) for p in POLARITIES],
        initial="affirmed",
        widget=forms.RadioSelect,
    )
    qualifiers = forms.CharField(required=False, widget=forms.HiddenInput)  # errors slot
    valid_from = _minutes("Valid from")
    valid_from_inner = _minutes("Valid from (inner)")
    valid_to = _minutes("Valid to")
    valid_to_inner = _minutes("Valid to (inner)")
    valid_time = forms.CharField(required=False, widget=forms.HiddenInput)  # errors slot
    scope = forms.ChoiceField(
        label="Visibility", choices=[(s, s) for s in SCOPES], initial="objective"
    )
    characters = forms.MultipleChoiceField(required=False, label="Characters")
    anchored_to = forms.CharField(
        required=False, label="Anchored to (fact id)", widget=forms.TextInput(attrs={"size": 30})
    )
    locked = forms.BooleanField(required=False, label="Locked")
    json_body = JsonBoxField(dict, {}, required=False, label="Request body (JSON)")

    def __init__(
        self,
        data: QueryDict | None = None,
        *,
        predicate: Predicate | None,
        cast: Sequence[Character] | None,
        initial: Mapping[str, Any] | None = None,
        rows: Sequence[QualifierRow] | None = None,
    ) -> None:
        start = dict(initial or {})
        if cast is None and isinstance(start.get("characters"), list):
            start["characters"] = "\n".join(start["characters"])
        super().__init__(data, initial=start)
        self.gate = predicate
        self.cast = cast
        self.body: dict[str, Any] = {}
        self.forms_offered = object_forms(predicate)
        offered = cast_type(forms.ChoiceField, self.fields["object_form"])
        offered.choices = [(f, OBJECT_LABELS[f]) for f in self.forms_offered]
        if len(self.forms_offered) == 1:
            offered.initial = self.forms_offered[0]
        if predicate is not None:
            self.fields["predicate"].widget = forms.HiddenInput()
            self.fields["predicate"].initial = predicate.id
            if predicate.object_type in LITERAL_KINDS:
                self.fields["literal_kind"].initial = predicate.object_type
        if cast is None:
            self.fields["characters"] = forms.CharField(
                required=False,
                label="Character ids (one per line)",
                widget=forms.Textarea(attrs={"rows": 3, "cols": 30}),
            )
        else:
            labels = character_labels(cast)
            choices = [
                (c.id, f"{labels[c.id]}{PLAYER_SUFFIX}" if c.is_player else labels[c.id])
                for c in cast
            ]
            characters = cast_type(forms.MultipleChoiceField, self.fields["characters"])
            characters.choices = choices
            characters.widget.attrs["size"] = min(max(len(choices), 2), 8)
        self.row_set = self._rows(rows)

    @property
    def posted(self) -> QueryDict:
        """The bound data as a ``QueryDict`` (empty when unbound)."""
        return self.data if isinstance(self.data, QueryDict) else QueryDict()

    # --- rows ---

    @property
    def predicate_name(self) -> str:
        """The predicate's name for messages (its id without the registry)."""
        if self.gate is not None:
            return self.gate.name
        return str(self.data.get("predicate") or "this predicate").strip().upper()

    def _skip(self) -> Sequence[str]:
        return ()

    def _rows_mode(self) -> str:
        if self.gate is None or self.gate.qualifiers is None:
            return "free"
        return "declared"

    def _rows(self, initial_rows: Sequence[QualifierRow] | None) -> RowSet:
        mode = self._rows_mode()
        if mode == "off":
            return RowSet("off")
        if mode == "declared":
            declared = (self.gate.qualifiers if self.gate is not None else None) or {}
            if self.is_bound:
                values = {k: v for k, _, v in _posted(self.posted) if k}
            else:
                values = {r.key: r.value for r in initial_rows or ()}
            built = _declared_rows(declared, values, self._skip())
            return RowSet(mode, built, declared_none=not built)
        if self.is_bound:
            free = [
                QualifierRow(i, k, t or "str", v, False)
                for i, (k, t, v) in enumerate(_posted(self.posted))
            ]
        else:
            free = [
                QualifierRow(i, r.key, r.kind, r.value, False)
                for i, r in enumerate(initial_rows or ())
            ]
        if not free:
            free = [QualifierRow(0, "", "str", "", False)]
        return RowSet(mode, free)

    @property
    def qualifier_kinds(self) -> tuple[str, ...]:
        return QUALIFIER_KINDS

    @property
    def total_rows(self) -> int:
        return len(self.row_set.rows)

    @property
    def json_mode(self) -> bool:
        return self.is_bound and bool(str(self.data.get("json_body") or "").strip())

    def suppress_errors(self) -> None:
        """Render a bound form without validating it (a row add/remove re-render)."""
        self._errors = ErrorDict()
        self.cleaned_data = {}

    # --- cleaning ---

    def full_clean(self) -> None:
        if not self.json_mode:
            super().full_clean()
            return
        self._errors = ErrorDict()
        self.cleaned_data = {}
        try:
            self.body = self.fields["json_body"].clean(self.data.get("json_body"))
        except forms.ValidationError as exc:
            self.add_error("json_body", exc)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        proposition = self._proposition(data)
        visibility = self._visibility(data)
        if self.errors:
            return data
        self.body = self._request(data, proposition, visibility)
        return data

    def _request(
        self, data: Mapping[str, Any], proposition: dict[str, Any], visibility: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "proposition": proposition,
            "visibility": visibility,
            "locked": bool(data.get("locked")),
        }
        anchored = str(self.data.get("anchored_to") or "").strip()
        if anchored:
            body["anchored_to"] = anchored.upper()
        return body

    def _text(self, name: str) -> str:
        return str(self.data.get(name) or "")

    def _identity(self) -> dict[str, Any]:
        """The proposition's subject and predicate (the commit form's inputs)."""
        out: dict[str, Any] = {}
        subject = entity_id(self._text("subject"))
        if subject is None:
            self.add_error("subject", BAD_ID)
        else:
            out["subject_id"] = subject
        if self.gate is not None:
            out["predicate_id"] = self.gate.id
        else:
            predicate = entity_id(self._text("predicate"))
            if predicate is None:
                self.add_error("predicate", BAD_ID)
            else:
                out["predicate_id"] = predicate
        anchored = self._text("anchored_to").strip()
        if anchored and entity_id(anchored) is None:
            self.add_error("anchored_to", BAD_ID)
        return out

    def _proposition(self, data: Mapping[str, Any]) -> dict[str, Any]:
        proposition = self._identity()
        proposition.update(self._object())
        proposition["polarity"] = data.get("polarity") or "affirmed"
        qualifiers = self._qualifiers()
        if qualifiers:
            proposition["qualifiers"] = qualifiers
        proposition["valid_time"] = self._valid_time()
        proposition.update(self._extra())
        return proposition

    def _extra(self) -> dict[str, Any]:
        return {}

    def _object(self) -> dict[str, Any]:
        """The object part (see :func:`object_forms`)."""
        typed = {
            ENTITY: self._text("object_id").strip(),
            LITERAL: self._text("literal_value").strip() or self._text("literal_unit").strip(),
            SET: self._text("object_set").strip(),
        }
        chosen = self._text("object_form")
        if chosen not in self.forms_offered:
            chosen = self.forms_offered[0] if len(self.forms_offered) == 1 else ""
        fields = {ENTITY: "object_id", LITERAL: "literal_value", SET: "object_set"}
        if self.forms_offered == (NONE,):
            if any(typed.values()):
                self.add_error("object_form", takes_no_object(self.predicate_name))
            return {}
        if not chosen:
            self.add_error("object_form", CHOOSE_OBJECT)
            return {}
        for form, text in typed.items():
            if form != chosen and text:
                # An input the radio doesn't offer isn't shown: its error goes on the radio.
                shown = form in self.forms_offered
                self.add_error(fields[form] if shown else "object_form", only_text(chosen))
        if chosen == ENTITY:
            found = entity_id(typed[ENTITY])
            if found is None:
                self.add_error("object_id", BAD_ID)
                return {}
            return {"object_id": found}
        if chosen == LITERAL:
            literal = self._literal()
            return {"literal": literal} if literal is not None else {}
        if chosen == SET:
            members = [m.strip() for m in self._text("object_set").splitlines() if m.strip()]
            if not members:
                self.add_error("object_set", EMPTY_SET)
                return {}
            ids = [entity_id(m) for m in members]
            if any(i is None for i in ids):
                self.add_error("object_set", BAD_ID)
                return {}
            return {"object_set": sorted({i for i in ids if i is not None})}
        return {}

    def _literal(self) -> dict[str, Any] | None:
        kind = self._text("literal_kind")
        if kind not in LITERAL_KINDS:
            self.add_error("literal_kind", NOT_A_KIND)
            return None
        predicate = self.gate
        if (
            predicate is not None
            and predicate.object_type in LITERAL_KINDS
            and kind != predicate.object_type
        ):
            self.add_error("literal_kind", takes_literal(predicate.name, predicate.object_type))
            return None
        unit = self._text("literal_unit").strip()
        typed = self._text("literal_value")
        ok = True
        if kind == "quantity" and not unit:
            self.add_error("literal_unit", QUANTITY_UNIT)
            ok = False
        if kind != "quantity" and unit:
            self.add_error("literal_unit", UNIT_ONLY_QUANTITY)
            ok = False
        if not typed.strip():
            self.add_error("literal_value", ENTER_A_VALUE)
            return None
        value = literal_value(kind, typed)
        if value is None:
            self.add_error("literal_value", LITERAL_INVALID[kind])
            return None
        if not ok:
            return None
        literal: dict[str, Any] = {"kind": kind, "value": value}
        if kind == "quantity":
            literal["unit"] = unit
        return literal

    def _row_key_error(self, key: str) -> str | None:
        return None

    def _qualifiers(self) -> dict[str, Any]:
        """The rows as the proposition's qualifiers (the Engine's checks, its wording)."""
        rows = self.row_set
        posted = _posted(self.posted) if rows.mode != "off" else []
        declared = None if self.gate is None else self.gate.qualifiers
        out: dict[str, Any] = {}
        seen: set[str] = set()
        for key, kind, typed in posted:
            key = key.strip()
            if not key and not typed.strip():
                continue
            if not key:
                self.add_error("qualifiers", GIVE_A_KEY)
                continue
            if key in seen:
                self.add_error("qualifiers", KEY_ONCE)
                continue
            seen.add(key)
            refused = self._row_key_error(key)
            if refused is not None:
                self.add_error("qualifiers", refused)
                continue
            if declared is not None:
                if key not in declared:
                    self.add_error("qualifiers", not_a_qualifier(key, self.predicate_name))
                    continue
                kind = declared[key]
            elif kind not in QUALIFIER_KINDS:
                self.add_error("qualifiers", UNKNOWN_KIND)
                continue
            if not typed.strip():
                continue  # a blank value is not sent
            value = qualifier_value(kind, typed)
            if value is None:
                self.add_error("qualifiers", wrong_kind(key, kind))
                continue
            out[key] = value
        return out

    def _bound(self, outer_name: str, inner_name: str) -> dict[str, int] | None:
        outer_text = self._text(outer_name).strip()
        inner_text = self._text(inner_name).strip()
        outer = _integer(outer_text) if outer_text else None
        inner = _integer(inner_text) if inner_text else None
        if outer_text and outer is None:
            self.add_error(outer_name, WHOLE_MINUTES)
        if inner_text and inner is None:
            self.add_error(inner_name, WHOLE_MINUTES)
        if inner_text and not outer_text:
            self.add_error(inner_name, OUTER_TOO)
        if outer is None:
            return None
        return {"inner": outer if inner is None else inner, "outer": outer}

    def _valid_time(self) -> dict[str, Any]:
        return {
            "kind": "interval",
            "start": self._bound("valid_from", "valid_from_inner"),
            "end": self._bound("valid_to", "valid_to_inner"),
        }

    def _characters(self, data: Mapping[str, Any]) -> list[str] | None:
        if self.cast is not None:
            picked = data.get("characters") or []
            return sorted(set(picked)) if isinstance(picked, list) else []
        lines = "\n".join(str(v) for v in self.posted.getlist("characters"))
        typed = [c.strip() for c in lines.splitlines() if c.strip()]
        ids = [entity_id(c) for c in typed]
        if any(i is None for i in ids):
            self.add_error("characters", BAD_ID)
            return None
        return sorted({i for i in ids if i is not None})

    def _scope_visibility(self, scope: str, data: Mapping[str, Any]) -> dict[str, Any]:
        if scope != CHARACTERS:
            return {"scope": scope}  # selected characters are dropped silently
        characters = self._characters(data)
        if characters is None:
            return {"scope": scope}
        if not characters:
            self.add_error("characters", PICK_A_CHARACTER)
        return {"scope": scope, "characters": characters}

    def _visibility(self, data: Mapping[str, Any]) -> dict[str, Any]:
        return self._scope_visibility(str(data.get("scope") or "objective"), data)

    # --- the Engine's 422 ---

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422 (``unknown_predicate``, ``validation_error``): each ``details.errors`` entry
        (else ``details.field``) on the field it names (:func:`field_for`), unmapped entries
        above the form; in JSON mode every entry above the JSON box."""
        failures = engine_field_errors(exc)
        if self.json_mode:
            if not failures:
                self.add_error("json_body", exc.message)
            for failure in failures:
                self.add_error("json_body", f"{failure.path}: {failure.message or exc.message}")
            return
        if exc.code == UNKNOWN_PREDICATE and not failures:
            target = "predicate" if self._shows("predicate") else None
            self.add_error(target, exc.message)
            return
        if not failures:
            self.add_error(None, exc.message)
            return
        for failure in failures:
            name = field_for(failure.path)
            message = failure.message or exc.message
            if name is not None and self._shows(name):
                self.add_error(name, message)
            else:
                self.add_error(None, f"{failure.path}: {message}")

    def _shows(self, name: str) -> bool:
        return name in self.fields

    @property
    def body_text(self) -> str:
        """The body as pretty JSON (the supersede page's read-only ``<pre>``)."""
        return json.dumps(self.body, indent=2, ensure_ascii=False) if self.body else ""


# --- supersede -----------------------------------------------------------------------------------

CARRY = ""


def carried_visibility(snapshot: Snapshot, names: Mapping[str, str]) -> str:
    if snapshot.scope != CHARACTERS:
        return snapshot.scope
    listed = ", ".join(names.get(c, c) for c in snapshot.characters) or "none listed"
    return f"{snapshot.scope}: {listed}"


class SupersedeForm(FactForm):
    """A supersede of the version ``snapshot`` names: no subject, predicate or ``anchored_to``
    inputs (the snapshot's, and ``anchored_to`` carries over), the key qualifiers read-only, and
    visibility and lock selects that start on carry-over (left out of the request)."""

    def __init__(
        self,
        data: QueryDict | None = None,
        *,
        snapshot: Snapshot,
        predicate: Predicate | None,
        cast: Sequence[Character] | None,
        cast_names: Mapping[str, str],
        initial: Mapping[str, Any] | None = None,
        rows: Sequence[QualifierRow] | None = None,
    ) -> None:
        self.snapshot = snapshot
        super().__init__(data, predicate=predicate, cast=cast, initial=initial, rows=rows)
        for name in ("subject", "predicate", "anchored_to"):
            del self.fields[name]
        scope = cast_type(forms.ChoiceField, self.fields["scope"])
        carried = carried_visibility(snapshot, cast_names)
        scope.choices = [(CARRY, f"(carry over: {carried})"), *((s, s) for s in SCOPES)]
        scope.required = False
        scope.initial = CARRY
        lock_text = "locked" if snapshot.locked else "not locked"
        self.fields["locked"] = forms.ChoiceField(
            required=False,
            label="Lock",
            choices=[
                (CARRY, f"(carry over: {lock_text})"),
                (_TRUE, "locked"),
                (_FALSE, "unlocked"),
            ],
            initial=CARRY,
        )
        self.fixed = [
            FixedQualifier(k, typed_text(v)) for k, v in sorted(snapshot.key_qualifiers.items())
        ]

    @property
    def predicate_name(self) -> str:
        if self.gate is not None:
            return self.gate.name
        return self.snapshot.predicate_id

    def _rows_mode(self) -> str:
        if not getattr(self, "snapshot", None) or not self.snapshot.rows_allowed:
            return "off"
        return super()._rows_mode()

    def _skip(self) -> Sequence[str]:
        return list(self.snapshot.key_qualifiers)

    def _row_key_error(self, key: str) -> str | None:
        if key in self.snapshot.key_qualifiers:
            return KEY_FIXED
        if self.gate is not None and key in self.gate.key_qualifiers:
            return KEY_FIXED
        return None

    def _identity(self) -> dict[str, Any]:
        return {"subject_id": self.snapshot.subject_id, "predicate_id": self.snapshot.predicate_id}

    def _qualifiers(self) -> dict[str, Any]:
        edited = super()._qualifiers()
        return {**self.snapshot.key_qualifiers, **edited}

    def _extra(self) -> dict[str, Any]:
        return {"event_id": self.snapshot.event_id} if self.snapshot.event_id else {}

    def _visibility(self, data: Mapping[str, Any]) -> dict[str, Any]:
        scope = str(data.get("scope") or CARRY)
        if scope == CARRY:
            return {}
        return self._scope_visibility(scope, data)

    def _request(
        self, data: Mapping[str, Any], proposition: dict[str, Any], visibility: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"proposition": proposition}
        if visibility:
            body["visibility"] = visibility
        locked = data.get("locked") or CARRY
        if locked != CARRY:
            body["locked"] = locked == _TRUE
        return body


def supersede_initial(
    fact: FactBody, predicate: Predicate | None
) -> tuple[dict[str, Any], list[QualifierRow]]:
    """A supersede form's pre-fill from the version: its object, polarity, the non-key
    qualifiers as rows, its flattened valid-time bounds (inner included) and, under a
    ``characters`` scope, its characters."""
    proposition = fact.proposition
    initial: dict[str, Any] = {"polarity": proposition.polarity}
    if proposition.object_id is not None:
        initial.update(object_form=ENTITY, object_id=proposition.object_id)
    elif proposition.literal is not None:
        literal = proposition.literal
        initial.update(
            object_form=LITERAL,
            literal_kind=literal.kind,
            literal_value=typed_text(literal.value),
            literal_unit=literal.unit or "",
        )
    elif proposition.object_set is not None:
        initial.update(object_form=SET, object_set="\n".join(proposition.object_set))
    else:
        initial["object_form"] = NONE
    for name in ("valid_from", "valid_from_inner", "valid_to", "valid_to_inner"):
        value = getattr(fact, name)
        initial[name] = "" if value is None else str(value)
    if fact.visibility.scope == CHARACTERS:
        initial["characters"] = list(fact.visibility.characters or [])
    keys = set(key_qualifier_names(fact, predicate))
    rows = [
        QualifierRow(i, k, inferred_kind(v), typed_text(v), False)
        for i, (k, v) in enumerate(
            (k, v) for k, v in sorted(proposition.qualifiers.items()) if k not in keys
        )
    ]
    return initial, rows


def prefill_data(initial: Mapping[str, Any], rows: Sequence[QualifierRow]) -> QueryDict:
    """The pre-fill as posted data (to build the body the unchanged form would send)."""
    data = QueryDict(mutable=True)
    for name, value in initial.items():
        if isinstance(value, list):
            data.setlist(name, [str(v) for v in value])
        else:
            data[name] = str(value)
    for row in rows:
        data[f"{ROWS_PREFIX}-{row.index}-key"] = row.key
        data[f"{ROWS_PREFIX}-{row.index}-type"] = row.kind
        data[f"{ROWS_PREFIX}-{row.index}-value"] = row.value
    data[f"{ROWS_PREFIX}-TOTAL_FORMS"] = str(len(rows))
    return data
