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

from collections.abc import Mapping, Sequence
from typing import Any

from django import forms
from django.http import QueryDict

from console.cast import Character, character_choices, default_actor
from console.engine.client import EngineError
from console.engine_form_errors import apply_engine_errors
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
