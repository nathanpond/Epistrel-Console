"""The turn form (#48): one ``POST /stories/{id}/respond`` request, from the play view.

It copies only the bounds the recorded schema states (``RespondRequest``, #43): say and do up to
8,000 characters (at least one given), length ``short``/``medium``/``long`` or 1-4,000,
temperature 0-2, top_p 0-1, up to 20 recent messages of up to 2,000 characters, max calls 1-50
and a deadline of 0.5-600 s. Everything else is the Engine's 422, placed beside the field
(:data:`ERROR_PATHS`). Empty optional fields are left out of the request.

#200 adds the multi-character section (``interaction_mode``, ``sequence_order``,
``simultaneous_tie_break``, ``reactive_interjections``, ``order_<cid>``, ``role_<cid>``; enabled
only under per-character renders), Player view (``player_view``, perception ``co_location`` or
above) and Whisper to (``whisper_to``, perception ``full``). :meth:`TurnForm.body` sends none of
them while its gate is closed, whatever the POST carries (:mod:`console.multi_turn`).
:data:`REASON_WORDING` words the Engine's ``details.reason`` refusals before the generic
placement.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from console import multi_turn
from console.cast import CastMember, character_choices, character_labels, viewpoint_for
from console.engine.client import EngineError
from console.inspect import Unavailable
from console.multi_turn import MAX_PRIMARIES, MultiGate, first_choice
from console.participation import ROLE_LABELS, ROLES, role_text
from console.story_settings import SETTING_CHOICES

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
    "interaction_mode": "interaction_mode",
    "sequence_order": "sequence_order",
    "simultaneous_tie_break": "simultaneous_tie_break",
    "reactive_interjections": "reactive_interjections",
    "player_view": "player_view",
    "player_input.whisper_to": "whisper_to",
}

# #200: the section's own selects (``order_<cid>`` / ``role_<cid>`` are per cast member).
SECTION_SELECTS = (
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "reactive_interjections",
)
ORDER_PREFIX, ROLE_PREFIX = "order_", "role_"
INTERJECTION_CHOICES = [("on", "On"), ("off", "Off")]
PLAYER_VIEW_CHOICES = [
    ("character", "Character (audience-filtered)"),
    ("story", "Story (every beat)"),
]
ORDER_CHOICES = [("", "—"), *((str(n), str(n)) for n in range(1, MAX_PRIMARIES + 1))]
RENDER_FIELD = "render_character_ids"
TIE_BREAK = "simultaneous_tie_break"
WHISPER = "whisper_to"
MULTI_SECTION = "multi_character"  # REASON_WORDING's target for the section-level error
# An Engine ``details.reason`` → where its sentence goes and the sentence (``None``: built from
# the form, :meth:`TurnForm.reason_sentence`). Consulted before ``apply_engine_errors``.
REASON_WORDING: dict[str, tuple[str, str | None]] = {
    "one_render_character": (RENDER_FIELD, multi_turn.ONE_RENDER),
    "player_cannot_render": (RENDER_FIELD, None),
    "too_many_primaries": (RENDER_FIELD, multi_turn.too_many()),
    "per_character_only": (MULTI_SECTION, multi_turn.RELOAD),
    "not_yet": (TIE_BREAK, "State tie-breaks arrive in Engine M6"),  # #197's NOT_YET_TEXT
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
        gate: MultiGate | None = None,
        story_multi: Mapping[str, Any] | None = None,
        roles: Mapping[str, tuple[str, bool]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.cast = list(cast)
        self.default_viewpoint = default_viewpoint
        self.gate = gate if gate is not None else multi_turn.closed()
        self.story_multi = story_multi
        self.stored_roles = dict(roles or {})
        self.multi_error = ""  # the section-level error (#200: per_character_only, module_off)
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
        self._multi_fields(characters, labels)

    @property
    def non_players(self) -> list[CastMember]:
        return [c for c in self.cast if not c.is_player]

    @property
    def section_names(self) -> list[str]:
        """The fields the section renders itself (the generic field loop skips them)."""
        rows = [
            f"{prefix}{c.id}" for c in self.non_players for prefix in (ORDER_PREFIX, ROLE_PREFIX)
        ]
        return [*SECTION_SELECTS, *rows]

    def section_rows(self) -> list[tuple[str, forms.BoundField, forms.BoundField]]:
        """The section's table: ``(character label, its order field, its role field)``."""
        labels = character_labels(self.cast)
        return [
            (labels[c.id], self[f"{ORDER_PREFIX}{c.id}"], self[f"{ROLE_PREFIX}{c.id}"])
            for c in self.non_players
        ]

    def section_fields(self) -> list[forms.BoundField]:
        return [self[name] for name in SECTION_SELECTS]

    def _multi_fields(self, characters: list[tuple[str, str]], labels: dict[str, str]) -> None:
        """#200's fields: the section's selects and per-character rows, Player view after
        Output viewpoint and Whisper to after Say."""
        multi = self.story_multi
        selects: dict[str, forms.Field] = {}
        for name, label in (
            ("interaction_mode", "Interaction mode"),
            ("sequence_order", "Sequence order"),
            (TIE_BREAK, "Tie-break"),
        ):
            selects[name] = forms.ChoiceField(
                label=label,
                required=False,
                choices=[first_choice(name, multi), *SETTING_CHOICES[name]],
            )
        selects["reactive_interjections"] = forms.ChoiceField(
            label="Reactive interjections",
            required=False,
            choices=[first_choice("reactive_interjections", multi), *INTERJECTION_CHOICES],
        )
        for c in self.non_players:
            order_label = f"Render order: {labels[c.id]}"
            selects[f"{ORDER_PREFIX}{c.id}"] = forms.ChoiceField(
                label=order_label,
                required=False,
                choices=ORDER_CHOICES,
                widget=forms.Select(attrs={"aria-label": order_label}),
            )
            stored = self.stored_roles.get(c.id)
            first = f"Stored: {role_text(*stored)}" if stored else "Stored: —"
            role_label = f"Role this turn: {labels[c.id]}"
            selects[f"{ROLE_PREFIX}{c.id}"] = forms.ChoiceField(
                label=role_label,
                required=False,
                choices=[("", first), *((r, ROLE_LABELS[r]) for r in ROLES)],
                widget=forms.Select(attrs={"aria-label": role_label}),
            )
        player_view = forms.ChoiceField(
            label="Player view",
            required=False,
            choices=[first_choice("player_view", multi), *PLAYER_VIEW_CHOICES],
        )
        whisper = forms.MultipleChoiceField(
            label="Whisper to",
            required=False,
            choices=characters,
            widget=forms.SelectMultiple(attrs={"size": 3}),
        )
        _grey(player_view, self.gate.player_view_reason, "gate-player-view")
        _grey(whisper, self.gate.whisper_reason, "gate-whisper-to")
        if self.gate.enabled:
            render = self.fields[RENDER_FIELD]
            render.help_text = f"{render.help_text} {multi_turn.RENDER_HELP}"
        ordered: dict[str, forms.Field] = {}
        for name, found in self.fields.items():
            ordered[name] = found
            if name == "say":
                ordered[WHISPER] = whisper
            elif name == "output_viewpoint":
                ordered["player_view"] = player_view
        ordered.update(selects)
        self.fields = ordered

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
        if not self.gate.whisper_enabled:
            data[WHISPER] = []  # dropped silently, before the "needs something to say" check
        picked = set(data.get(RENDER_FIELD) or ())
        if self.gate.enabled and len(picked) > MAX_PRIMARIES:
            self.add_error(RENDER_FIELD, multi_turn.too_many(len(picked)))
        if data.get(WHISPER) and not (data.get("say") or "").strip() and "say" not in self.errors:
            self.add_error(WHISPER, multi_turn.WHISPER_NEEDS_SAY)
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
        if data[WHISPER]:
            player_input[WHISPER] = list(data[WHISPER])
        if data["render_character_ids"]:
            body["render_character_ids"] = self._render_order(list(data["render_character_ids"]))
        body.update(self._section_body())
        if self.gate.player_view_enabled and data.get("player_view"):
            body["player_view"] = data["player_view"]
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
            kept[WHISPER] = []
        return kept

    def _render_order(self, picked: list[str]) -> list[str]:
        """The render characters as sent: under the open section, the numbered picks first
        (ascending), then the unnumbered ones; ties and unnumbered picks in cast order (the
        player has no order select, so it is unnumbered). A greyed section keeps the posted
        order."""
        if not self.gate.enabled:
            return picked
        position = {c.id: index for index, c in enumerate(self.cast)}
        unique = list(dict.fromkeys(picked))

        def key(cid: str) -> tuple[int, int]:
            number = self.cleaned_data.get(f"{ORDER_PREFIX}{cid}")
            rank = int(number) if number else MAX_PRIMARIES + 1
            return rank, position.get(cid, len(position))

        return sorted(unique, key=key)

    def _section_body(self) -> dict[str, Any]:
        """The section's keys, only while it is enabled and only those explicitly chosen."""
        if not self.gate.enabled:
            return {}
        data = self.cleaned_data
        found: dict[str, Any] = {}
        for name in SECTION_SELECTS[:3]:
            if data.get(name):
                found[name] = data[name]
        roles = {
            c.id: data[f"{ROLE_PREFIX}{c.id}"]
            for c in self.non_players
            if data.get(f"{ROLE_PREFIX}{c.id}")
        }
        if roles:
            found["roles"] = roles
        interjections = data.get("reactive_interjections")
        if interjections:
            found["reactive_interjections"] = interjections == "on"
        return found

    def role_aliases(self) -> dict[str, str]:
        """``roles.<cid>`` → ``role_<cid>`` for every role select (a 422 on a role)."""
        return {f"roles.{c.id}": f"{ROLE_PREFIX}{c.id}" for c in self.non_players}

    def place_reason(self, error: EngineError) -> bool:
        """Place a 422 whose ``details.reason`` :data:`REASON_WORDING` knows; ``True`` when
        placed (the Engine's message is then not placed as well)."""
        reason = error.details.get("reason")
        if not isinstance(reason, str) or reason not in REASON_WORDING:
            return False
        target, sentence = REASON_WORDING[reason]
        if sentence is None:
            sentence = self._player_cannot_render()
        if target == MULTI_SECTION:
            self.multi_error = sentence
        else:
            self.add_error(target, sentence)
        return True

    def _player_cannot_render(self) -> str:
        labels = character_labels(self.cast)
        picked = set(self.cleaned_data.get(RENDER_FIELD) or ())
        names = [labels[c.id] for c in self.cast if c.is_player and c.id in picked]
        return multi_turn.player_cannot_render(names)


def _grey(field: forms.Field, gate: Unavailable | None, gate_id: str) -> None:
    """Convention 22 on a widget: ``disabled``, ``aria-describedby`` and the reason as
    ``title`` (the template renders the visible reason). Not Django's ``Field.disabled``:
    :meth:`TurnForm.body` is what guarantees nothing is sent."""
    if gate is None:
        return
    field.widget.attrs.update(
        {"disabled": True, "aria-describedby": gate_id, "title": gate.reason.replace("`", "")}
    )
