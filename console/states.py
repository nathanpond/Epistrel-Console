"""The World tab's States (#242): the section's rows, its wording and its links.

A State (``StoryState``) reads through D5 (``console.world.state_value_text`` / ``state_values``),
its ``since_position`` through D7 (``console.world.five_part``, then C1's
``knowledge_position_text`` / ``knowledge_position_url``), and its Engine enums (``scope``,
``visibility``, ``owner``) through C3's ``humanised`` with the raw value in ``title`` (D15: open
enums, an unknown value humanised the same way). Scope alone has one display label of its own:
``character`` reads "Per character" (:data:`SCOPE_LABELS`).

Rows keep the Engine's order. The per-character ``<dl>`` lists every key the Engine sends (every
cast member, Engine ``core/states/api.py`` ``_read``) in cast order, then unknown ids. A State
whose members don't match its scope renders a non-null ``values`` object as the ``<dl>``, else
``value`` through ``state_value``.

``StateBody`` carries no ``provider_id`` (D18), so an external State's owner reads "External"
with no provider; #247 and #248 show the provider.

Links: :func:`state_url` is the one link to ``inspect:state`` (#251, #265 reuse it); a character
viewpoint is carried as ``viewpoint=character:<id>``, the omniscient one not at all.
:func:`clock_url` is ``None`` while ``inspect:clock`` (#243) doesn't resolve (D19).

Per-State URLs use the ``state_name`` path converter (:class:`StateNameConverter`, the Engine's
``StateName`` pattern), so a name the Engine would refuse never leaves the Console.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse

from console.cast import OMNISCIENT, character_of
from console.engine.models import StoryState
from console.engine_errors import EMPTY
from console.positions import knowledge_position_text, knowledge_position_url, position_step
from console.wording import character_label, humanised
from console.world import UNDEFINED, boundary_text, five_part, state_value_text, state_values

STATE_NAME_PATTERN = "[A-Za-z][A-Za-z0-9_]{0,79}"
CHARACTER_SCOPE = "character"
STORY_SCOPE = "story"
SCOPE_LABELS: Mapping[str, str] = {CHARACTER_SCOPE: "Per character", STORY_SCOPE: "Story"}
INITIAL_VALUE = "initial value"
STATES_EMPTY = "This story tracks no States yet"
STATES_EMPTY_FOR = "No States are readable from {label}'s viewpoint"
STATES_UNPARSED = "Some States couldn't be read"
NOT_READABLE = "No State “{name}” is readable here. It may not exist, be hidden from this viewpoint"
POSITION_PARTS = ("turn", "variant", "slot_k", "beat", "step")


class StateNameConverter:
    """The ``<state_name:…>`` path converter: the Engine's ``StateName`` pattern, so any other
    name is a Django 404 before the view (and the Engine) is reached."""

    regex = STATE_NAME_PATTERN

    def to_python(self, value: str) -> str:
        return value

    def to_url(self, value: str) -> str:
        return value


def state_url(story_id: str, name: str, **params: Any) -> str:
    """``inspect:state`` for ``name``, plus ``params`` (``None`` and blank values dropped; ``+``
    in ``as_of`` percent-encoded)."""
    url = reverse("inspect:state", args=[story_id, name])
    kept = {key: value for key, value in params.items() if value is not None and value != ""}
    return f"{url}?{urlencode(kept)}" if kept else url


def link_params(viewpoint: str | None) -> dict[str, str]:
    """The viewpoint a States link carries: ``character:<id>`` for a character, nothing for
    omniscient."""
    return {"viewpoint": viewpoint} if viewpoint and viewpoint != OMNISCIENT else {}


def states_url(story_id: str, viewpoint: str | None) -> str:
    """ "Back to States" (``as_of`` never carried)."""
    url = reverse("inspect:states", args=[story_id])
    params = link_params(viewpoint)
    return f"{url}?{urlencode(params)}" if params else url


def clock_url(story_id: str) -> str | None:
    """``inspect:clock`` (#243), ``None`` while that name doesn't resolve (D19)."""
    try:
        return reverse("inspect:clock", args=[story_id])
    except NoReverseMatch:
        return None


def count_text(n: int) -> str:
    """C11: "4 States" / "1 State"."""
    return f"{n:,} State" if n == 1 else f"{n:,} States"


def viewpoint_label(viewpoint: str | None, names: Mapping[str, str]) -> str | None:
    """The C4 label of a character viewpoint, ``None`` for omniscient."""
    if not viewpoint or viewpoint == OMNISCIENT:
        return None
    character = character_of(viewpoint)
    return character_label(character, names) if character is not None else viewpoint


def empty_text(viewpoint: str | None, names: Mapping[str, str]) -> str:
    """ "This story tracks no States yet", or "No States are readable from Alice's
    viewpoint"."""
    label = viewpoint_label(viewpoint, names)
    return STATES_EMPTY if label is None else STATES_EMPTY_FOR.replace("{label}", label)


def not_readable_text(name: str, as_of: str | None) -> str:
    """The one-State 404 line; " or be declared after turn 3⁺¹" only when ``as_of`` was sent
    (the C1 turn label, without ``boundary_text``'s "through")."""
    text = NOT_READABLE.replace("{name}", name)
    if as_of is None:
        return f"{text}."
    after = boundary_text(as_of).removeprefix("through ")
    return f"{text}, or be declared after {after}."


@dataclass(frozen=True)
class ValueLine:
    """One per-character value: the C4 label, D5's text and whether it is undefined (rendered
    as ``span.undefined``)."""

    label: str
    text: str
    undefined: bool


@dataclass(frozen=True)
class StateRow:
    """``_state_row.html``'s context for one State. ``value_text`` is ``None`` for a
    per-character ``<dl>`` (``per_character``); ``scope``/``visibility``/``owner`` are (label,
    raw); ``since_url`` ``None`` for an initial or malformed position; ``more`` the More rows
    after the type."""

    name: str
    url: str
    type: str
    value_text: str | None
    value_is_undefined: bool
    per_character: list[ValueLine] | None
    scope: tuple[str, str]
    visibility: tuple[str, str]
    owner: tuple[str, str]
    since_text: str
    since_url: str | None
    more: list[tuple[str, str]]
    state: StoryState


def _enum(raw: Any) -> tuple[str, str]:
    text = raw if isinstance(raw, str) else str(raw)
    return (humanised(text) if text else EMPTY, text)


def _scope(raw: Any) -> tuple[str, str]:
    text = raw if isinstance(raw, str) else str(raw)
    return (SCOPE_LABELS.get(text) or (humanised(text) if text else EMPTY), text)


def _per_character(
    values: Any, names: Mapping[str, str], cast_order: Iterable[str], state: StoryState
) -> list[ValueLine]:
    raw = values if isinstance(values, Mapping) else {}
    order = list(cast_order)
    lines = state_values(values, names, order, type=state.type, unit=state.unit)
    if not raw:
        return [ValueLine(label, text, text == UNDEFINED) for label, text in lines]
    present = [cid for cid in order if cid in raw]
    ids = present + [cid for cid in raw if cid not in set(present)]
    return [
        ValueLine(label, text, raw[cid] is None)
        for (label, text), cid in zip(lines, ids, strict=True)
    ]


def _since(story_id: str, state: StoryState) -> tuple[str, str | None, list[tuple[str, str]]]:
    raw = state.since_position
    if raw is None:
        return INITIAL_VALUE, None, []
    position = five_part(raw)
    if position is None:
        return EMPTY, None, []
    step = position_step(position)
    parts = [(part, str(getattr(position, part))) for part in POSITION_PARTS]
    return (
        knowledge_position_text(position),
        knowledge_position_url(story_id, position),
        [
            step,
            *parts,
        ],
    )


def state_row(
    state: StoryState,
    names: Mapping[str, str],
    cast_order: Sequence[str],
    story_id: str,
    viewpoint: str | None,
) -> StateRow:
    """One State as a row (see the module docstring for the value and Since rules)."""
    per_character: list[ValueLine] | None = None
    value_text: str | None = None
    listed = isinstance(state.values, Mapping)
    if listed or (state.scope == CHARACTER_SCOPE and state.value is None):
        per_character = _per_character(state.values, names, cast_order, state)
    else:
        value_text = state_value_text(state.value, state.type, state.unit)
    since_text, since_url, since_more = _since(story_id, state)
    more: list[tuple[str, str]] = []
    if state.unit:
        more.append(("Unit", state.unit))
    more += since_more
    return StateRow(
        name=state.name,
        url=state_url(story_id, state.name, **link_params(viewpoint)),
        type=state.type,
        value_text=value_text,
        value_is_undefined=per_character is None and state.value is None,
        per_character=per_character,
        scope=_scope(state.scope),
        visibility=_enum(state.visibility),
        owner=_enum(state.owner),
        since_text=since_text,
        since_url=since_url,
        more=more,
        state=state,
    )


def state_rows(
    states: Iterable[StoryState],
    names: Mapping[str, str],
    cast_order: Sequence[str],
    story_id: str,
    viewpoint: str | None,
) -> list[StateRow]:
    """The rows in the Engine's order. ``cast_order`` is empty when the story read failed (the
    per-character rows then keep the Engine's key order, labelled per C4)."""
    return [state_row(state, names, cast_order, story_id, viewpoint) for state in states]
