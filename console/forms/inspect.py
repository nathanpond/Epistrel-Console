"""The Retrieval explorer's form (#141): a GET form, so every question is a shareable URL.

Two groups. The **question** maps to ``GET /stories/{id}/retrieve`` (:meth:`RetrievalForm.query`):
``q`` (sent exactly as entered), ``as_of`` (blank: the head, not sent), ``actor`` (Console name
for ``actor_character_id``; the player is the default and is not sent) and
``include_quarantined`` (sent only when checked). The **display filters** ``kind``, ``tier`` and
``memory`` narrow the rendered candidates and never change the Engine call.

Input the form refuses makes no retrieve call: a blank or whitespace-only ``q``, one over the
Engine's 4,000 code points (``len(q)``, as the Engine counts; no HTML ``maxlength``, which
counts UTF-16 units), a negative or non-integer ``as_of``, an actor outside the cast (when the
story read succeeded), an unknown ``kind`` or ``tier``. ``as_of`` above the head passes through,
so the Engine's 422 (``details.head``) lands on the field. ``memory`` is never an error: a short
or odd value simply matches nothing.

``explain`` and ``explain_variant`` (hidden) mark a re-run of a past turn's retrieval (#142's
reconstruction banner); they are lenient: a value that isn't a whole number ≥ 1 cleans to
``None``, never a field error, and neither changes the Engine call.

A request with no ``q`` parameter at all (a filter-only link, #135 convention 11) is not a
question: :meth:`RetrievalForm.filters_only` keeps only the ``kind``/``tier`` errors.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from django import forms
from django.http import QueryDict

from console.cast import Character, character_choices, default_actor
from console.engine.client import EngineError
from console.engine.models import Predicate
from console.engine_form_errors import apply_engine_errors
from console.ids import is_ulid
from console.inspect import short_id
from console.retrieval import KINDS, TIERS

QUERY_TEXT_MAX = 4000  # the Engine's ``QUERY_TEXT_MAX`` (core/retrieval/pipeline.py)
# The question's parameters, kept by links that filter its result (the id cells, #141).
QUESTION_FIELDS = ("q", "as_of", "actor", "include_quarantined")

ENTER_A_QUESTION = "Enter a question."
AS_OF_INVALID = "Enter a whole number, 0 or more."
PLAYER_DEFAULT = "Player (default)"
PLAYER_SUFFIX = " (player)"
VIEWPOINT_UNRESOLVED = "viewpoint_unresolved"
# A 422 ``validation_error``'s ``details.field`` → the form field it lands on.
ENGINE_FIELDS: Mapping[str, str] = {
    "query.q": "q",
    "query.as_of": "as_of",
    "query.actor_character_id": "actor",
}


def too_long(length: int) -> str:
    return f"Questions are at most {QUERY_TEXT_MAX:,} characters (this one has {length:,})."


def as_of_help(head: int | None) -> str:
    """ "blank = head (turn N)"; "(no turns yet)" for an empty story; no turn when unknown."""
    if head is None:
        return "blank = head"
    if head < 0:
        return "blank = head (no turns yet)"
    return f"blank = head (turn {head})"


def _positive(value: str) -> int | None:
    """A whole number ≥ 1 written in ASCII digits, else ``None``."""
    return int(value) if value.isascii() and value.isdigit() and int(value) >= 1 else None


class RetrievalForm(forms.Form):
    """The question and the display filters. ``cast`` fills the actor select (``None`` when the
    story read failed: the select then offers the player default and the requested id);
    ``head`` is the story's last turn (``next_turn_index - 1``; ``None`` when unknown)."""

    q = forms.CharField(
        label="Question",
        strip=False,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 80}),
        error_messages={"required": ENTER_A_QUESTION},
    )
    actor = forms.ChoiceField(required=False, label="As")
    as_of = forms.IntegerField(
        required=False,
        min_value=0,
        label="After turn",
        error_messages={"invalid": AS_OF_INVALID, "min_value": AS_OF_INVALID},
    )
    include_quarantined = forms.BooleanField(required=False, label="Include quarantined")
    kind = forms.ChoiceField(
        required=False,
        label="Kind",
        choices=[("", "Any"), *((kind, kind.capitalize()) for kind in KINDS)],
    )
    tier = forms.ChoiceField(
        required=False, label="Tier", choices=[("", "Any"), *((tier, tier) for tier in TIERS)]
    )
    memory = forms.CharField(
        required=False, label="Memory id", help_text="full id or its last 8+ characters"
    )
    explain = forms.CharField(required=False, widget=forms.HiddenInput)
    explain_variant = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(
        self,
        data: QueryDict | dict[str, Any],
        *,
        cast: Sequence[Character] | None,
        head: int | None,
    ) -> None:
        bound = data.copy()
        self.default_actor = default_actor(cast) if cast is not None else None
        self.player = next((c.id for c in cast or () if c.is_player), None)
        if cast is not None and not bound.get("actor") and self.default_actor is not None:
            bound["actor"] = self.default_actor  # the select's default, kept sticky
        super().__init__(bound)
        self.cast = cast
        self.head = head
        self.fields["as_of"].help_text = as_of_help(head)
        actor = self.fields["actor"]
        if isinstance(actor, forms.ChoiceField):
            actor.choices = self._actor_choices(bound.get("actor") or "")

    def _actor_choices(self, requested: str) -> list[tuple[str, str]]:
        if self.cast is None:
            choices = [("", PLAYER_DEFAULT)]
            if requested:
                choices.append((requested, requested))
            return choices
        players = {c.id for c in self.cast if c.is_player}
        return [
            (cid, f"{label}{PLAYER_SUFFIX}" if cid in players else label)
            for cid, label in character_choices(self.cast)
        ]

    def clean_q(self) -> str:
        q: str = self.cleaned_data["q"]
        if not q.strip():
            raise forms.ValidationError(ENTER_A_QUESTION)
        if len(q) > QUERY_TEXT_MAX:
            raise forms.ValidationError(too_long(len(q)))
        return q

    def clean_memory(self) -> str:
        memory: str = self.cleaned_data["memory"]
        return memory.strip()

    def clean_explain(self) -> int | None:
        return _positive(self.cleaned_data["explain"])

    def clean_explain_variant(self) -> int | None:
        return _positive(self.cleaned_data["explain_variant"])

    def filters_only(self) -> None:
        """A request without ``q``: validate, then keep only the ``kind``/``tier`` errors (the
        question isn't being asked, so its fields can't be wrong)."""
        for name in list(self.errors):
            if name not in ("kind", "tier"):
                del self.errors[name]

    def query(self) -> dict[str, Any]:
        """``EngineClient.retrieve``'s keyword arguments (valid form only). The player, and a
        blank actor (the default when the cast is unknown), are not sent."""
        data = self.cleaned_data
        actor = data["actor"] or None
        return {
            "q": data["q"],
            "as_of": data["as_of"],
            "actor_character_id": None if actor == self.player else actor,
            "include_quarantined": bool(data["include_quarantined"]),
        }

    def value(self, name: str) -> str:
        """The submitted (raw) value of a control, for links that keep it."""
        found = self.data.get(name, "")
        return found if isinstance(found, str) else ""

    def filter_value(self, name: str) -> str:
        """A display filter's cleaned value ("" when blank or invalid)."""
        if not hasattr(self, "cleaned_data"):
            return ""
        found = self.cleaned_data.get(name)
        return found if isinstance(found, str) else ""

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422 on the field ``details.field`` names (as_of: "Turn must be 0\u2013<head>" when
        ``details.head`` is present), else above the form; ``viewpoint_unresolved``'s message
        above the form."""
        if exc.code == VIEWPOINT_UNRESOLVED:
            self.add_error(None, exc.message)
            return
        head = exc.details.get("head")
        if exc.details.get("field") == "query.as_of" and isinstance(head, int):
            self.add_error("as_of", f"Turn must be 0\u2013{head}")
            return
        if not apply_engine_errors(exc, self, aliases=ENGINE_FIELDS):
            self.add_error(None, exc.message)


# --- quarantining a memory (#143) ----------------------------------------------------------------

REASON_MAX = 500  # the Engine's ``QuarantineRequest.reason`` maxLength
RESERVED_REASON = "repeated_implication"  # the output gate's own reason (FR-GUARD-5)
REASON_TOO_LONG = "At most 500 characters."
REASON_BLANK = "Give a reason."
REASON_RESERVED = "`repeated_implication` is the Engine's own reason; give your own."


class QuarantineForm(forms.Form):
    """The quarantine page's reason (#143). Checked here, before any Engine call: ``\\r\\n``
    becomes ``\\n`` first (a browser's ``maxlength`` counts a newline once), then more than 500
    characters, blank after ``str.strip``, or exactly ``repeated_implication`` once trimmed
    (case-sensitive) is refused. ``cleaned_data["reason"]`` is the trimmed reason, as sent; a
    refused form re-renders the raw typed value."""

    reason = forms.CharField(
        label="Reason",
        required=False,
        strip=False,
        max_length=None,
        widget=forms.Textarea(attrs={"maxlength": REASON_MAX, "rows": 4, "cols": 60}),
    )

    def clean_reason(self) -> str:
        typed = str(self.cleaned_data.get("reason") or "").replace("\r\n", "\n")
        if len(typed) > REASON_MAX:
            raise forms.ValidationError(REASON_TOO_LONG)
        trimmed = typed.strip()
        if not trimmed:
            raise forms.ValidationError(REASON_BLANK)
        if trimmed == RESERVED_REASON:
            raise forms.ValidationError(REASON_RESERVED)
        return trimmed

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422 on the field ``details.field`` names (``body.reason`` → the reason), else above
        the form."""
        if not apply_engine_errors(exc, self):
            self.add_error(None, exc.message)


# --- sanitizing a memory (#144) ------------------------------------------------------------------

SANITIZE_MAX = 2000  # the Engine's ``SanitizeRequest`` span/surrogate maxLength, and a text's
SPAN_REQUIRED = "Enter the span to replace."
SPAN_ABSENT = "This span isn't in the memory's text"
SPAN_CHANGED = " (it may have changed since this page loaded)"
SURROGATE_SAME = "The surrogate is the same as the span; nothing would change"
REWRITE_INVALID = "The sanitized text must be 1\u20132,000 characters and not blank"
SURROGATE_HELP = "Empty removes the span"
SPAN_NOT_FOUND = "span_not_found"


def sanitize_too_long(length: int) -> str:
    return f"At most {SANITIZE_MAX:,} characters (this one has {length:,})."


def _lf(value: object) -> str:
    """A posted textarea's value with ``\\r\\n`` as ``\\n`` (what a browser sends for a newline)."""
    return str(value or "").replace("\r\n", "\n")


class SanitizeForm(forms.Form):
    """The sanitize page (#144): a ``span`` and its ``surrogate``, taken exactly as typed
    (``\\r\\n`` becomes ``\\n`` first, nothing is stripped), and the hidden ``shown_text`` (the
    memory's text as the explorer's question retrieved it; display and local checks only, never
    sent; dropped when blank or over 2,000 characters).

    Refused here, before any Engine call: an empty span, a span or surrogate over 2,000 code
    points (``len``, as the Engine counts; no HTML ``maxlength``, which counts UTF-16 units), a
    surrogate equal to the span and, when the text is known, a span not in it (Python ``in``, the
    Engine's ``str.count``) or a rewrite that would be blank or over 2,000 characters."""

    span = forms.CharField(
        label="Span",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 80}),
    )
    surrogate = forms.CharField(
        label="Surrogate",
        required=False,
        strip=False,
        help_text=SURROGATE_HELP,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 80}),
    )
    shown_text = forms.CharField(required=False, strip=False, widget=forms.HiddenInput)

    def clean_span(self) -> str:
        span = _lf(self.cleaned_data.get("span"))
        if not span:
            raise forms.ValidationError(SPAN_REQUIRED)
        if len(span) > SANITIZE_MAX:
            raise forms.ValidationError(sanitize_too_long(len(span)))
        return span

    def clean_surrogate(self) -> str:
        surrogate = _lf(self.cleaned_data.get("surrogate"))
        if len(surrogate) > SANITIZE_MAX:
            raise forms.ValidationError(sanitize_too_long(len(surrogate)))
        return surrogate

    def clean_shown_text(self) -> str:
        text = _lf(self.cleaned_data.get("shown_text"))
        return text if text.strip() and len(text) <= SANITIZE_MAX else ""

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        span, surrogate = data.get("span"), data.get("surrogate")
        if span is None or surrogate is None:
            return data
        text = data.get("shown_text") or ""
        if surrogate == span:
            self.add_error("surrogate", SURROGATE_SAME)
        elif text and span not in text:
            self.add_error("span", SPAN_ABSENT)
        elif text:
            rewritten = text.replace(span, surrogate)
            if not rewritten.strip() or len(rewritten) > SANITIZE_MAX:
                self.add_error("surrogate", REWRITE_INVALID)
        return data

    @property
    def text(self) -> str | None:
        """The shown text once cleaned (``None`` when not shown)."""
        cleaned = getattr(self, "cleaned_data", {})
        return cleaned.get("shown_text") or None

    def apply_engine_error(self, exc: EngineError) -> None:
        """422 ``span_not_found`` on the span (noting the text may have changed when the local
        check had passed); a ``validation_error`` on the field ``details.field`` names
        (``body.surrogate`` with the Console's rewrite text, ``body.span`` with the Engine's),
        else above the form."""
        if exc.code == SPAN_NOT_FOUND:
            self.add_error("span", SPAN_ABSENT + (SPAN_CHANGED if self.text else ""))
            return
        if exc.details.get("field") == "body.surrogate":
            self.add_error("surrogate", REWRITE_INVALID)
            return
        if not apply_engine_errors(exc, self):
            self.add_error(None, exc.message)


# --- requesting consolidation (#145) -------------------------------------------------------------

TURN_MAX = 2_147_483_647  # the Engine's ``ConsolidateRequest`` turn maximum
ENTER_A_TURN = "Enter a turn."
WHOLE_NUMBER = "Enter a whole number."
TURNS_START = "Turns start at 1."
TURN_TOO_HIGH = f"At most {TURN_MAX:,}."
ENDS_BEFORE_START = "The window ends before it starts."
_INTEGER = re.compile(r"^[+-]?\d+$")


def past_head(to_turn: int, head: int) -> str:
    return f"Turn {to_turn} is past the head (turn {head})"


def _turn_widget() -> forms.NumberInput:
    return forms.NumberInput(attrs={"min": 1, "max": TURN_MAX, "step": 1})


class ConsolidateForm(forms.Form):
    """The window a consolidation request covers (#145), checked before any Engine call: each
    turn present, a whole number, 1 to 2,147,483,647, ``from_turn`` ≤ ``to_turn``, and
    ``to_turn`` at most the hidden ``head`` (the Memory tab's ``head_turn`` at render) when one
    is carried. ``head`` is lenient: a value that isn't a whole number ≥ 1 cleans to ``None`` and
    drops the past-head check (a tampered head only loosens it; the Engine queues a past-head
    window harmlessly). The shared scope only: the form never sends ``scope``."""

    from_turn = forms.CharField(label="From turn", required=False, widget=_turn_widget())
    to_turn = forms.CharField(label="To turn", required=False, widget=_turn_widget())
    head = forms.CharField(required=False, widget=forms.HiddenInput)

    @staticmethod
    def _turn(value: object) -> int:
        typed = str(value or "").strip()
        if not typed:
            raise forms.ValidationError(ENTER_A_TURN)
        if not _INTEGER.match(typed):
            raise forms.ValidationError(WHOLE_NUMBER)
        turn = int(typed)
        if turn < 1:
            raise forms.ValidationError(TURNS_START)
        if turn > TURN_MAX:
            raise forms.ValidationError(TURN_TOO_HIGH)
        return turn

    def clean_from_turn(self) -> int:
        return self._turn(self.cleaned_data.get("from_turn"))

    def clean_to_turn(self) -> int:
        return self._turn(self.cleaned_data.get("to_turn"))

    def clean_head(self) -> int | None:
        typed = str(self.cleaned_data.get("head") or "").strip()
        return int(typed) if typed.isascii() and typed.isdigit() and int(typed) >= 1 else None

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        start, end, head = data.get("from_turn"), data.get("to_turn"), data.get("head")
        if isinstance(start, int) and isinstance(end, int) and start > end:
            self.add_error("to_turn", ENDS_BEFORE_START)
        elif isinstance(end, int) and isinstance(head, int) and end > head:
            self.add_error("to_turn", past_head(end, head))
        return data

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422 ``validation_error`` on the field ``details.field`` names (``body.from_turn``,
        ``body.to_turn``), else above the form."""
        if not apply_engine_errors(exc, self):
            self.add_error(None, exc.message)


# --- the Canon tab (#137) ------------------------------------------------------------------------

MINUTE_MAX = 2**63 - 1  # the Engine stores story-minutes as a signed 64-bit integer
SUBJECT_INVALID = "Enter a 26-character entity id (digits and capital letters, no I, L, O or U)"
PREDICATE_INVALID = (
    "Enter a 26-character predicate id (digits and capital letters, no I, L, O or U)"
)
MINUTE_INVALID = "Enter a whole number of minutes, 0 or more"
MINUTE_TOO_LARGE = "That minute is too large"
RECORD_TIME_INVALID = "Enter a date and time"
BOTH_TIMES = (
    "The Engine lists every version only in today's record — clear 'as recorded at' or "
    "'include superseded'"
)
PREDICATES_UNAVAILABLE = "Predicate names unavailable — enter a predicate id"
ANY_PREDICATE = "Any predicate"
OMNISCIENT_TEXT = "This read needs the omniscient viewpoint"
NOT_IN_REGISTRY = " (not in registry)"
TICKED = frozenset({"on", "1", "true"})
CANON_FIELDS = ("subject", "predicate", "as_of_story_time", "as_of_record_time")
_LOCAL_MOMENT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$")


class TickBox(forms.CheckboxInput):
    """A checkbox whose submitted ``on``, ``1`` or ``true`` (any case) counts as ticked; any
    other value, or none, as unticked."""

    def value_from_datadict(self, data: Any, files: Any, name: str) -> bool:
        value = data.get(name)
        return isinstance(value, str) and value.strip().lower() in TICKED


def record_moment(typed: str) -> datetime | None:
    """A ``datetime-local`` value (``YYYY-MM-DDTHH:MM`` or ``…:SS``) read as UTC, moved to the
    end of that second (``…:SS.999999``: ``tx_from`` has microsecond precision); ``None`` when
    it is not one."""
    found = _LOCAL_MOMENT.match(typed)
    if found is None:
        return None
    year, month, day, hour, minute, second = (int(p) if p else 0 for p in found.groups())
    try:
        return datetime(year, month, day, hour, minute, second, 999999, tzinfo=UTC)
    except ValueError:
        return None


class CanonForm(forms.Form):
    """The Canon tab's GET filters; each name is the Engine's query parameter (see
    ``console/views/inspect_canon.py``). ``registry`` (the predicates in registry order, or
    ``None`` when the registry read failed) makes ``predicate`` a select or a free-text id
    field. Subject and predicate are trimmed and upper-cased before validation, so the form
    echoes the normalised values, then checked against the Engine's strict id pattern (no
    other normalising: an ``l`` or ``O`` stays an error)."""

    subject = forms.CharField(
        required=False,
        label="Subject",
        widget=forms.TextInput(attrs={"list": "canon-cast", "size": 30}),
    )
    predicate = forms.CharField(required=False, label="Predicate")
    as_of_story_time = forms.CharField(
        required=False,
        label="True at story-minute",
        widget=forms.NumberInput(attrs={"min": 0, "step": 1}),
    )
    as_of_record_time = forms.CharField(
        required=False,
        label="As recorded at",
        help_text="UTC",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "step": 1}),
    )
    include_superseded = forms.BooleanField(
        required=False, label="Include superseded", widget=TickBox
    )

    def __init__(
        self, data: QueryDict | dict[str, Any], *, registry: Sequence[Predicate] | None
    ) -> None:
        bound = data.copy()
        for name in ("subject", "predicate"):
            value = bound.get(name)
            if isinstance(value, str):
                bound[name] = value.strip().upper()
        super().__init__(bound)
        self.registry = registry
        if registry is None:
            self.fields["predicate"].help_text = PREDICATES_UNAVAILABLE
            self.fields["predicate"].widget.attrs["size"] = 30
        else:
            requested = bound.get("predicate") or ""
            choices = [("", ANY_PREDICATE), *((p.id, f"{p.name} ({p.layer})") for p in registry)]
            if requested and requested not in {p.id for p in registry}:
                choices.append((requested, f"{short_id(requested)}{NOT_IN_REGISTRY}"))
            self.fields["predicate"].widget = forms.Select(choices=choices)

    def clean_subject(self) -> str:
        subject: str = self.cleaned_data["subject"]
        if subject and not is_ulid(subject):
            raise forms.ValidationError(SUBJECT_INVALID)
        return subject

    def clean_predicate(self) -> str:
        predicate: str = self.cleaned_data["predicate"]
        if predicate and not is_ulid(predicate):
            raise forms.ValidationError(PREDICATE_INVALID)
        return predicate

    def clean_as_of_story_time(self) -> int | None:
        typed = str(self.cleaned_data.get("as_of_story_time") or "").strip()
        if not typed:
            return None
        if not (typed.isascii() and typed.isdigit()):
            raise forms.ValidationError(MINUTE_INVALID)
        minute = int(typed)
        if minute > MINUTE_MAX:
            raise forms.ValidationError(MINUTE_TOO_LARGE)
        return minute

    def clean_as_of_record_time(self) -> datetime | None:
        typed = str(self.cleaned_data.get("as_of_record_time") or "").strip()
        if not typed:
            return None
        moment = record_moment(typed)
        if moment is None:
            raise forms.ValidationError(RECORD_TIME_INVALID)
        return moment

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if self.data.get("as_of_record_time", "").strip() and data.get("include_superseded"):
            self.add_error(None, BOTH_TIMES)
        return data

    def query(self) -> dict[str, Any]:
        """``EngineClient.list_facts``' filter arguments (valid form only; no cursor)."""
        data = self.cleaned_data
        return {
            "subject": data["subject"] or None,
            "predicate": data["predicate"] or None,
            "as_of_story_time": data["as_of_story_time"],
            "as_of_record_time": data["as_of_record_time"],
            "include_superseded": bool(data["include_superseded"]),
        }

    @property
    def any_filter(self) -> bool:
        """Whether the request sets any filter (the empty result's wording)."""
        typed = any(str(self.data.get(name) or "").strip() for name in CANON_FIELDS)
        return typed or bool(self["include_superseded"].value())

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422: ``viewpoint_unresolved`` → convention 10's line above the filters; a
        ``validation_error`` beside the filter ``details.field`` names (``query.<name>``), else
        above the filters."""
        if exc.code == VIEWPOINT_UNRESOLVED:
            self.add_error(None, OMNISCIENT_TEXT)
            return
        aliases = {f"query.{name}": name for name in self.fields}
        field_name = exc.details.get("field")
        if isinstance(field_name, str) and field_name in aliases:
            self.add_error(aliases[field_name], exc.message)
            return
        if not apply_engine_errors(exc, self, aliases=aliases):
            self.add_error(None, exc.message)
