"""The turn form's multi-character section (#200): its gate and its "Story setting (…)" labels.

The play page's signed snapshot carries what the gate needs (``pipeline``,
``multi_character.renders``, ``perception.level`` and ``character_knowledge.partitions``, each
``None`` when the effective-config read failed or the key was missing). :func:`gate` rebuilds a
minimal effective mapping from them and asks #188's :func:`~console.inspect.m5_gate`; no rule is
re-derived here:

- the section: ``multi_character``;
- Player view: ``perception`` at ``co_location``;
- Whisper to: ``perception`` at ``full``.

The Console checks only what the Engine's schema states: at most :data:`MAX_PRIMARIES` render
characters in one per-character turn (Engine ``orchestrator/structured/multi.py``
``MAX_PRIMARIES``), and a ``say`` for a whisper. Everything else is the Engine's 422 or 409.

:func:`first_choice` is C7's first choice for the section's selects and Player view: it sends
nothing and names the value that applies, worded as #197's markers word it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from console.inspect import (
    MULTI_CHARACTER,
    PER_CHARACTER,
    PERCEPTION,
    STRUCTURED,
    Unavailable,
    m5_gate,
)
from console.story_settings import SETTING_CHOICES, null_default_text, tie_break_label
from console.wording import humanised

MAX_PRIMARIES = 6
# The story settings the form's first choices name (#197), plus the scenario's inactive mode.
STORY_MULTI_KEYS = (
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "reactive_interjections",
    "player_view",
)
SCENARIO_MODE = "scenario_interaction_mode"
UNREAD_SETTING = "couldn't read"
PLAYER_VIEW_DEFAULT = "character"
TOO_MANY = "At most {max} characters render in one turn"
RELOAD = "The story's profile no longer renders per character — reload the page"
WHISPER_NEEDS_SAY = "Whispering needs something to say"
ONE_RENDER = "This story's profile renders one character per turn"
PLAYER_CANNOT_RENDER = "The player's character{names} can't render in a multi-character turn"
RENDER_HELP = f"(per-character renders: up to {MAX_PRIMARIES}, in the Render order below)"


class GateSource(Protocol):
    """What :func:`gate` reads from the play page's snapshot (``PlayStory``)."""

    @property
    def pipeline(self) -> str | None: ...

    @property
    def renders(self) -> str | None: ...

    @property
    def perception(self) -> str | None: ...

    @property
    def partitions(self) -> bool | None: ...


@dataclass(frozen=True)
class MultiGate:
    """Whether the section is enabled, and why the section, Player view and Whisper to are
    greyed (``None``: served). ``config_link``: the section's reason is the renders clause, so
    its visible line links the story's Config page."""

    enabled: bool
    reason: Unavailable | None
    whisper_reason: Unavailable | None
    player_view_reason: Unavailable | None
    config_link: bool = False

    @property
    def whisper_enabled(self) -> bool:
        return self.whisper_reason is None

    @property
    def player_view_enabled(self) -> bool:
        return self.player_view_reason is None


def effective_of(source: GateSource) -> dict[str, Any]:
    """The minimal effective mapping the snapshot stands for, each key left out when unknown."""
    effective: dict[str, Any] = {}
    if source.pipeline is not None:
        effective["pipeline"] = source.pipeline
    if source.renders is not None:
        effective["multi_character"] = {"renders": source.renders}
    if source.perception is not None:
        effective["perception"] = {"level": source.perception}
    if source.partitions is not None:
        effective["character_knowledge"] = {"partitions": source.partitions}
    return effective


def gate(source: GateSource) -> MultiGate:
    effective = effective_of(source)
    section = m5_gate(MULTI_CHARACTER, effective)
    renders_clause = (
        section is not None
        and source.pipeline == STRUCTURED
        and isinstance(source.renders, str)
        and source.renders != PER_CHARACTER
    )
    return MultiGate(
        section is None,
        section,
        m5_gate(PERCEPTION, effective, "full"),
        m5_gate(PERCEPTION, effective, "co_location"),
        renders_clause,
    )


def closed() -> MultiGate:
    """The gate when nothing is known (no snapshot): every control greyed as unread."""
    return gate(_Unknown())


@dataclass(frozen=True)
class _Unknown:
    pipeline: str | None = None
    renders: str | None = None
    perception: str | None = None
    partitions: bool | None = None


def setting_text(name: str, story_multi: Mapping[str, Any] | None) -> str:
    """The value that applies for the story setting ``name``, as the first choice names it."""
    if story_multi is None:
        return UNREAD_SETTING
    value = story_multi.get(name)
    if name == "reactive_interjections":
        return "On" if value is True else "Off"
    if name == "player_view" and value is None:
        return PLAYER_VIEW_DEFAULT
    if value is None:
        inactive = {"interaction_mode": {"value": story_multi.get(SCENARIO_MODE)}}
        return null_default_text(name, inactive)
    text = str(value)
    if name == "player_view" and text in dict(SETTING_CHOICES["player_view"]):
        return text
    if name == "simultaneous_tie_break" and text not in dict(SETTING_CHOICES[name]):
        return tie_break_label(text)
    known = dict(SETTING_CHOICES.get(name, []))
    return known[text].lower() if text in known else humanised(text)


def first_choice(name: str, story_multi: Mapping[str, Any] | None) -> tuple[str, str]:
    """C7's first choice: ``("", "Story setting (<what applies>)")``; it sends nothing."""
    return ("", f"Story setting ({setting_text(name, story_multi)})")


def too_many(count: int | None = None) -> str:
    text = TOO_MANY.format(max=MAX_PRIMARIES)
    return f"{text} (you picked {count})" if count is not None else text


def player_cannot_render(names: list[str]) -> str:
    return PLAYER_CANNOT_RENDER.format(names=f" ({', '.join(names)})" if names else "")
