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

from console.cast import OMNISCIENT, Character, character_choices, default_actor, viewpoint_for
from console.engine.client import EngineError
from console.engine.models import Predicate
from console.engine_form_errors import apply_engine_errors
from console.forms.history import as_of_options
from console.ids import is_ulid
from console.inspect import short_id
from console.paging import cast_id
from console.retrieval import KINDS, TIERS
from console.wording import UNKNOWN_CHARACTER

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


def local_moment(typed: str) -> datetime | None:
    """A ``datetime-local`` value (``YYYY-MM-DDTHH:MM`` or ``…:SS``) read as UTC (convention 4);
    ``None`` when it is not one (#149's eval windows)."""
    found = _LOCAL_MOMENT.match(typed)
    if found is None:
        return None
    year, month, day, hour, minute, second = (int(p) if p else 0 for p in found.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def record_moment(typed: str) -> datetime | None:
    """:func:`local_moment` moved to the end of that second (``…:SS.999999``: ``tx_from`` has
    microsecond precision); ``None`` when it is not a ``datetime-local`` value."""
    moment = local_moment(typed)
    return moment.replace(microsecond=999999) if moment is not None else None


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


# --- the Ledger tab's filters (#148) -------------------------------------------------------------

ANY = "any"
# Console value → the Engine's ``state`` (``None``: not sent).
LEDGER_STATES: Mapping[str, str | None] = {
    ANY: None,
    "open": "open",
    "resolved": "resolved",
    "expired": "expired",
}
# Console value → the Engine's ``discharged`` (``None``: not sent).
LEDGER_DISCHARGED: Mapping[str, bool | None] = {
    ANY: None,
    "discharged": True,
    "undischarged": False,
}
LEDGER_FIELDS = ("state", "discharged")


class LedgerForm(forms.Form):
    """The Ledger tab's GET filters (``console/views/inspect_ledger.py``): ``state`` (any, open,
    resolved, expired) and ``discharged`` (any, discharged, undischarged). Blank is ``any``; an
    unknown value is an error beside its select, and the view then makes no ledger call. The
    page cursor ``after_id`` isn't a field: the view passes it through as given."""

    state = forms.ChoiceField(
        required=False,
        label="State",
        choices=[(ANY, "Any"), ("open", "Open"), ("resolved", "Resolved"), ("expired", "Expired")],
    )
    discharged = forms.ChoiceField(
        required=False,
        label="Entries",
        choices=[(ANY, "Any"), ("discharged", "Discharged"), ("undischarged", "Undischarged")],
    )

    def clean_state(self) -> str:
        return str(self.cleaned_data.get("state") or ANY)

    def clean_discharged(self) -> str:
        return str(self.cleaned_data.get("discharged") or ANY)

    def query(self) -> dict[str, Any]:
        """``EngineClient.inspect_ledger``' filter arguments (valid form only; no cursor)."""
        return {
            "state": LEDGER_STATES[self.cleaned_data["state"]],
            "discharged": LEDGER_DISCHARGED[self.cleaned_data["discharged"]],
        }

    @property
    def state_filter(self) -> bool:
        """Whether ``state`` narrows the thunks (a ``discharged`` filter narrows entries only)."""
        return bool(self.cleaned_data.get("state", ANY) != ANY)

    @property
    def any_filter(self) -> bool:
        return any(self.cleaned_data.get(name, ANY) != ANY for name in LEDGER_FIELDS)

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422: ``viewpoint_unresolved`` → convention 10's line above the filters; otherwise
        the Engine's message beside the select ``details.field`` names (``query.state`` /
        ``query.discharged``), else above the filters (a bad ``after_id`` lands there)."""
        if exc.code == VIEWPOINT_UNRESOLVED:
            self.add_error(None, OMNISCIENT_TEXT)
            return
        aliases = {f"query.{name}": name for name in LEDGER_FIELDS}
        aliases.update({name: name for name in LEDGER_FIELDS})
        field_name = exc.details.get("field")
        if isinstance(field_name, str) and field_name in aliases:
            self.add_error(aliases[field_name], exc.message)
            return
        self.add_error(None, exc.message)


# --- the Scene tab and the entity page (#58) -----------------------------------------------------

SCENE_TURN_INVALID = "Enter a whole number, 0 or more."
VIEWPOINT_NOT_IN_CAST = "That viewpoint isn't one of this story's cast"
OMNISCIENT_LABEL = "Omniscient"
SCENE_FIELDS = ("as_of", "viewpoint")


def turn_too_high(head: int) -> str:
    return f"Turn must be 0\u2013{head}"


class SceneForm(forms.Form):
    """The Scene tab's and the entity page's GET form (#58): **Turn** (``as_of``; blank = the
    head, not sent) and **Viewpoint** (always sent: "Omniscient" first, then each cast member as
    ``character:<id>`` in the Engine's cast order, the player marked; no "story default" entry,
    since the story default can't be ``omniscient`` and may be unset, and either would 422).

    ``cast`` is ``None`` when the page's story read failed: the select then offers "Omniscient"
    and the requested value shown raw, and the upper bound isn't checked (the Engine's 422 covers
    it). ``head`` is the active chain's last turn (``None`` disables the upper bound).
    ``default_viewpoint`` is the story's ``settings.default_output_viewpoint``: the initial
    viewpoint when it names a cast member, else ``omniscient``.

    A non-integer, negative or past-head turn and a viewpoint outside the select are errors
    beside their field, and the views then make no scene or profile call."""

    as_of = forms.IntegerField(
        required=False,
        min_value=0,
        label="Turn",
        error_messages={"invalid": SCENE_TURN_INVALID, "min_value": SCENE_TURN_INVALID},
        widget=forms.NumberInput(attrs={"list": "scene-turns", "min": 0}),
    )
    viewpoint = forms.ChoiceField(
        label="Viewpoint",
        error_messages={"invalid_choice": VIEWPOINT_NOT_IN_CAST, "required": VIEWPOINT_NOT_IN_CAST},
    )

    def __init__(
        self,
        data: QueryDict | dict[str, Any],
        *,
        cast: Sequence[Character] | None,
        head: int | None,
        default_viewpoint: str | None,
    ) -> None:
        bound = data.copy()
        self.cast = cast
        self.head = head
        self.choices = self._choices(cast, bound.get("viewpoint") or "")
        self.initial_viewpoint = OMNISCIENT
        if default_viewpoint in {value for value, _ in self.choices} and cast is not None:
            self.initial_viewpoint = default_viewpoint or OMNISCIENT
        if "viewpoint" not in bound:
            bound["viewpoint"] = self.initial_viewpoint  # a first visit reads at once
        super().__init__(bound)
        viewpoint = self.fields["viewpoint"]
        if isinstance(viewpoint, forms.ChoiceField):
            viewpoint.choices = self.choices
        if head is not None:
            self.fields["as_of"].widget.attrs["max"] = head

    @staticmethod
    def _choices(cast: Sequence[Character] | None, requested: str) -> list[tuple[str, str]]:
        choices = [(OMNISCIENT, OMNISCIENT_LABEL)]
        if cast is None:
            if requested and requested != OMNISCIENT:
                choices.append((requested, requested))
            return choices
        players = {c.id for c in cast if c.is_player}
        return [
            *choices,
            *(
                (viewpoint_for(cid), f"{label}{PLAYER_SUFFIX}" if cid in players else label)
                for cid, label in character_choices(cast)
            ),
        ]

    def clean_as_of(self) -> int | None:
        as_of: int | None = self.cleaned_data.get("as_of")
        if as_of is not None and self.head is not None and as_of > self.head:
            raise forms.ValidationError(turn_too_high(self.head))
        return as_of

    def query(self) -> dict[str, Any]:
        """``get_scene``'s / ``get_entity_profile``'s keyword arguments (valid form only)."""
        return {"as_of": self.cleaned_data["as_of"], "viewpoint": self.cleaned_data["viewpoint"]}

    def value(self, name: str) -> str:
        """The submitted (raw) value of a control ("" when absent)."""
        found = self.data.get(name, "")
        return found if isinstance(found, str) else ""

    @property
    def options(self) -> list[int]:
        """The Turn datalist (#52's :func:`console.forms.history.as_of_options`); none when the
        head is unknown."""
        return as_of_options(self.head) if self.head is not None else []

    def apply_engine_error(self, exc: EngineError) -> None:
        """A 422: ``query.as_of`` with ``details.head`` → "Turn must be 0\u2013<head>" beside Turn
        (without a head, the Engine's message there); ``viewpoint_unresolved`` or any 422 on
        ``query.viewpoint`` → beside Viewpoint; anything else above the form."""
        field_name = exc.details.get("field")
        if exc.code == VIEWPOINT_UNRESOLVED or field_name == "query.viewpoint":
            self.add_error("viewpoint", VIEWPOINT_NOT_IN_CAST)
            return
        if field_name == "query.as_of":
            head = exc.details.get("head")
            if isinstance(head, int) and not isinstance(head, bool):
                self.add_error("as_of", turn_too_high(head))
            else:
                self.add_error("as_of", exc.message)
            return
        self.add_error(None, exc.message)


# --- the Knowledge tab's Beliefs section (#190) ---------------------------------------------------

PROPOSITION_INVALID = (
    "Enter a 26-character proposition id (digits and capital letters, no I, L, O or U)"
)
NOT_IN_CAST_SUFFIX = " (not in the cast)"
UNKNOWN_CHARACTER_REASON = "unknown_character"
# A 422 ``validation_error``'s ``details.field`` → the Beliefs filter it lands on.
BELIEFS_FIELDS: Mapping[str, str] = {
    "query.character": "character",
    "query.as_of": "as_of",
    "query.proposition_id": "proposition_id",
}
AFTER_FIELD = "query.after"


class BeliefsForm(forms.Form):
    """The Beliefs section's GET filters (``console/views/inspect_beliefs.py``); each name is the
    Engine's query parameter. The cursor ``after`` isn't a field: Apply starts over.

    **Character.** With the story read (``cast`` given), a select of every non-player cast member
    in cast order, labelled by ``labels`` (C4's, from the view); absent or blank, it defaults to
    the first of them (:attr:`defaulted`). A URL id is trimmed and upper-cased, then read by
    C13's :func:`console.paging.cast_id`: a valid id outside the options (the player, or an id
    outside the cast) is kept as an extra option, last, and sent; an invalid one is
    :data:`console.paging.CHARACTER_INVALID`. Without the story read (``cast`` ``None``), a
    free-text id field read the same way. Blank (no option, or nothing typed) cleans to "": the
    view then makes no call.

    **As of turn** is parsed as Canon parses a minute (ASCII digits only), up to
    :data:`TURN_MAX`; **Proposition id** is trimmed, upper-cased and checked with ``is_ulid``."""

    character = forms.CharField(required=False, label="Character")
    as_of = forms.CharField(
        required=False,
        label="As of turn",
        help_text="Blank for now",
        widget=forms.NumberInput(attrs={"step": 1}),
    )
    proposition_id = forms.CharField(
        required=False,
        label="Proposition id",
        help_text="Optional — keep one proposition",
        widget=forms.TextInput(attrs={"size": 30}),
    )

    def __init__(
        self,
        data: QueryDict | dict[str, Any],
        *,
        cast: Sequence[Character] | None,
        labels: Mapping[str, str],
    ) -> None:
        bound = data.copy()
        for name in ("character", "proposition_id"):
            value = bound.get(name)
            if isinstance(value, str):
                bound[name] = value.strip().upper()
        self.cast = cast
        self.defaulted = False
        self.option_ids: tuple[str, ...] = ()
        requested = str(bound.get("character") or "")
        if cast is not None:
            options = [(c.id, labels.get(c.id) or short_id(c.id)) for c in cast if not c.is_player]
            self.option_ids = tuple(cid for cid, _ in options)
            if not requested and options:
                requested = options[0][0]
                bound["character"] = requested
                self.defaulted = True
            typed = cast_id(requested, self.option_ids)
            if typed.kept:
                players = {c.id for c in cast if c.is_player}
                cast_ids = {c.id for c in cast}
                if requested in cast_ids:
                    label = labels.get(requested) or short_id(requested)
                    if requested in players and PLAYER_SUFFIX not in label:
                        label += PLAYER_SUFFIX
                else:
                    label = f"{short_id(requested)}{NOT_IN_CAST_SUFFIX}"
                options.append((requested, label))
        super().__init__(bound)
        if cast is not None:
            self.fields["character"].widget = forms.Select(choices=options)
        else:
            self.fields["character"].widget = forms.TextInput(attrs={"size": 30})

    def clean_character(self) -> str:
        typed = cast_id(str(self.cleaned_data.get("character") or ""), self.option_ids)
        if typed.error:
            raise forms.ValidationError(typed.error)
        return typed.value

    def clean_as_of(self) -> int | None:
        typed = str(self.cleaned_data.get("as_of") or "").strip()
        if not typed:
            return None
        if not (typed.isascii() and typed.isdigit()):
            raise forms.ValidationError(AS_OF_INVALID)
        turn = int(typed)
        if turn > TURN_MAX:
            raise forms.ValidationError(TURN_TOO_HIGH)
        return turn

    def clean_proposition_id(self) -> str:
        proposition_id: str = self.cleaned_data["proposition_id"]
        if proposition_id and not is_ulid(proposition_id):
            raise forms.ValidationError(PROPOSITION_INVALID)
        return proposition_id

    def valid(self, name: str) -> bool:
        """Whether field ``name`` cleaned (the header keeps the parts that did)."""
        self.is_valid()
        return name in self.cleaned_data and name not in self.errors

    def query(self) -> dict[str, Any]:
        """``EngineClient.inspect_beliefs``' filter arguments (valid form only; no cursor)."""
        data = self.cleaned_data
        return {
            "character": data["character"],
            "as_of": data["as_of"],
            "proposition_id": data["proposition_id"] or None,
        }

    def apply_engine_error(self, exc: EngineError) -> bool:
        """A 422: ``unknown_character`` → :data:`console.wording.UNKNOWN_CHARACTER` beside
        Character; any other the Engine's message beside the field ``details.field`` names, else
        above the filters. ``True`` when it named ``query.after`` (the line gets "First page")."""
        field_name = exc.details.get("field")
        if exc.details.get("reason") == UNKNOWN_CHARACTER_REASON:
            self.add_error("character", UNKNOWN_CHARACTER)
            return False
        if isinstance(field_name, str) and field_name in BELIEFS_FIELDS:
            self.add_error(BELIEFS_FIELDS[field_name], exc.message)
            return False
        self.add_error(None, exc.message)
        return field_name == AFTER_FIELD


# --- the Knowledge tab's Divergences section (#192) -----------------------------------------------

ANY_SPEAKER = "Any"
# A 422 ``validation_error``'s ``details.field`` → the Divergences filter it lands on.
DIVERGENCES_FIELDS: Mapping[str, str] = {
    "query.speaker": "speaker",
    "query.subject": "subject",
    "query.as_of": "as_of",
}
AFTER_ID_FIELD = "query.after_id"


class DivergencesForm(forms.Form):
    """The Divergences section's GET filters (``console/views/inspect_divergences.py``); each
    name is the Engine's query parameter. The cursor ``after_id`` isn't a field: Apply starts
    over.

    **Speaker.** With the story read (``cast`` given), a select: "Any" (value "", not sent), then
    the whole cast in cast order (the player included: a player can lie), labelled by ``labels``
    (C4's, from the view). A URL id is trimmed and upper-cased, then read by C13's
    :func:`console.paging.cast_id`: a valid id outside the cast is kept as an extra option, last
    ("<short id> (not in the cast)"), and sent; an invalid one is
    :data:`console.paging.CHARACTER_INVALID`. Without the story read (``cast`` ``None``), a
    free-text id field read the same way.

    **Subject** is an entity id (trimmed, upper-cased, ``is_ulid``; :data:`SUBJECT_INVALID`);
    **As of turn** is parsed as the Beliefs section's (ASCII digits only, up to
    :data:`TURN_MAX`)."""

    speaker = forms.CharField(required=False, label="Speaker")
    subject = forms.CharField(
        required=False, label="Subject", widget=forms.TextInput(attrs={"size": 30})
    )
    as_of = forms.CharField(
        required=False,
        label="As of turn",
        help_text="Blank for now",
        widget=forms.NumberInput(attrs={"step": 1}),
    )

    def __init__(
        self,
        data: QueryDict | dict[str, Any],
        *,
        cast: Sequence[Character] | None,
        labels: Mapping[str, str],
    ) -> None:
        bound = data.copy()
        for name in ("speaker", "subject"):
            value = bound.get(name)
            if isinstance(value, str):
                bound[name] = value.strip().upper()
        self.cast = cast
        self.cast_ids: tuple[str, ...] = tuple(c.id for c in cast or ())
        options: list[tuple[str, str]] = [("", ANY_SPEAKER)]
        if cast is not None:
            options += [(c.id, labels.get(c.id) or short_id(c.id)) for c in cast]
            requested = str(bound.get("speaker") or "")
            if cast_id(requested, self.cast_ids).kept:
                options.append((requested, f"{short_id(requested)}{NOT_IN_CAST_SUFFIX}"))
        super().__init__(bound)
        if cast is not None:
            self.fields["speaker"].widget = forms.Select(choices=options)
        else:
            self.fields["speaker"].widget = forms.TextInput(attrs={"size": 30})

    def clean_speaker(self) -> str:
        typed = cast_id(str(self.cleaned_data.get("speaker") or ""), self.cast_ids)
        if typed.error:
            raise forms.ValidationError(typed.error)
        return typed.value

    def clean_subject(self) -> str:
        subject: str = self.cleaned_data["subject"]
        if subject and not is_ulid(subject):
            raise forms.ValidationError(SUBJECT_INVALID)
        return subject

    def clean_as_of(self) -> int | None:
        typed = str(self.cleaned_data.get("as_of") or "").strip()
        if not typed:
            return None
        if not (typed.isascii() and typed.isdigit()):
            raise forms.ValidationError(AS_OF_INVALID)
        turn = int(typed)
        if turn > TURN_MAX:
            raise forms.ValidationError(TURN_TOO_HIGH)
        return turn

    def query(self) -> dict[str, Any]:
        """``EngineClient.inspect_divergences``' filter arguments (valid form only; no cursor):
        ``None`` for a blank filter, which isn't sent."""
        data = self.cleaned_data
        return {
            "speaker": data["speaker"] or None,
            "subject": data["subject"] or None,
            "as_of": data["as_of"],
        }

    @property
    def any_filter(self) -> bool:
        """Whether Speaker, Subject or As of is set (valid form only; a cursor isn't a filter)."""
        return any(v is not None for v in self.query().values())

    def normalised(self, name: str) -> str:
        """The bound (trimmed, upper-cased) value of ``speaker`` or ``subject``, valid or not."""
        return str(self.data.get(name) or "")

    def apply_engine_error(self, exc: EngineError) -> bool:
        """A 422: ``unknown_character`` → :data:`console.wording.UNKNOWN_CHARACTER` beside
        Speaker; any other the Engine's message beside the filter ``details.field`` names, else
        above the filters. ``True`` when it named ``query.after_id`` (the line gets "First
        page")."""
        field_name = exc.details.get("field")
        if exc.details.get("reason") == UNKNOWN_CHARACTER_REASON:
            self.add_error("speaker", UNKNOWN_CHARACTER)
            return False
        if isinstance(field_name, str) and field_name in DIVERGENCES_FIELDS:
            self.add_error(DIVERGENCES_FIELDS[field_name], exc.message)
            return False
        self.add_error(None, exc.message)
        return field_name == AFTER_ID_FIELD
