"""The history browser's controls (#52): a GET form, so every view is a shareable URL.

Each control maps to exactly one ``GET /stories/{id}/history`` query parameter
(:meth:`HistoryForm.query`); an optional control left blank sends nothing. The page size is
required (50, 200 or 1000; default 50) and always sent as ``limit``.

"Show all traces" (``traces``, #147) is the one control that isn't a history parameter: it
asks the page to read each structured turn's trace (:attr:`HistoryForm.show_traces`).

Input the form refuses makes no history call: a page size outside the three, a non-integer or
negative turn, and audit records asked for without the omniscient viewpoint (the Engine's
403 ``omniscient_required``, refused here first). A well-formed ``as_of`` above the head passes
through, so the Engine's 422 shows.

"Player view" (``player_view``, #62) asks for the filtered (``character``) or the full, marked
(``story``) history of a character viewpoint; blank sends nothing (the story's setting). An
explicit ``omniscient`` viewpoint never sends it: :meth:`HistoryForm.clean` blanks it in
``cleaned_data`` (the submitted value stays, so the page keeps it for a character viewpoint).

"View" (``view``, #304) picks the corrected history (blank, main's form byte for byte:
``checkpoint`` is ignored and :meth:`HistoryForm.query` returns main's keys) or the history
**as originally recorded** (``as_recorded``, FR-STORE-23). That view is omniscient-only (E2):
:meth:`HistoryForm.clean` forces the omniscient viewpoint and blanks every control the Engine
doesn't read there (actor, player view, audit, summary, traces, ``before_turn``; ``as_of`` and
``limit`` are ignored, their errors dropped), and :meth:`HistoryForm.query` sends only ``view``,
``viewpoint``, ``as_of`` (D7's ``checkpoint``, ``N`` or ``N+k``) and ``variant``.
"""

from collections.abc import Sequence
from typing import Any

from django import forms
from django.http import QueryDict

from console.cast import OMNISCIENT, Character, character_choices, viewpoint_for

PAGE_SIZES = (50, 200, 1000)
DEFAULT_PAGE_SIZE = 50
AUDIT_NEEDS_OMNISCIENT = "Audit records need the omniscient viewpoint"
DATALIST_ALL_UP_TO = 500  # every turn is listed up to this head; above it, a sample
DATALIST_STEP = 50
DATALIST_TAIL = 50
# #62: shared with the play transcript's toggle (#196).
PLAYER_VIEW_CHOICES = [
    ("", "story setting"),
    ("character", "Character (filtered)"),
    ("story", "Story (everything, marked)"),
]
AS_RECORDED = "as_recorded"
# #304: the corrected history (main's page) or the history as originally recorded.
VIEW_CHOICES = [
    ("", "Corrected (as the story stands)"),
    (AS_RECORDED, "As originally recorded"),
]
CHECKPOINT_HELP = "A turn (12), or a turn and slot (12+1); blank for the latest"
# The controls the as-recorded view doesn't read, blanked in ``cleaned_data`` (#304).
AS_RECORDED_BLANKED: dict[str, Any] = {
    "actor_character_id": "",
    "player_view": "",
    "include_audit": False,
    "include_summary": False,
    "traces": False,
    "before_turn": None,
    "as_of": None,
    "limit": None,
}
# Submitted controls a disabled as-recorded control carries in a hidden input (#304).
CARRIED = ("viewpoint", "actor_character_id", "include_audit", "include_summary", "traces", "limit")


class HistoryForm(forms.Form):
    """The controls. ``cast`` fills the viewpoint and actor selects (``GET /stories/{id}``)."""

    viewpoint = forms.ChoiceField(required=False, label="Viewpoint")
    player_view = forms.ChoiceField(
        required=False, choices=PLAYER_VIEW_CHOICES, label="Player view"
    )
    as_of = forms.IntegerField(required=False, min_value=0, label="As of turn")
    actor_character_id = forms.ChoiceField(required=False, label="Actor")
    include_audit = forms.BooleanField(required=False, label="Include audit")
    include_summary = forms.BooleanField(required=False, label="Include summary")
    # #147: open every beat's Trace and load each turn's escalation and repair. A page control,
    # not an Engine parameter: :meth:`query` leaves it out.
    traces = forms.BooleanField(required=False, label="Show all traces")
    limit = forms.TypedChoiceField(
        choices=[(str(n), str(n)) for n in PAGE_SIZES],
        coerce=int,
        initial=str(DEFAULT_PAGE_SIZE),
        label="Page size",
    )
    before_turn = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)
    variant = forms.CharField(required=False, widget=forms.HiddenInput)
    # #304: which history; ``checkpoint`` (D7's ``BoundaryField``, added in ``__init__``) is
    # read only on the as-recorded view.
    view = forms.ChoiceField(required=False, choices=VIEW_CHOICES, label="View")

    def __init__(self, data: QueryDict | dict[str, Any], *, cast: Sequence[Character]) -> None:
        bound = data.copy()
        if not bound.get("limit"):
            bound["limit"] = str(DEFAULT_PAGE_SIZE)  # the required control's default
        super().__init__(bound)
        # Imported here: console.forms.world imports console.forms.inspect, which imports this
        # module's as_of_options.
        from console.forms.world import BoundaryField

        self.fields["checkpoint"] = BoundaryField(
            required=False, label="Checkpoint", help_text=CHECKPOINT_HELP
        )
        characters = character_choices(cast)
        viewpoint = self.fields["viewpoint"]
        if isinstance(viewpoint, forms.ChoiceField):
            viewpoint.choices = [
                ("", "story default"),
                *[(viewpoint_for(cid), label) for cid, label in characters],
                (OMNISCIENT, OMNISCIENT),
            ]
        actor = self.fields["actor_character_id"]
        if isinstance(actor, forms.ChoiceField):
            actor.choices = [("", "any"), *characters]

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        if self.as_recorded:
            return self._clean_as_recorded(cleaned or {})
        self._ignore(cleaned, "checkpoint")  # #304: read on the as-recorded view only
        if cleaned and cleaned.get("include_audit") and cleaned.get("viewpoint") != OMNISCIENT:
            self.add_error("include_audit", AUDIT_NEEDS_OMNISCIENT)
        if cleaned and cleaned.get("viewpoint") == OMNISCIENT and "player_view" in cleaned:
            cleaned["player_view"] = ""  # applies to a character viewpoint only (#62)
        return cleaned

    def _ignore(self, cleaned: dict[str, Any] | None, *names: str) -> None:
        """Drop the errors of controls the current view doesn't read (#304); their cleaned
        value is ``None``."""
        for name in names:
            self.errors.pop(name, None)  # set by now: clean() runs inside full_clean()
            if cleaned is not None:
                cleaned[name] = None

    def _clean_as_recorded(self, cleaned: dict[str, Any]) -> dict[str, Any]:
        """#304: omniscient whatever was submitted (E2), every control the Engine doesn't read
        on this view blanked."""
        self._ignore(cleaned, "viewpoint", *AS_RECORDED_BLANKED)
        cleaned.update(AS_RECORDED_BLANKED)
        cleaned["viewpoint"] = OMNISCIENT
        return cleaned

    def query(self) -> dict[str, Any]:
        """``get_history``'s keyword arguments (valid form only); ``None``/``False`` values
        are left out of the request by the client. As recorded (#304): ``view``, the omniscient
        viewpoint, the checkpoint as ``as_of`` and ``variant``, nothing else."""
        data = self.cleaned_data
        if self.as_recorded:
            return {
                "view": AS_RECORDED,
                "viewpoint": OMNISCIENT,
                "as_of": data["checkpoint"] or None,
                "variant": data["variant"] or None,
            }
        return {
            "viewpoint": data["viewpoint"] or None,
            "as_of": data["as_of"],
            "limit": data["limit"],
            "before_turn": data["before_turn"],
            "actor_character_id": data["actor_character_id"] or None,
            "variant": data["variant"] or None,
            "include_audit": bool(data["include_audit"]),
            "include_summary": bool(data["include_summary"]),
            "player_view": data["player_view"] or None,
        }

    def value(self, name: str) -> str:
        """The submitted (raw) value of a control, for links that keep it."""
        found = self.data.get(name, "")
        return found if isinstance(found, str) else ""

    @property
    def show_traces(self) -> bool:
        """ "Show all traces" is ticked (valid form only)."""
        return bool(self.cleaned_data.get("traces"))

    @property
    def omniscient(self) -> bool:
        return self.value("viewpoint") == OMNISCIENT

    @property
    def as_recorded(self) -> bool:
        """View is "As originally recorded" (#304); the raw value, so the unvalidated HTMX
        re-render can read it."""
        return self.value("view") == AS_RECORDED

    @property
    def carried(self) -> list[tuple[str, str]]:
        """The hidden inputs that carry each disabled as-recorded control's submitted value
        (#304), only when it is one of that control's choices: switching back restores it."""
        found = []
        for name in CARRIED:
            value = self.value(name)
            field = self.fields[name]
            if isinstance(field, forms.BooleanField):
                if value and value.lower() not in {"0", "false", "off"}:
                    found.append((name, "1"))
            elif isinstance(field, forms.ChoiceField) and value and field.valid_value(value):
                found.append((name, value))
        return found

    @property
    def player_view_kept(self) -> str:
        """The submitted player view when it is one of the choices, else "" (the hidden input
        that carries it while the select is disabled)."""
        value = self.value("player_view")
        return value if value in {key for key, _ in PLAYER_VIEW_CHOICES if key} else ""


def as_of_options(last: int) -> list[int]:
    """The as-of datalist for turns 0…``last``: every turn up to 500; above that, 0, every
    50th turn and the last 50 turns."""
    if last < 0:
        return [0]
    if last <= DATALIST_ALL_UP_TO:
        return list(range(last + 1))
    sampled = set(range(0, last + 1, DATALIST_STEP))
    sampled.update(range(last - DATALIST_TAIL + 1, last + 1))
    return sorted(sampled)
