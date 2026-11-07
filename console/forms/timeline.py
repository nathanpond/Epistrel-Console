"""The timeline operation forms (#53): what a confirm page or the fork form posts back.

Every one carries the page's ``rev`` (the timeline revision the operator was looking at, sent
as ``X-Epistrel-Expected-Revision``), ``head`` and the display-only ``name`` as hidden fields
copied from the link's query string. A missing or non-integer ``rev`` is a form error, and the
operation is not sent. Bounds copied from the recorded schema: ``RegenerateRequest`` (length,
temperature, top_p as #48's turn form; up to 64 render characters; viewpoint up to 80
characters), ``ForkRequest.name`` up to 200 characters, chains ``{turn ≥ 1, variant ≥ 1}``,
``TruncateRequest.slot``/``ForkRequest.slot`` 0 to 32767 (#209: keep or copy turn N's inter-turn
slots 1..k; blank sends nothing).

#301 (FR-COMMIT-12): :class:`RegenerateForm` mixes in E4's :class:`RewriteModesForm` and adds the
optional ``directive`` (the Direction box, at most :data:`DIRECTIVE_MAX` code points, no HTML
``maxlength``, C10). Its body gains ``directive``, ``preview`` and ``resolution_mode`` only with a
direction, and only the modes that differ from the Engine's defaults (``preview_first``,
``synchronous``): a regenerate without a direction is byte for byte #53's.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from django import forms
from django.core import signing

from console.cast import CastMember, character_choices, viewpoint_for
from console.forms.play import CUSTOM, TurnForm
from console.forms.rewrites import (
    APPLY_DIRECTLY,
    RESOLUTION_CHOICES,
    RESOLUTION_LABEL,
    SYNCHRONOUS,
    RewriteModesForm,
)
from console.world import SLOTS_NOT_LISTED

REVISION_MISSING = "This page carries no timeline revision — reload the story and try again."
CHAIN_INVALID = "A chain is turn:variant, both at least 1 (e.g. 5:2)."
NAME_MAX = 200
RENDER_MAX = 64
KEEP_RECORDED = ""
# Where an Engine 422 about a regenerate body goes (``apply_engine_errors`` aliases).
REGENERATE_ERRORS = {
    "output_viewpoint": "output_viewpoint",
    "query.viewpoint": "output_viewpoint",
    "viewpoint_unresolved": "output_viewpoint",
    "render_character_ids": "render_character_ids",
    "unknown_render_ids": "render_character_ids",
    "prompt_params": "length",
    "prompt_params.length": "length_custom",
    "prompt_params.temperature": "temperature",
    "prompt_params.top_p": "top_p",
    # #301: the direction and E4's modes (E21's ``body.<member>`` locs; the helper strips
    # ``body.``, so both spellings are listed).
    "directive": "directive",
    "directive.text": "directive",
    "body.directive": "directive",
    "body.directive.text": "directive",
    "preview": "apply_directly",
    "resolution_mode": "resolution",
}
DIRECTIVE_MAX = 2000  # RegenerateRequest.directive.text's bound
DIRECTIVE_LABEL = "Direction (optional)"
DIRECTIVE_HELP = (
    "Steer the new reply, e.g. “the knocker is George”. If recorded history disagrees, the "
    "reply is held in a rewrite session until you apply it"
)
DIRECTIVE_TOO_LONG = "At most 2,000 characters"
# Where an Engine 422 about a correction's input goes (#263); any other field is a refusal.
CORRECT_ERRORS = {
    "player_input": "__all__",
    "player_input.say": "say",
    "player_input.do": "do",
}
FORK_ERRORS = {"name": "new_name", "variant_chain": "variant_chain", "slot": "slot"}
REWIND_ERRORS = {"slot": "slot"}
SLOT_MAX = 32767  # the recorded schema's bound on TruncateRequest.slot / ForkRequest.slot
SLOT_INVALID = "Enter a whole number of slots (0 or more), or leave it blank"
SLOT_TOO_HIGH = "At most 32,767."
SLOT_LABEL = "Inter-turn slot (optional)"
_CHAIN = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")
_DIGITS = re.compile(r"[0-9]+")


def parse_chain(value: str) -> tuple[int, int] | None:
    """``"T:V"`` → ``(T, V)``, or ``None`` unless both are integers of at least 1."""
    match = _CHAIN.match(value or "")
    if match is None:
        return None
    turn, variant = int(match.group(1)), int(match.group(2))
    return (turn, variant) if turn >= 1 and variant >= 1 else None


class ChainField(forms.CharField):
    """A ``T:V`` chain, cleaned to ``(turn, variant)`` (``None`` when blank and optional)."""

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return None
        found = parse_chain(text)
        if found is None:
            raise forms.ValidationError(CHAIN_INVALID)
        return found


class SlotField(forms.CharField):
    """An inter-turn slot k (#209): blank → ``None`` (nothing sent), ASCII digits → ``int`` up to
    :data:`SLOT_MAX`. No HTML ``min``/``max`` (C10): the Console's own messages say why."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("label", SLOT_LABEL)
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "numeric", "size": 6}))
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return None
        if _DIGITS.fullmatch(text) is None:
            raise forms.ValidationError(SLOT_INVALID, code="slot_invalid")
        # Python refuses ``int()`` on very long strings; anything past five digits is too high.
        if len(text.lstrip("0")) > 5 or int(text) > SLOT_MAX:
            raise forms.ValidationError(SLOT_TOO_HIGH, code="slot_too_high")
        return int(text)


def slot_help(verb: str, turn: int) -> str:
    """The slot field's help: what k keeps (``"Keep"``) or copies (``"Copy"``), then D18."""
    return (
        f"{verb} turn {turn}'s inter-turn slots 1..k — State sets, clock advances and "
        f"settlements recorded after turn {turn}. Blank: none of them. {SLOTS_NOT_LISTED}."
    )


class TimelineForm(forms.Form):
    """The hidden fields every timeline operation posts back."""

    rev = forms.IntegerField(
        widget=forms.HiddenInput,
        min_value=0,
        error_messages={"required": REVISION_MISSING, "invalid": REVISION_MISSING},
    )
    head = forms.IntegerField(widget=forms.HiddenInput, required=False, min_value=0)
    name = forms.CharField(widget=forms.HiddenInput, required=False)

    HIDDEN: tuple[str, ...] = ("rev", "head", "name")

    def hidden(self) -> dict[str, str]:
        """The hidden fields' values as the page received them (re-posted unchanged)."""
        return {name: str(self.data.get(name, "")) for name in self.HIDDEN}

    def value(self, name: str) -> Any:
        """A cleaned value, or ``None`` when the form or that field did not validate."""
        if not hasattr(self, "cleaned_data"):
            self.is_valid()
        return self.cleaned_data.get(name)

    @property
    def shown_name(self) -> str:
        return str(self.data.get("name", ""))

    def hidden_errors(self) -> list[str]:
        """Errors on the hidden fields, shown above the Confirm button."""
        return [str(e) for name in self.HIDDEN for e in self.errors.get(name, [])]


class SwapForm(TimelineForm):
    chain = ChainField(widget=forms.HiddenInput, error_messages={"required": CHAIN_INVALID})
    check = forms.CharField(widget=forms.HiddenInput, required=False)

    HIDDEN = ("rev", "head", "name", "chain", "check")


class RegenerateForm(TimelineForm, RewriteModesForm):
    """Regenerate's optional overrides; blank keeps what the turn was played with. #301 adds the
    optional direction and E4's modes (sent only with a direction)."""

    snapshot = forms.CharField(widget=forms.HiddenInput, required=False)
    length = TurnForm.base_fields["length"]
    length_custom = TurnForm.base_fields["length_custom"]
    temperature = TurnForm.base_fields["temperature"]
    top_p = TurnForm.base_fields["top_p"]
    output_viewpoint = forms.ChoiceField(label="Output viewpoint", required=False)
    render_character_ids = forms.MultipleChoiceField(
        label="Render characters",
        required=False,
        help_text="none selected = keep recorded",
        widget=forms.SelectMultiple(attrs={"size": 3}),
    )
    directive = forms.CharField(
        label=DIRECTIVE_LABEL,
        required=False,
        max_length=DIRECTIVE_MAX,
        help_text=DIRECTIVE_HELP,
        error_messages={"max_length": DIRECTIVE_TOO_LONG},
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    # E4's resolution, optional here: Play's Regenerate link is a GET with no modes, and blank
    # means ``synchronous``. A value outside the choices is still a field error.
    resolution = forms.ChoiceField(
        choices=RESOLUTION_CHOICES, required=False, initial=SYNCHRONOUS, label=RESOLUTION_LABEL
    )

    HIDDEN = ("rev", "head", "name", "snapshot")
    PARAMS = (
        "length",
        "length_custom",
        "temperature",
        "top_p",
        "output_viewpoint",
        "render_character_ids",
    )

    def __init__(self, data: Any = None, *, cast: Sequence[CastMember], **kwargs: Any) -> None:
        super().__init__(data, **kwargs)
        characters = character_choices(list(cast))
        viewpoint = self.fields["output_viewpoint"]
        render = self.fields["render_character_ids"]
        assert isinstance(viewpoint, forms.ChoiceField)  # noqa: S101 - declared above
        assert isinstance(render, forms.MultipleChoiceField)  # noqa: S101 - declared above
        viewpoint.choices = [(KEEP_RECORDED, "Keep recorded")] + [
            (viewpoint_for(cid), label) for cid, label in characters
        ]
        render.choices = characters
        self.fields["directive"].widget.attrs.pop("maxlength", None)  # C10: the server says why

    @property
    def param_fields(self) -> list[forms.BoundField]:
        """The Parameters ``<details>``'s fields, in :data:`PARAMS` order."""
        return [self[name] for name in self.PARAMS]

    def clean_resolution(self) -> str:
        return str(self.cleaned_data.get("resolution") or SYNCHRONOUS)

    def clean_render_character_ids(self) -> list[str]:
        chosen: list[str] = self.cleaned_data.get("render_character_ids") or []
        if len(chosen) > RENDER_MAX:
            raise forms.ValidationError(f"At most {RENDER_MAX} render characters.")
        return chosen

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        custom = data.get("length") == CUSTOM and data.get("length_custom") is None
        if custom and "length_custom" not in self.errors:
            self.add_error("length_custom", "Give a number when the length is custom.")
        return data

    def has_overrides(self) -> bool:
        """Whether any parameter was given (the ``<details>`` opens to show them)."""
        return any(self.data.get(name) for name in self.PARAMS)

    def has_mode_overrides(self) -> bool:
        """Whether Apply directly or a non-default resolution was posted (the modes'
        ``<details>`` opens to show them)."""
        resolution = self.data.get("resolution")
        return bool(self.data.get("apply_directly")) or resolution not in (None, "", SYNCHRONOUS)

    def posted_direction(self) -> bool:
        """Whether the bound data holds a non-blank direction, valid or not."""
        return self.is_bound and bool(str(self.data.get("directive") or "").strip())

    def direction(self) -> str:
        """The cleaned direction ("" for none). Call after ``is_valid()``."""
        return str(self.cleaned_data.get("directive") or "")

    def body(self) -> dict[str, Any]:
        """The ``RegenerateRequest`` body: ``turn`` (the head the page showed, so a stale page
        gets the Engine's 422) and only the overrides given. Call after ``is_valid()``."""
        data = self.cleaned_data
        body: dict[str, Any] = {}
        if data.get("head") is not None:
            body["turn"] = data["head"]
        prompt: dict[str, Any] = {}
        if data["length"] == CUSTOM:
            prompt["length"] = data["length_custom"]
        elif data["length"]:
            prompt["length"] = data["length"]
        for name in ("temperature", "top_p"):
            if data[name] is not None:
                prompt[name] = data[name]
        if prompt:
            body["prompt_params"] = prompt
        if data["output_viewpoint"]:
            body["output_viewpoint"] = data["output_viewpoint"]
        if data["render_character_ids"]:
            body["render_character_ids"] = list(data["render_character_ids"])
        direction = self.direction()
        if direction:  # #301: the modes ride only with a direction, and only non-defaults
            body["directive"] = {"text": direction}
            modes = self.modes()
            if modes["preview"] == APPLY_DIRECTLY:
                body["preview"] = APPLY_DIRECTLY
            if modes["resolution_mode"] != SYNCHRONOUS:
                body["resolution_mode"] = modes["resolution_mode"]
        return body


class RewindForm(TimelineForm):
    """Rewind: the hidden fields plus the optional inter-turn slot k (#209)."""

    slot = SlotField()

    def __init__(self, data: Any = None, *, turn: int, **kwargs: Any) -> None:
        super().__init__(data, **kwargs)
        self.fields["slot"].help_text = slot_help("Keep", turn)


class ForkForm(TimelineForm):
    """Fork: the new story's name (pre-filled, required), from a Variants row the chain, and the
    optional inter-turn slot k (#209)."""

    new_name = forms.CharField(label="New story's name", max_length=NAME_MAX)
    variant_chain = ChainField(widget=forms.HiddenInput, required=False)
    slot = SlotField()

    HIDDEN = ("rev", "head", "name", "variant_chain")

    def __init__(self, data: Any = None, *, turn: int, **kwargs: Any) -> None:
        super().__init__(data, **kwargs)
        self.fields["slot"].help_text = slot_help("Copy", turn)


def default_fork_name(name: str, story_id: str, turn: int) -> str:
    """``"<name> (fork at turn T)"``, cut to the 200 characters the Engine accepts."""
    return f"{name or story_id} (fork at turn {turn})"[:NAME_MAX]


# --- Correct (#263) -------------------------------------------------------------------------------

INPUT_MAX = 8000  # PlayerInputBody's say/do bound
ORIGINAL_SALT = "console.correct.original"
NOTHING_TO_SEND = "Give a say or a do — whitespace alone counts as none."
UNCHANGED_INPUT = "That's the turn's input unchanged — edit it to correct the turn."
WHISPER_HELP = "A whisper isn't carried over"
CHANNELS = ("say", "do")


def sign_original(story_id: str, turn: int, say: str | None, do: str | None) -> str:
    """The turn's own input as the page pre-filled it, signed for the hidden ``original``
    (``None``: the channel had no readable item)."""
    return signing.dumps(
        {"story": story_id, "turn": turn, "say": say, "do": do}, salt=ORIGINAL_SALT
    )


def _compared(text: Any) -> str:
    """How the Engine compares an input: NFC-normalised and stripped ("" for absent)."""
    return unicodedata.normalize("NFC", text).strip() if isinstance(text, str) else ""


class CorrectForm(TimelineForm):
    """A correction's replacement input (#263): the turn's ``say`` and ``do``, pre-filled from
    the history read. The original travels signed in ``original`` so an untouched form sends
    nothing (C9); a tampered, missing or other story's/turn's value skips that check."""

    snapshot = forms.CharField(widget=forms.HiddenInput, required=False)
    original = forms.CharField(widget=forms.HiddenInput, required=False)
    say = forms.CharField(
        label="Say",
        required=False,
        max_length=INPUT_MAX,
        help_text=WHISPER_HELP,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    do = forms.CharField(
        label="Do",
        required=False,
        max_length=INPUT_MAX,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    HIDDEN = ("rev", "head", "name", "snapshot", "original")

    def __init__(self, data: Any = None, *, story_id: str, turn: int, **kwargs: Any) -> None:
        super().__init__(data, **kwargs)
        self.story_id = story_id
        self.turn = turn
        for name in CHANNELS:  # no maxlength attribute: the server says why (C10)
            self.fields[name].widget.attrs.pop("maxlength", None)

    def hidden(self) -> dict[str, str]:
        """The hidden fields as posted, or on an unbound (GET) form as its ``initial``."""
        if self.is_bound:
            return super().hidden()
        return {
            name: "" if self.initial.get(name) is None else str(self.initial[name])
            for name in self.HIDDEN
        }

    def original_values(self) -> Mapping[str, Any] | None:
        """The signed original ``{say, do}``, or ``None`` when missing, tampered or another
        story's or turn's."""
        raw = self.data.get("original") if self.is_bound else self.initial.get("original")
        if not raw:
            return None
        try:
            found = signing.loads(str(raw), salt=ORIGINAL_SALT)
        except signing.BadSignature:
            return None
        if not isinstance(found, dict):
            return None
        if found.get("story") != self.story_id or found.get("turn") != self.turn:
            return None
        return found

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if any(name in self.errors for name in CHANNELS):
            return data
        say, do = data.get("say") or "", data.get("do") or ""
        if not say.strip() and not do.strip():
            raise forms.ValidationError(NOTHING_TO_SEND, code="nothing_to_send")
        original = self.original_values()
        if original is not None and all(
            _compared(data.get(name)) == _compared(original.get(name)) for name in CHANNELS
        ):
            raise forms.ValidationError(UNCHANGED_INPUT, code="unchanged_input")
        return data

    def body(self) -> dict[str, Any]:
        """The ``CorrectRequest`` body: the URL's turn and only the non-blank say/do (stripped);
        nothing else, so the Engine reuses the turn's recorded viewpoint, render characters and
        prompt parameters. Call after ``is_valid()``."""
        given = {name: self.cleaned_data[name].strip() for name in CHANNELS}
        return {
            "turn": self.turn,
            "player_input": {name: text for name, text in given.items() if text},
        }
