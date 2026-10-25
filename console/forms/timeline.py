"""The timeline operation forms (#53): what a confirm page or the fork form posts back.

Every one carries the page's ``rev`` (the timeline revision the operator was looking at, sent
as ``X-Epistrel-Expected-Revision``), ``head`` and the display-only ``name`` as hidden fields
copied from the link's query string. A missing or non-integer ``rev`` is a form error, and the
operation is not sent. Bounds copied from the recorded schema: ``RegenerateRequest`` (length,
temperature, top_p as #48's turn form; up to 64 render characters; viewpoint up to 80
characters), ``ForkRequest.name`` up to 200 characters, chains ``{turn ≥ 1, variant ≥ 1}``.
"""

import re
from collections.abc import Sequence
from typing import Any

from django import forms

from console.cast import CastMember, character_choices, viewpoint_for
from console.forms.play import CUSTOM, TurnForm

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
}
FORK_ERRORS = {"name": "new_name", "variant_chain": "variant_chain"}
_CHAIN = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


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


class RegenerateForm(TimelineForm):
    """Regenerate's optional overrides; blank keeps what the turn was played with."""

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
        return body


class ForkForm(TimelineForm):
    """Fork: the new story's name (pre-filled, required) and, from a Variants row, the chain."""

    new_name = forms.CharField(label="New story's name", max_length=NAME_MAX)
    variant_chain = ChainField(widget=forms.HiddenInput, required=False)

    HIDDEN = ("rev", "head", "name", "variant_chain")


def default_fork_name(name: str, story_id: str, turn: int) -> str:
    """``"<name> (fork at turn T)"``, cut to the 200 characters the Engine accepts."""
    return f"{name or story_id} (fork at turn {turn})"[:NAME_MAX]
