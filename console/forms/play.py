"""The turn form (#48): one ``POST /stories/{id}/respond`` request, from the play view.

It copies only the bounds the recorded schema states (``RespondRequest``, #43): say and do up to
8,000 characters (at least one given), length ``short``/``medium``/``long`` or 1-4,000,
temperature 0-2, top_p 0-1, up to 20 recent messages of up to 2,000 characters, max calls 1-50
and a deadline of 0.5-600 s. Everything else is the Engine's 422, placed beside the field
(:data:`ERROR_PATHS`). Empty optional fields are left out of the request.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from console.cast import CastMember, character_choices, character_labels, viewpoint_for

INPUT_MAX = 8_000
LENGTH_MIN, LENGTH_MAX = 1, 4_000
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 2.0
TOP_P_MIN, TOP_P_MAX = 0.0, 1.0
RECENT_MAX = 20
RECENT_TEXT_MAX = 2_000
MAX_CALLS_MIN, MAX_CALLS_MAX = 1, 50
DEADLINE_MIN, DEADLINE_MAX = 0.5, 600.0
STORY_DEFAULT = "default"  # the viewpoint select's "story default" value
CUSTOM = "custom"
LENGTH_CHOICES = [
    ("", "Engine default"),
    ("short", "short"),
    ("medium", "medium"),
    ("long", "long"),
    (CUSTOM, "number (below)"),
]
NEED_INPUT = "Say or do something: at least one is required."

# Where an Engine 422 about the request body goes (``apply_engine_errors`` aliases).
ERROR_PATHS = {
    "actor_character_id": "actor",
    "unknown_actor": "actor",
    "player_input": "say",
    "player_input.say": "say",
    "player_input.do": "do",
    "output_viewpoint": "output_viewpoint",
    "viewpoint_unresolved": "output_viewpoint",
    "query.viewpoint": "output_viewpoint",
    "render_character_ids": "render_character_ids",
    "unknown_render_ids": "render_character_ids",
    "prompt_params": "length",
    "prompt_params.length": "length_custom",
    "prompt_params.temperature": "temperature",
    "prompt_params.top_p": "top_p",
    "scene_context": "recent_messages",
    "scene_context.characters_present": "characters_present",
    "scene_context.recent_messages": "recent_messages",
    "budget": "max_calls",
    "budget.max_calls": "max_calls",
    "budget.deadline_s": "deadline_s",
}


@dataclass(frozen=True)
class RecentMessage:
    text: str
    character_id: str | None = None

    def body(self) -> dict[str, str]:
        if self.character_id is None:
            return {"text": self.text}
        return {"character_id": self.character_id, "text": self.text}


def parse_recent_messages(text: str, cast: Sequence[CastMember]) -> list[RecentMessage]:
    """One message per non-blank line, ``Name: text``. A name matching a cast member's
    (case-insensitive, trimmed) sends that character's id with the rest of the line; anything
    else (no colon, unknown name) sends the whole line as text."""
    by_name: dict[str, str] = {}
    for member in cast:
        by_name.setdefault(member.name.strip().casefold(), member.id)
    found = []
    for line in text.splitlines():
        if not line.strip():
            continue
        name, colon, rest = line.partition(":")
        character = by_name.get(name.strip().casefold()) if colon else None
        if character is not None:
            found.append(RecentMessage(rest.strip(), character))
        else:
            found.append(RecentMessage(line.strip()))
    return found


class TurnForm(forms.Form):
    """One turn. ``cast`` sets the character selects; ``default_viewpoint`` (the story's
    ``default_output_viewpoint``, when it has one) adds the "story default" option."""

    actor = forms.ChoiceField(label="Actor")
    say = forms.CharField(
        label="Say", max_length=INPUT_MAX, required=False, widget=forms.Textarea(attrs={"rows": 3})
    )
    do = forms.CharField(
        label="Do", max_length=INPUT_MAX, required=False, widget=forms.Textarea(attrs={"rows": 2})
    )
    output_viewpoint = forms.ChoiceField(label="Output viewpoint")
    render_character_ids = forms.MultipleChoiceField(
        label="Render characters",
        required=False,
        help_text="blank = everyone except the actor",
        widget=forms.SelectMultiple(attrs={"size": 3}),
    )
    length = forms.ChoiceField(label="Length", choices=LENGTH_CHOICES, required=False)
    length_custom = forms.IntegerField(
        label="Length (number)",
        required=False,
        validators=[MinValueValidator(LENGTH_MIN), MaxValueValidator(LENGTH_MAX)],
    )
    temperature = forms.FloatField(
        label="Temperature",
        required=False,
        validators=[MinValueValidator(TEMPERATURE_MIN), MaxValueValidator(TEMPERATURE_MAX)],
    )
    top_p = forms.FloatField(
        label="top_p",
        required=False,
        validators=[MinValueValidator(TOP_P_MIN), MaxValueValidator(TOP_P_MAX)],
    )
    characters_present = forms.MultipleChoiceField(
        label="Characters present",
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 3}),
    )
    recent_messages = forms.CharField(
        label="Recent messages",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=f"Up to {RECENT_MAX} lines, each `Name: text`.",
    )
    max_calls = forms.IntegerField(
        label="Max calls",
        required=False,
        validators=[MinValueValidator(MAX_CALLS_MIN), MaxValueValidator(MAX_CALLS_MAX)],
    )
    deadline_s = forms.FloatField(
        label="Deadline (seconds)",
        required=False,
        validators=[MinValueValidator(DEADLINE_MIN), MaxValueValidator(DEADLINE_MAX)],
    )

    def __init__(
        self,
        data: Any = None,
        *,
        cast: Sequence[CastMember],
        default_viewpoint: str | None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.cast = list(cast)
        self.default_viewpoint = default_viewpoint
        characters = character_choices(self.cast)
        labels = character_labels(self.cast)
        viewpoints = [(viewpoint_for(cid), label) for cid, label in characters]
        if default_viewpoint:
            shown = next(
                (labels[c.id] for c in self.cast if viewpoint_for(c.id) == default_viewpoint),
                default_viewpoint,
            )
            viewpoints.insert(0, (STORY_DEFAULT, f"Story default ({shown})"))
        self._set_choices("actor", characters)
        self._set_choices("output_viewpoint", viewpoints)
        self._set_choices("render_character_ids", characters)
        self._set_choices("characters_present", characters)

    def _set_choices(self, name: str, choices: list[tuple[str, str]]) -> None:
        field = self.fields[name]
        if isinstance(field, forms.ChoiceField):
            field.choices = choices

    def clean_recent_messages(self) -> list[RecentMessage]:
        text: str = self.cleaned_data.get("recent_messages") or ""
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > RECENT_MAX:
            raise forms.ValidationError(f"At most {RECENT_MAX} recent messages (got {len(lines)}).")
        for number, line in enumerate(lines, start=1):
            if len(line) > RECENT_TEXT_MAX:
                raise forms.ValidationError(
                    f"Line {number} is longer than {RECENT_TEXT_MAX} characters."
                )
        return parse_recent_messages(text, self.cast)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        given = (data.get("say") or "").strip() or (data.get("do") or "").strip()
        if not given and "say" not in self.errors and "do" not in self.errors:
            raise forms.ValidationError(NEED_INPUT)
        custom = data.get("length") == CUSTOM and data.get("length_custom") is None
        if custom and "length_custom" not in self.errors:
            self.add_error("length_custom", "Give a number when the length is custom.")
        return data

    def resolved_viewpoint(self) -> str:
        """The ``output_viewpoint`` sent: the story default made explicit, else the chosen
        character's (always sent, so the Engine never falls back to the actor silently)."""
        chosen: str = self.cleaned_data["output_viewpoint"]
        if chosen == STORY_DEFAULT and self.default_viewpoint:
            return self.default_viewpoint
        return chosen

    def body(self) -> dict[str, Any]:
        """The ``RespondRequest`` body; call after ``is_valid()``."""
        data = self.cleaned_data
        player_input = {k: data[k] for k in ("say", "do") if data[k]}
        body: dict[str, Any] = {
            "actor_character_id": data["actor"],
            "player_input": player_input,
            "output_viewpoint": self.resolved_viewpoint(),
        }
        if data["render_character_ids"]:
            body["render_character_ids"] = list(data["render_character_ids"])
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
        scene: dict[str, Any] = {}
        if data["characters_present"]:
            scene["characters_present"] = list(data["characters_present"])
        if data["recent_messages"]:
            scene["recent_messages"] = [m.body() for m in data["recent_messages"]]
        if scene:
            body["scene_context"] = scene
        budget = {k: data[k] for k in ("max_calls", "deadline_s") if data[k] is not None}
        if budget:
            body["budget"] = budget
        return body

    def kept_values(self, *, clear_input: bool) -> dict[str, Any]:
        """Initial values for the next render: everything as posted, say and do cleared after a
        finished turn. Call after ``is_valid()`` succeeded."""
        data = self.cleaned_data
        kept = {name: data.get(name) for name in self.fields if name != "recent_messages"}
        kept["recent_messages"] = self.data.get("recent_messages", "")
        if clear_input:
            kept["say"] = ""
            kept["do"] = ""
        return kept
