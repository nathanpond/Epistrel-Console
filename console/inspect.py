"""A story's Inspect area (#135): its tabs, and which of them the story's configuration serves.

Every story has one Inspect area at ``/stories/<id>/inspect/``, with the tabs of :class:`Tab` in
bar order. A tab whose Engine data needs a module the story's effective configuration switches
off stays in the bar, greyed, naming the module (the owner's decision: greyed, not hidden). The
view side is :func:`console.views.inspect.inspect_page`; this module only decides.

**The rules** (:func:`tab_availability`, read from ``GET /stories/{id}/effective-config``'s
``effective``; a missing switch counts as off). Each matches the Engine's own check:

- Memory, Retrieval: ``memory.episodic.enabled`` on. The Engine's ``require_episodic``
  (``core/mutation.py``) answers 409 ``module_off`` with ``details.module: "episodic"``.
- Canon, Scene: ``pipeline == "structured"``. ``require_canon`` (``core/mutation.py``) refuses a
  Lite story, naming ``details.module: "pipeline"`` (its ``CANON_MODULE``).
- Ledger: ``pipeline == "structured"`` **and** ``commitment.enabled`` (``commitment_enabled`` in
  ``core/commit/lifecycle.py`` needs both). A Lite story names ``pipeline``; a structured one with
  commitment off names ``commitment``.
- Knowledge: ``m5_gate("partitions")`` (#188); the Engine's ``require_partitions`` answers 409
  ``module_off`` with ``details.module: "partitions"``.
- Overview, Predicates: always served (the Engine serves the predicate registry under every
  profile).
- World, Effects (#239, D3): always served. The Engine serves the clock read and the
  reconciliation list under every configuration, so the section, not the tab, is the unit of
  gating (below).

When both the pipeline and episodic memory are off, Memory and Retrieval name ``episodic``.

**The reasons** come from one mapping (:func:`unavailable_for`) shared by the rules and by a
route's 409 ``module_off`` (:func:`module_off_unavailable`): ``pipeline`` → "Needs the structured
pipeline (`pipeline: structured`; this story runs `<level>`)"; ``episodic``, ``commitment``,
``consolidation`` → "Needs the `<module>` module (`<setting>` is off under this story's
profile)"; any other module → "Needs the `<module>` module (off under this story's
configuration)". Backtick spans are code: the panel renders them as ``<code>``, a ``title``
strips them (``console/templatetags/inspect_tags.py``).

``module_off`` is mapped here, inside Inspect only, not in #43's central ``engine_errors``.

**The M5 gates (#188, C6).** The six modules M5 delivers (:data:`M5_MODULES`: ``partitions``,
``nested_beliefs``, ``lazy_resolution``, ``testimony``, ``perception``, ``multi_character``) are
asked through :func:`m5_gate` (``None``: served), which checks, in order:

1. ``effective`` is ``None``, or a key the gate reads is missing (``pipeline``, the module's own
   switch, and ``character_knowledge.partitions`` for the four dependants; an explicit ``null``
   or a non-object step counts as missing) → :data:`UNREAD` (:data:`GATE_UNREAD`);
2. ``pipeline`` ≠ ``structured`` → "Needs the `<module>` module, which needs the structured
   pipeline (`pipeline: structured`; this story runs `lite`)" (the Engine's schema forbids every
   M5 module without the structured pipeline);
3. ``nested_beliefs``, ``lazy_resolution``, ``testimony`` and ``perception`` with partitions off
   → partitions' own reason (Engine schema rules 18-21);
4. the module's switch off, or below ``level`` → its own reason: a boolean is on only when it is
   ``True``; ``perception.level`` ranks :data:`PERCEPTION_LEVELS` (default ``co_location``; the
   only module ``level`` applies to); ``testimony`` is served by either of
   :data:`TESTIMONY_SERVED`; ``multi_character.renders`` must be :data:`PER_CHARACTER`.

:func:`unavailable_for` applies steps 2-4 in the same order, so a 409 ``module_off`` naming an M5
module reads the same as the gate (without ``effective``, or without ``pipeline``, it gives the
module's plain wording).

**Convention 22 (C6): gates outside Inspect.** A page with an M5 control reads
``get_effective_config`` once (3 s, #85), or reuses a read it already makes, and asks
``m5_gate(module, effective, level)``. A failed read passes ``None``, so the control greys with
``GATE_UNREAD`` and the form sends none of the gated fields. A greyed control is ``disabled``
through ``gate_attrs`` (on the control, or on a ``<fieldset>`` for a group), with its reason in
``console/_gated_control.html``'s visible ``<small class="gate-reason">`` (backticks as
``code_spans`` from ``inspect_tags``) and in ``title`` (``no_backticks``). It is never hidden. A
POST carrying an M5 field for a story whose gate is closed is refused Console-side by
:func:`gate_refusal`, with the reason as that field's error and **no Engine write**: the POST may
re-read the effective configuration once to check the gate, and that read is its only Engine
call. Tests assert no POST/PUT/PATCH to the write route, not an empty call log.

**D1 — the M6 gates (#239).** M6 adds two level modules and the clock's two flags
(:data:`M6_MODULES`, in the Engine's ``schema.MODULES`` order): ``clock`` (``clock.level``),
``time_claims`` (``clock.time_claims``), ``scheduled_stops`` (``clock.scheduled_stops``) and
``states`` (``states.level``). A level runs on :data:`WORLD_LEVELS` (``off`` < ``prompt_only``
< ``on``); Lite serves ``prompt_only`` (FR-LITE-5), ``on`` needs the structured pipeline.
:func:`m6_gate` (``level`` default :data:`WORLD_DEFAULT_LEVEL`, ignored for the flags) checks, in
order:

1. ``effective`` is ``None``, or ``pipeline`` or the module's switch is missing (for a flag,
   ``clock.level`` too) → :data:`UNREAD`;
2. ``on`` asked on a non-structured pipeline → "Needs `<switch>: on`, which needs the structured
   pipeline (`pipeline: structured`; this story runs `lite`)"; a flag on a non-structured
   pipeline at any level → "Needs the `<flag>` module, which needs the structured pipeline (…)"
   (Engine schema rules 12-13);
3. a flag with the clock below ``prompt_only`` → the clock's own reason (rules 22-23);
4. the switch below ``level`` → "Needs `<switch>` at `prompt_only` or above (this story runs
   `off`)" or "Needs `<switch>: on` (this story runs `prompt_only`)"; a flag that isn't ``True``
   → "Needs the `<flag>` module (`<switch>` is off under this story's profile)" (M5's boolean
   wording, never "(this story runs …)"). An unexpected level value counts as below
   ``prompt_only`` and is named raw (JSON for a non-string).

``time_claims`` checks only the clock, not ``verification`` (rule 22), and rule 24 (``clock``
needs ``commitment`` under ``structured``) is not gated: the Engine can't store either
configuration. :func:`unavailable_for` words the M6 modules in the same order, so a 409
``module_off`` naming ``states`` or ``clock`` reads the same as the gate; without ``effective``,
without ``pipeline``, with a missing switch, or for a stale 409 it gives the plain wording.
:func:`states_served`/:func:`clock_served` are the boolean wrappers; :func:`states_level` and
:func:`clock_level` the plain readers. :func:`module_gate` routes an M5 module to :func:`m5_gate`
and an M6 one to :func:`m6_gate`, and :func:`gate_refusal` calls it, so convention 22 covers M6
fields unchanged. Gate exactly what the Engine refuses: ``states`` at ``prompt_only`` for every
States read and write, proposals, settle-now and compensate; ``states`` at ``on`` for the effects
trail; ``clock`` at ``prompt_only`` for advance and scheduled-event writes. Never gated: the clock
read, the clock settings PATCH, the scheduled-events list, external values, the reconciliation
list, providers and bindings.

**D2 — the Overview's M6 rows.** :data:`MODULE_ROWS` lists ``clock``, ``time_claims``,
``scheduled_stops`` and ``states`` after ``commitment`` (the Engine's order), each only when its
switch is in ``effective``. ``off`` reads "off" (muted), ``on`` "on", ``prompt_only`` verbatim,
anything else RAW; the flags read "on"/"off". :data:`CAPABILITY_LABELS` names them ("World
clock", "Time-claim verification", "Scheduled-event stops", "States"), so ``clock:prompt_only``
reads "World clock (prompt_only)".

**D3 — the World and Effects tabs and their sections.** ``Tab.WORLD`` and ``Tab.EFFECTS`` follow
Knowledge in the bar and are never greyed. Each section is its own URL with its own gate, from
:data:`WORLD_SECTIONS` / :data:`EFFECTS_SECTIONS` (``(url name, label, module, level)``);
:func:`sections` lists the registered ones, each greyed through :func:`module_gate`. A greyed
section's URL renders the inline panel and never calls its read
(:func:`console.views.inspect.inspect_page`). The tab's entry URL redirects to the first open
section, else renders the shell. "Current" is an exact URL-name match: every page below a section
passes its parent section's URL name as ``current``. Per-State pages live under the singular
``world/state/<name>/…``, beside the fixed ``world/states/new/`` and ``world/states/proposals/``.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeGuard
from urllib.parse import urlencode

from django.http import QueryDict
from django.urls import NoReverseMatch, reverse

from console.engine.client import EngineError
from console.engine.models import EffectiveConfig

MODULE_OFF = "module_off"  # the 409's code

PIPELINE = "pipeline"
EPISODIC = "episodic"
COMMITMENT = "commitment"
CONSOLIDATION = "consolidation"
STRUCTURED = "structured"

# The M5 modules (#188), named as the Engine's ``details.module`` names them.
PARTITIONS = "partitions"
NESTED_BELIEFS = "nested_beliefs"
LAZY_RESOLUTION = "lazy_resolution"
TESTIMONY = "testimony"
PERCEPTION = "perception"
MULTI_CHARACTER = "multi_character"
M5_MODULES: tuple[str, ...] = (
    PARTITIONS,
    NESTED_BELIEFS,
    LAZY_RESOLUTION,
    TESTIMONY,
    PERCEPTION,
    MULTI_CHARACTER,
)

# The M6 modules (#239, D1), named as the Engine's ``schema.MODULES`` names them: two level modules
# and the clock's two flags. The Engine's ``details.module`` names only ``clock`` and ``states``.
CLOCK = "clock"
TIME_CLAIMS = "time_claims"
SCHEDULED_STOPS = "scheduled_stops"
STATES = "states"
M6_MODULES: tuple[str, ...] = (CLOCK, TIME_CLAIMS, SCHEDULED_STOPS, STATES)
CLOCK_FLAGS = frozenset({TIME_CLAIMS, SCHEDULED_STOPS})
WORLD_LEVELS: tuple[str, ...] = ("off", "prompt_only", "on")  # the Engine's ladder
WORLD_DEFAULT_LEVEL = "prompt_only"  # what an M6 gate asks without ``level`` (Lite serves it)
WORLD_ON = WORLD_LEVELS[-1]

# A module's switch in ``effective`` (``pipeline`` is a level, not a switch).
SETTINGS: Mapping[str, str] = {
    PIPELINE: "pipeline",
    EPISODIC: "memory.episodic.enabled",
    COMMITMENT: "commitment.enabled",
    CONSOLIDATION: "memory.consolidation.enabled",
    PARTITIONS: "character_knowledge.partitions",
    NESTED_BELIEFS: "character_knowledge.nested_beliefs.enabled",
    LAZY_RESOLUTION: "character_knowledge.lazy_resolution",
    TESTIMONY: "character_knowledge.testimony",
    PERCEPTION: "perception.level",
    MULTI_CHARACTER: "multi_character.renders",
    CLOCK: "clock.level",
    TIME_CLAIMS: "clock.time_claims",
    SCHEDULED_STOPS: "clock.scheduled_stops",
    STATES: "states.level",
}

PERCEPTION_LEVELS: tuple[str, ...] = ("none", "co_location", "full")  # the Engine's ladder
PERCEPTION_DEFAULT_LEVEL = "co_location"  # what a perception gate asks without ``level``
TESTIMONY_SERVED: tuple[str, ...] = ("model", "defaulted")  # alternatives, not a ladder
PER_CHARACTER = "per_character"
BOOLEAN_MODULES = frozenset({PARTITIONS, NESTED_BELIEFS, LAZY_RESOLUTION})
# The M5 modules the Engine's schema lets on only with partitions (rules 18-21).
NEEDS_PARTITIONS: tuple[str, ...] = (NESTED_BELIEFS, LAZY_RESOLUTION, TESTIMONY, PERCEPTION)


class Tab(StrEnum):
    """The Inspect tabs, in bar order. The value is the URL name (``inspect:<value>``) and the
    path segment (``inspect/<value>/``; Overview is ``inspect/`` itself)."""

    OVERVIEW = "overview"
    MEMORY = "memory"
    RETRIEVAL = "retrieval"
    CANON = "canon"
    KNOWLEDGE = "knowledge"
    WORLD = "world"
    EFFECTS = "effects"
    PREDICATES = "predicates"
    LEDGER = "ledger"
    SCENE = "scene"

    @property
    def label(self) -> str:
        """The tab's name in the bar and the page ``<title>``: "Overview", "Memory", …"""
        return self.value.capitalize()

    @property
    def url_name(self) -> str:
        return f"inspect:{self.value}"


@dataclass(frozen=True)
class Unavailable:
    """Why a tab can't be served: the Engine ``module`` that is off, its switch in the effective
    configuration (``None`` for a module the Console doesn't know), and the reason text (backtick
    spans are code)."""

    module: str
    setting: str | None
    reason: str


def setting(effective: Mapping[str, Any], path: str) -> Any | None:
    """The value at dotted ``path`` (``"memory.episodic.enabled"``) in ``effective``, or ``None``
    when any step is missing or not an object."""
    value: Any = effective
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


def _on(effective: Mapping[str, Any], module: str) -> bool:
    return setting(effective, SETTINGS[module]) is True


def is_structured_pipeline(effective: Mapping[str, Any]) -> bool:
    """The story runs the structured pipeline (``pipeline == "structured"``)."""
    return setting(effective, SETTINGS[PIPELINE]) == STRUCTURED


def _check_level(level: str | None) -> None:
    if level is not None and level not in PERCEPTION_LEVELS:
        raise ValueError(f"{level!r} is not a perception level: {list(PERCEPTION_LEVELS)}")


def _named(value: Any) -> str | None:
    """A switch's value as "(this story runs …)" names it: a string as given, anything else as
    JSON; ``None`` (unknown) or an empty string names nothing."""
    if value is None or value == "":
        return None
    return value if isinstance(value, str) else json.dumps(value)


def _served(module: str, value: Any, level: str | None) -> bool:
    """Whether an M5 module's switch ``value`` serves the module (at ``level``, perception)."""
    if module in BOOLEAN_MODULES:
        return value is True
    if module == TESTIMONY:
        return isinstance(value, str) and value in TESTIMONY_SERVED
    if module == MULTI_CHARACTER:
        return bool(value == PER_CHARACTER)
    wanted = PERCEPTION_LEVELS.index(level or PERCEPTION_DEFAULT_LEVEL)
    return (
        isinstance(value, str)
        and value in PERCEPTION_LEVELS
        and PERCEPTION_LEVELS.index(value) >= wanted
    )


def _m5_wording(module: str, value: Any, level: str | None) -> str:
    """An M5 module's own reason; "(this story runs …)" names ``value`` unless it is unknown
    or serves the module (a stale 409)."""
    switch = SETTINGS[module]
    if module in BOOLEAN_MODULES:
        return f"Needs the `{module}` module (`{switch}` is off under this story's profile)"
    if module == PERCEPTION:
        wanted = level or PERCEPTION_DEFAULT_LEVEL
        if wanted == PERCEPTION_LEVELS[-1]:
            needs = f"Needs `{switch}: {wanted}`"
        else:
            needs = f"Needs `{switch}` at `{wanted}` or above"
    elif module == MULTI_CHARACTER:
        needs = f"Needs `{switch}: {PER_CHARACTER}`"
    else:
        needs = f"Needs `{switch}` at " + " or ".join(f"`{v}`" for v in TESTIMONY_SERVED)
    named = None if _served(module, value, level) else _named(value)
    return f"{needs} (this story runs `{named}`)" if named is not None else needs


def _runs(pipeline: Any) -> str:
    """The pipeline clause's "; this story runs `<level>`", or "" for a non-string or empty one."""
    return f"; this story runs `{pipeline}`" if isinstance(pipeline, str) and pipeline else ""


def _pipeline_clause(module: str, pipeline: Any) -> Unavailable:
    reason = (
        f"Needs the `{module}` module, which needs the structured pipeline "
        f"(`pipeline: structured`{_runs(pipeline)})"
    )
    return Unavailable(module, SETTINGS[module], reason)


# --- the M6 gates (#239, D1) ---------------------------------------------------------------------


def _check_world_level(level: str | None) -> None:
    if level is not None and level not in WORLD_LEVELS[1:]:
        raise ValueError(f"{level!r} is not an M6 gate level: {list(WORLD_LEVELS[1:])}")


def _world_wanted(module: str, level: str | None) -> str | None:
    """The level an M6 gate asks of ``module``: ``level`` or the default for ``clock`` and
    ``states``; ``None`` for the two flags (``level`` is ignored for them)."""
    return None if module in CLOCK_FLAGS else (level or WORLD_DEFAULT_LEVEL)


def _world_served(module: str, value: Any, level: str | None) -> bool:
    """Whether an M6 switch ``value`` serves ``module`` at ``level``: a flag only when it is
    ``True``; a level when it is on :data:`WORLD_LEVELS` at ``level`` or above (an unexpected
    value counts as below ``prompt_only``)."""
    wanted = _world_wanted(module, level)
    if wanted is None:
        return value is True
    return (
        isinstance(value, str)
        and value in WORLD_LEVELS
        and WORLD_LEVELS.index(value) >= WORLD_LEVELS.index(wanted)
    )


def _m6_wording(module: str, value: Any, level: str | None) -> str:
    """An M6 module's own reason. A flag reads M5's boolean wording (never "(this story runs
    …)"); a level names ``value`` unless it is unknown or serves the module (a stale 409)."""
    switch = SETTINGS[module]
    wanted = _world_wanted(module, level)
    if wanted is None:
        return f"Needs the `{module}` module (`{switch}` is off under this story's profile)"
    if wanted == WORLD_ON:
        needs = f"Needs `{switch}: {WORLD_ON}`"
    else:
        needs = f"Needs `{switch}` at `{wanted}` or above"
    named = None if _world_served(module, value, level) else _named(value)
    return f"{needs} (this story runs `{named}`)" if named is not None else needs


def _world_pipeline_clause(module: str, level: str | None, pipeline: Any) -> Unavailable | None:
    """D1 step 2: a flag on a non-structured pipeline (Engine schema rules 12-13), or ``on`` asked
    of ``clock``/``states`` there (``prompt_only`` serves under Lite, FR-LITE-5); else ``None``."""
    if pipeline == STRUCTURED:
        return None
    if module in CLOCK_FLAGS:
        return _pipeline_clause(module, pipeline)
    if _world_wanted(module, level) != WORLD_ON:
        return None
    switch = SETTINGS[module]
    reason = (
        f"Needs `{switch}: {WORLD_ON}`, which needs the structured pipeline "
        f"(`pipeline: structured`{_runs(pipeline)})"
    )
    return Unavailable(module, switch, reason)


def _m6_unavailable(
    module: str, effective: Mapping[str, Any] | None, level: str | None
) -> Unavailable:
    """:func:`unavailable_for` for an M6 module: D1's steps 2-4 when ``effective`` carries the
    pipeline, else (and for a missing value) the plain wording."""
    pipeline = setting(effective, SETTINGS[PIPELINE]) if effective is not None else None
    value = None
    if effective is not None and pipeline is not None:
        clause = _world_pipeline_clause(module, level, pipeline)
        if clause is not None:
            return clause
        if module in CLOCK_FLAGS:
            clock = setting(effective, SETTINGS[CLOCK])
            if clock is not None and not _world_served(CLOCK, clock, None):
                return _m6_unavailable(CLOCK, effective, None)
        value = setting(effective, SETTINGS[module])
    return Unavailable(module, SETTINGS[module], _m6_wording(module, value, level))


LITE_REWRITES = "lite_rewrites"
LITE_REWRITES_TEXT = (
    "History rewrites need a planner for this story's pipeline (`lite_rewrites` is off)"
)


def unavailable_for(
    module: str, effective: Mapping[str, Any] | None = None, level: str | None = None
) -> Unavailable:
    """The :class:`Unavailable` naming ``module``. ``effective`` supplies the pipeline level the
    story runs (left out of the reason when unknown); for an M5 module it also supplies the
    pipeline clause, the partitions prerequisite and the value "(this story runs …)" names, in
    :func:`m5_gate`'s order, and ``level`` is the perception level asked for. An M6 module
    (#239) is worded in :func:`m6_gate`'s order, ``level`` then being a :data:`WORLD_LEVELS` rung
    (``prompt_only`` or ``on``; ignored for the two clock flags). ``lite_rewrites`` (#294, E5)
    has its own sentence: the Engine refuses a history rewrite when the story's pipeline has no
    planner, which the Console can't predict from the effective configuration."""
    if module == LITE_REWRITES:
        return Unavailable(LITE_REWRITES, None, LITE_REWRITES_TEXT)
    if module in M6_MODULES:
        _check_world_level(level)
        return _m6_unavailable(module, effective, level)
    _check_level(level)
    if module == PIPELINE:
        pipeline_level = setting(effective, SETTINGS[PIPELINE]) if effective is not None else None
        runs = (
            f"; this story runs `{pipeline_level}`"
            if isinstance(pipeline_level, str) and pipeline_level
            else ""
        )
        reason = f"Needs the structured pipeline (`pipeline: structured`{runs})"
        return Unavailable(PIPELINE, SETTINGS[PIPELINE], reason)
    if module in M5_MODULES:
        pipeline = setting(effective, SETTINGS[PIPELINE]) if effective is not None else None
        value = None
        if effective is not None and pipeline is not None:
            if pipeline != STRUCTURED:
                return _pipeline_clause(module, pipeline)
            partitions = setting(effective, SETTINGS[PARTITIONS])
            if module in NEEDS_PARTITIONS and partitions is not None and partitions is not True:
                return unavailable_for(PARTITIONS, effective)
            value = setting(effective, SETTINGS[module])
        return Unavailable(module, SETTINGS[module], _m5_wording(module, value, level))
    switch = SETTINGS.get(module)
    if switch is None:
        reason = f"Needs the `{module}` module (off under this story's configuration)"
        return Unavailable(module, None, reason)
    reason = f"Needs the `{module}` module (`{switch}` is off under this story's profile)"
    return Unavailable(module, switch, reason)


GATE_UNREAD = "Couldn't read this story's configuration — reload"
UNREAD = Unavailable("", None, GATE_UNREAD)


def m5_gate(
    module: str, effective: Mapping[str, Any] | None, level: str | None = None
) -> Unavailable | None:
    """Why ``effective`` can't serve the M5 ``module`` (at perception ``level``, default
    ``co_location``; ignored for the other five), or ``None`` when it is served. The order is the
    module docstring's: :data:`UNREAD`, the pipeline clause, partitions' reason for a dependant,
    the module's own reason. An unknown ``module`` raises ``KeyError``; a ``level`` outside
    :data:`PERCEPTION_LEVELS` raises ``ValueError``."""
    if module not in M5_MODULES:
        raise KeyError(module)
    _check_level(level)
    if effective is None:
        return UNREAD
    pipeline = setting(effective, SETTINGS[PIPELINE])
    value = setting(effective, SETTINGS[module])
    partitions = setting(effective, SETTINGS[PARTITIONS])
    if pipeline is None or value is None or (module in NEEDS_PARTITIONS and partitions is None):
        return UNREAD
    if pipeline != STRUCTURED:
        return _pipeline_clause(module, pipeline)
    if module in NEEDS_PARTITIONS and partitions is not True:
        return unavailable_for(PARTITIONS, effective)
    return None if _served(module, value, level) else unavailable_for(module, effective, level)


def m6_gate(
    module: str, effective: Mapping[str, Any] | None, level: str | None = None
) -> Unavailable | None:
    """Why ``effective`` can't serve the M6 ``module`` (``clock`` or ``states`` at ``level``,
    default :data:`WORLD_DEFAULT_LEVEL`; ``level`` is validated and ignored for the two flags), or
    ``None`` when it is served. The order is D1's (the module docstring): :data:`UNREAD`, the
    pipeline clause, the clock's reason for a flag, the module's own reason. An unknown
    ``module`` raises ``KeyError``; a ``level`` outside ``WORLD_LEVELS[1:]`` raises
    ``ValueError``."""
    if module not in M6_MODULES:
        raise KeyError(module)
    _check_world_level(level)
    if effective is None:
        return UNREAD
    pipeline = setting(effective, SETTINGS[PIPELINE])
    value = setting(effective, SETTINGS[module])
    flag = module in CLOCK_FLAGS
    if pipeline is None or value is None or (flag and setting(effective, SETTINGS[CLOCK]) is None):
        return UNREAD
    clause = _world_pipeline_clause(module, level, pipeline)
    if clause is not None:
        return clause
    if flag:
        clock = m6_gate(CLOCK, effective)
        if clock is not None:
            return clock
    if _world_served(module, value, level):
        return None
    return unavailable_for(module, effective, level)


def states_served(effective: Mapping[str, Any] | None, level: str | None = None) -> bool:
    """The story serves States at ``level`` (default ``prompt_only``; the ``states`` gate is
    open)."""
    return m6_gate(STATES, effective, level) is None


def clock_served(effective: Mapping[str, Any] | None, level: str | None = None) -> bool:
    """The story runs the world clock at ``level`` (default ``prompt_only``; the ``clock`` gate
    is open)."""
    return m6_gate(CLOCK, effective, level) is None


def _raw_string(effective: Mapping[str, Any] | None, module: str) -> str | None:
    value = setting(effective, SETTINGS[module]) if effective is not None else None
    return value if isinstance(value, str) else None


def states_level(effective: Mapping[str, Any] | None) -> str | None:
    """The raw ``states.level`` when it is a string, whatever the pipeline (not a gate)."""
    return _raw_string(effective, STATES)


def clock_level(effective: Mapping[str, Any] | None) -> str | None:
    """The raw ``clock.level`` when it is a string, whatever the pipeline (not a gate)."""
    return _raw_string(effective, CLOCK)


def module_gate(
    module: str, effective: Mapping[str, Any] | None, level: str | None = None
) -> Unavailable | None:
    """Convention 22's one entry point: an M5 module through :func:`m5_gate`, an M6 module
    through :func:`m6_gate`. Any other module (``episodic``, ``pipeline``) raises ``KeyError``."""
    if module in M5_MODULES:
        return m5_gate(module, effective, level)
    if module in M6_MODULES:
        return m6_gate(module, effective, level)
    raise KeyError(module)


def partitions_on(effective: Mapping[str, Any] | None) -> bool:
    """The story keeps per-character partitions (the ``partitions`` gate is open)."""
    return m5_gate(PARTITIONS, effective) is None


def nested_beliefs_on(effective: Mapping[str, Any] | None) -> bool:
    """The story keeps nested beliefs (the ``nested_beliefs`` gate is open)."""
    return m5_gate(NESTED_BELIEFS, effective) is None


def renders_per_character(effective: Mapping[str, Any] | None) -> bool:
    """The story renders per character (the ``multi_character`` gate is open)."""
    return m5_gate(MULTI_CHARACTER, effective) is None


def perception_level(effective: Mapping[str, Any] | None) -> str | None:
    """The raw ``perception.level`` when it is a string, whatever the pipeline (not a gate);
    ``None`` when missing or not a string."""
    value = setting(effective, SETTINGS[PERCEPTION]) if effective is not None else None
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class GateRefusal:
    """A POST field :func:`gate_refusal` refuses, and why."""

    field: str
    unavailable: Unavailable

    @property
    def reason(self) -> str:
        return self.unavailable.reason


def gate_refusal(
    data: QueryDict,
    effective: Mapping[str, Any] | None,
    gated: Mapping[str, tuple[str, str | None]],
) -> GateRefusal | None:
    """Convention 22's POST check: the first field of ``gated`` (field name → ``(module,
    level)``, any of the M5 and M6 modules, in its order) that ``data`` carries with any non-blank
    value (an "off"-looking one included) and whose :func:`module_gate` is closed; ``None`` when
    every carried field is served.
    ``effective`` ``None`` (a failed read) refuses every carried field with
    :data:`GATE_UNREAD`. The view calls it before any Engine write and makes none when it
    refuses."""
    for field, (module, level) in gated.items():
        if not any(str(value).strip() for value in data.getlist(field)):
            continue
        gate = module_gate(module, effective, level)
        if gate is not None:
            return GateRefusal(field, gate)
    return None


def _ledger(effective: Mapping[str, Any]) -> Unavailable | None:
    if not is_structured_pipeline(effective):
        return unavailable_for(PIPELINE, effective)
    return None if _on(effective, COMMITMENT) else unavailable_for(COMMITMENT, effective)


def tab_availability(effective: Mapping[str, Any]) -> dict[Tab, Unavailable | None]:
    """Every tab, mapped to why it is greyed for ``effective`` (``None``: served). The rules are
    the module docstring's."""
    episodic = None if _on(effective, EPISODIC) else unavailable_for(EPISODIC, effective)
    structured = None if is_structured_pipeline(effective) else unavailable_for(PIPELINE, effective)
    return {
        Tab.OVERVIEW: None,
        Tab.MEMORY: episodic,
        Tab.RETRIEVAL: episodic,
        Tab.CANON: structured,
        Tab.KNOWLEDGE: m5_gate(PARTITIONS, effective),
        Tab.WORLD: None,  # D3: never gated as a whole; its sections grey individually
        Tab.EFFECTS: None,
        Tab.PREDICATES: None,
        Tab.LEDGER: _ledger(effective),
        Tab.SCENE: structured,
    }


# The module a tab's rule names, for a 409 ``module_off`` without ``details.module``.
RULE_MODULES: Mapping[Tab, str] = {
    Tab.MEMORY: EPISODIC,
    Tab.RETRIEVAL: EPISODIC,
    Tab.CANON: PIPELINE,
    Tab.KNOWLEDGE: PARTITIONS,
    Tab.LEDGER: COMMITMENT,
    Tab.SCENE: PIPELINE,
}
UNNAMED_REASON = "Needs a module that is off under this story's configuration"


def is_module_off(exc: object) -> TypeGuard[EngineError]:
    return isinstance(exc, EngineError) and exc.http_status == 409 and exc.code == MODULE_OFF


def module_off_unavailable(
    error: EngineError,
    tab: Tab | None = None,
    effective: Mapping[str, Any] | None = None,
    *,
    level: str | None = None,
) -> Unavailable:
    """A route's 409 ``module_off`` as the greyed panel's (or form error's) reason: the module
    ``error.details.module`` names, else the module ``tab``'s rule names (no ``tab``, or a tab
    without a rule, gets a reason naming no module). ``effective`` and ``level`` are as in
    :func:`unavailable_for`, so an M5 module reads the same as its gate."""
    named = error.details.get("module")
    if isinstance(named, str) and named:
        return unavailable_for(named, effective, level)
    module = RULE_MODULES.get(tab) if tab is not None else None
    if module is None:
        return Unavailable("", None, UNNAMED_REASON)
    return unavailable_for(module, effective, level)


# --- the Overview (#151) --------------------------------------------------------------------------
#
# What the story runs with, from the shell's one effective-config read: the capabilities the
# Engine reports (``capability_names()`` in its ``core/profiles/capabilities.py``: a module on by
# its name, ``<module>:<level>`` at another level, the pipeline by its level, sorted), the module
# table (one row per module the Engine delivers through M6, in its ``schema.MODULES`` order), and
# a link to the Engine's capability map. Nothing is derived Console-side: ``floor`` and
# required-field repair are never in ``capabilities`` and are not shown.

# The Engine serves no capability-map route. A version-tag link is dead for released tags (v0.1.1
# has no such doc), so the link is to ``main``, unversioned.
CAPABILITY_MAP_URL = "https://github.com/nathanpond/Epistrel/blob/main/docs/capability-map.md"
CAPABILITY_MAP_LABEL = "Capability map (Engine docs, main)"

CAPABILITY_LABELS: Mapping[str, str] = {
    "lite": "Lite pipeline",
    "structured": "Structured pipeline",
    "episodic": "Episodic memory",
    "consolidation": "Consolidation",
    "commitment": "Commitment",
    "pins": "Pinned memories",
    "polish": "Polish",
    "rolling_summary": "Rolling summary",
    "partitions": "Per-character partitions",
    "nested_beliefs": "Nested beliefs",
    "lazy_resolution": "Lazy resolution",
    "testimony": "Testimony",
    "perception": "Perception",
    "multi_character": "Multi-character renders",
    "budgets": "Budgets",
    "models": "Model routing",
    "repair": "Repair attempts",
    "contradiction_checking": "Contradiction checking",
    "verification": "Verification",
    CLOCK: "World clock",
    TIME_CLAIMS: "Time-claim verification",
    SCHEDULED_STOPS: "Scheduled-event stops",
    STATES: "States",
}
ALWAYS_ON = frozenset({"budgets", "models"})  # reported under every profile


@dataclass(frozen=True)
class Capability:
    """One reported capability: the raw ``value`` and its label (``None``: unknown, shown as
    ``<code>value</code>``)."""

    value: str
    label: str | None


def capability_label(value: str) -> str | None:
    """``"episodic"`` → "Episodic memory"; ``"verification:entailment"`` → "Verification
    (entailment)"; ``budgets``/``models`` add "(always on)"; an unknown module → ``None``."""
    module, _, level = value.partition(":")
    label = CAPABILITY_LABELS.get(module)
    if label is None:
        return None
    if level:
        return f"{label} ({level})"
    return f"{label} (always on)" if module in ALWAYS_ON else label


def capabilities(config: EffectiveConfig) -> list[Capability]:
    """Every value the Engine reports, the pipeline level (the value equal to
    ``effective.pipeline``) first and the rest in the Engine's order. Without that value the
    Engine's order is kept as is."""
    values = list(config.capabilities)
    level = setting(config.effective, SETTINGS[PIPELINE])
    if isinstance(level, str) and level in values:
        values.remove(level)
        values.insert(0, level)
    return [Capability(value, capability_label(value)) for value in values]


# One row per module the Engine delivers (through M6), in its ``schema.MODULES`` order: (module,
# switch). A row shows only when its switch is in ``effective`` (an M4/M5-shaped configuration has
# no ``clock``/``states`` keys, so no M6 row; D2).
MODULE_ROWS: tuple[tuple[str, str], ...] = (
    ("pipeline", "pipeline"),
    ("rolling_summary", "memory.rolling_summary.enabled"),
    ("episodic", "memory.episodic.enabled"),
    ("consolidation", "memory.consolidation.enabled"),
    ("pins", "pins.enabled"),
    (PARTITIONS, SETTINGS[PARTITIONS]),
    (NESTED_BELIEFS, SETTINGS[NESTED_BELIEFS]),
    (LAZY_RESOLUTION, SETTINGS[LAZY_RESOLUTION]),
    (TESTIMONY, SETTINGS[TESTIMONY]),
    (PERCEPTION, SETTINGS[PERCEPTION]),
    ("verification", "verification.level"),
    ("contradiction_checking", "verification.contradiction_checking"),
    ("commitment", "commitment.enabled"),
    (CLOCK, SETTINGS[CLOCK]),
    (TIME_CLAIMS, SETTINGS[TIME_CLAIMS]),
    (SCHEDULED_STOPS, SETTINGS[SCHEDULED_STOPS]),
    (STATES, SETTINGS[STATES]),
    (MULTI_CHARACTER, SETTINGS[MULTI_CHARACTER]),
    ("repair", "repair.max_attempts"),
    ("polish", "repair.polish.enabled"),
    ("budgets", "budgets"),
    ("models", "models"),
)
# The structured pipeline's dials, listed under the ``pipeline`` row (``render.*`` is not a module).
RENDER_DIALS: tuple[str, ...] = (
    "render.max_beats",
    "render.prompt_token_budget",
    "render.working_memory_beats",
)
# Each module's dials, listed under its row: (module, caption, paths). ``nested_beliefs.depth``
# sits under partitions and shows even when nested beliefs are off.
MODULE_DIALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pipeline", "Structured pipeline dials", RENDER_DIALS),
    (
        PARTITIONS,
        "Character knowledge dials",
        (
            "character_knowledge.max_adjudications_per_turn",
            "character_knowledge.max_lazy_resolutions_per_turn",
            "character_knowledge.nested_beliefs.depth",
        ),
    ),
    (MULTI_CHARACTER, "Multi-character dials", ("multi_character.interjection_max_words",)),
)
UNUSED_UNDER_LITE = " (unused under lite)"
UNUSED_WHILE_OFF = " (unused while off)"

# The M5 level switches' "off" values, and the levels each reads verbatim; any other value is RAW.
OFF_VALUES: Mapping[str, str] = {TESTIMONY: "off", PERCEPTION: "none", MULTI_CHARACTER: "combined"}
KNOWN_LEVELS: Mapping[str, frozenset[str]] = {
    TESTIMONY: frozenset(TESTIMONY_SERVED),
    PERCEPTION: frozenset({"co_location", "full"}),
    MULTI_CHARACTER: frozenset({PER_CHARACTER}),
}

# A row's state kinds: "on" / "off" / "always on" read as words, a ``level`` verbatim, ``attempts``
# "on · N attempt(s)", ``raw`` an unexpected value verbatim in ``<code>``.
ON, OFF, ALWAYS, LEVEL, ATTEMPTS, RAW = "on", "off", "always on", "level", "attempts", "raw"


@dataclass(frozen=True)
class Dial:
    path: str
    value: Any

    @property
    def shown(self) -> str:
        """An integer with thousands separators; anything else verbatim."""
        if isinstance(self.value, int) and not isinstance(self.value, bool):
            return f"{self.value:,}"
        return str(self.value)


@dataclass(frozen=True)
class DialGroup:
    """The dials listed under a module row: the ``caption``, the present ``dials`` in
    :data:`MODULE_DIALS` order, and the caption's ``note`` (:data:`UNUSED_UNDER_LITE`,
    :data:`UNUSED_WHILE_OFF` or "")."""

    caption: str
    dials: tuple[Dial, ...]
    note: str = ""


@dataclass(frozen=True)
class ModuleRow:
    """One module table row: the module, the switch path it reads, its state kind and the value
    (a level, an attempt count, or a raw value), and its dials (``None``: no dial row)."""

    module: str
    path: str
    kind: str
    value: Any = None
    dials: DialGroup | None = None

    @property
    def off(self) -> bool:
        return self.kind == OFF


def _state(module: str, value: Any) -> tuple[str, Any]:
    if module in ALWAYS_ON:
        return ALWAYS, None
    if module in (CLOCK, STATES):  # D2: before the boolean rule, so a boolean level reads RAW
        if value == WORLD_LEVELS[0]:
            return OFF, None
        if value == WORLD_ON:
            return ON, None
        return (LEVEL, value) if value == WORLD_DEFAULT_LEVEL else (RAW, value)
    if isinstance(value, bool):
        return (ON if value else OFF), None
    if module == "repair" and isinstance(value, int):
        return (ATTEMPTS, value) if value > 0 else (OFF, None)  # the Engine's rule
    if module in OFF_VALUES:
        if value == OFF_VALUES[module]:
            return OFF, None
        return (LEVEL, value) if value in KNOWN_LEVELS[module] else (RAW, value)
    if module in ("pipeline", "verification") and isinstance(value, str) and value:
        return (OFF, None) if value == OFF else (LEVEL, value)
    return RAW, value


def _dial_group(effective: Mapping[str, Any], module: str, kind: str) -> DialGroup | None:
    """``module``'s :data:`MODULE_DIALS` group, its dials missing from ``effective`` left out
    (``None`` when it has no group or none is present). The note: unused under lite, else
    unused while the row reads "off" (a RAW state is not off)."""
    for owner, caption, paths in MODULE_DIALS:
        if owner != module:
            continue
        dials = tuple(
            Dial(path, value) for path in paths if (value := setting(effective, path)) is not None
        )
        if not dials:
            return None
        if setting(effective, SETTINGS[PIPELINE]) == "lite":
            note = UNUSED_UNDER_LITE
        else:
            note = UNUSED_WHILE_OFF if kind == OFF else ""
        return DialGroup(caption, dials, note)
    return None


def module_rows(effective: Mapping[str, Any]) -> list[ModuleRow]:
    """The module table: a row per :data:`MODULE_ROWS` entry whose switch is in ``effective``
    (the Engine sends only delivered settings, so an undelivered module has no row), each with
    its dial group."""
    rows = []
    for module, path in MODULE_ROWS:
        value = setting(effective, path)
        if value is None:
            continue
        kind, shown = _state(module, value)
        rows.append(ModuleRow(module, path, kind, shown, _dial_group(effective, module, kind)))
    return rows


FINGERPRINT_PREFIX = "sha256:"
FINGERPRINT_SHOWN = 12


def overview_fingerprint(value: str) -> str:
    """``sha256:`` and the first 12 hex digits when prefixed, else the value's first 12
    characters; "—" when empty."""
    if not value:
        return "—"
    if value.startswith(FINGERPRINT_PREFIX):
        return FINGERPRINT_PREFIX + value.removeprefix(FINGERPRINT_PREFIX)[:FINGERPRINT_SHOWN]
    return value[:FINGERPRINT_SHOWN]


def profile_flags(config: EffectiveConfig) -> str:
    """ "pinned", "deleted", "pinned, deleted" or "" for the header line."""
    profile = config.profile
    return ", ".join(
        name for name, on in (("pinned", profile.pinned), ("deleted", profile.deleted)) if on
    )


# --- shared M4 formatting and links (#135 conventions 2 and 3; first used by #140) --------------

SHORT_ID = 8  # convention 2: an Engine id shows its last 8 characters


def short_id(value: str) -> str:
    """Convention 2's shown part of an Engine id: "…" and its last 8 characters (a shorter
    value as given). The template filter ``engine_id`` wraps it in ``<code>`` with the full id
    in ``title``."""
    return f"…{value[-SHORT_ID:]}" if len(value) > SHORT_ID else value


def history_turn_url(story_id: str, turn: int, variant: int | None = None) -> str:
    """Convention 3: the story's history at ``turn`` on the active chain: ``before_turn`` one
    past it and the ``#turn-<turn>-<variant or 0>`` anchor of ``history/_items.html`` (#142
    passes a turn's variant; the page still shows the active chain)."""
    base = reverse("stories:history", args=[story_id])
    return f"{base}?{urlencode({'before_turn': turn + 1})}#turn-{turn}-{variant or 0}"


def position_text(turn: int | None, beat: int | None, variant: int | None = None) -> str | None:
    """Convention 4: "turn 12 · beat 3" ("turn 12 · v2 · beat 3" with a variant), skipping
    nulls; ``None`` when every part is null (first used by #141)."""
    parts = [
        f"{name}{value}"
        for name, value in (("turn ", turn), ("v", variant), ("beat ", beat))
        if value is not None
    ]
    return " · ".join(parts) or None


def retrieval_url(story_id: str, params: Mapping[str, str]) -> str | None:
    """A link into the Retrieval explorer (#141) pre-filled with ``params`` (convention 11:
    filters only, no read), or ``None`` while ``inspect:retrieval`` isn't registered (the
    caller then omits the link)."""
    try:
        base = reverse(Tab.RETRIEVAL.url_name, args=[story_id])
    except NoReverseMatch:
        return None
    return f"{base}?{urlencode(params)}" if params else base


def memory_list_url(story_id: str, params: Mapping[str, str] | None = None) -> str:
    """The Memory tab's memory list (``inspect:memory-list``, #236), pre-filled with ``params``
    (its filters, no read)."""
    base = reverse("inspect:memory-list", args=[story_id])
    return f"{base}?{urlencode(params)}" if params else base


# The longest URL-encoded ``next`` a memory action link carries (#143); a longer explorer path
# falls back to the explorer pre-filled with the memory alone (convention 11).
ACTION_NEXT_MAX = 2000


def memory_action_url(action: str, story_id: str, memory_id: str, next_url: str) -> str:
    """The link to a memory's ``inspect:memory-<action>`` page (``quarantine``, ``release``;
    #143; ``sanitize``, #144, whose page re-asks the question ``next`` holds), carrying ``next``
    (the explorer's full path). A ``next`` over :data:`ACTION_NEXT_MAX` characters URL-encoded
    becomes ``inspect:retrieval`` with only ``memory=<id>``."""
    base = reverse(f"inspect:memory-{action}", args=[story_id, memory_id])
    if len(urlencode({"next": next_url})) - len("next=") > ACTION_NEXT_MAX:
        next_url = retrieval_url(story_id, {"memory": memory_id}) or ""
    return f"{base}?{urlencode({'next': next_url})}"


# --- the Knowledge tab's sections (#189) ---------------------------------------------------------

# The sections their stories register (#190, #192, #193): (URL name under ``inspect:``, label).
KNOWLEDGE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("beliefs", "Beliefs"),
    ("divergences", "Divergences"),
    ("exposures", "Exposures"),
)


@dataclass(frozen=True)
class SectionLink:
    """One section in a tab's sub-navigation; ``active`` for the page's own. ``unavailable``
    (World and Effects only, D3) greys it with the reason. ``name`` is the Knowledge section's
    short name (``beliefs``), or a World/Effects section's full URL name (``inspect:states``)."""

    name: str
    label: str
    url: str
    active: bool
    unavailable: Unavailable | None = None


def knowledge_sections(story_id: str, current: str | None) -> list[SectionLink]:
    """The Knowledge sections whose URL is registered, in :data:`KNOWLEDGE_SECTIONS` order
    (#64's nav rule: an unregistered one is left out); ``current`` is the page's URL name."""
    links = []
    for name, label in KNOWLEDGE_SECTIONS:
        try:
            url = reverse(f"inspect:{name}", args=[story_id])
        except NoReverseMatch:
            continue
        links.append(SectionLink(name, label, url, name == current))
    return links


# --- the World and Effects tabs' sections (#239, D3) --------------------------------------------
#
# Each section is its own URL with its own gate: (URL name, label, module | None, level | None).
# A section lands with its owning story (D3's table); until then :func:`sections` leaves it out.
Section = tuple[str, str, str | None, str | None]
WORLD_SECTIONS: tuple[Section, ...] = (
    ("inspect:states", "States", STATES, WORLD_DEFAULT_LEVEL),  # #242
    ("inspect:state-proposals", "Proposed States", STATES, WORLD_DEFAULT_LEVEL),  # #266
    ("inspect:clock", "Clock", None, None),  # #243
    ("inspect:scheduled-events", "Scheduled events", None, None),  # #244
    ("inspect:external-values", "External values", None, None),  # #248
)
EFFECTS_SECTIONS: tuple[Section, ...] = (
    ("inspect:reconciliation", "Reconciliation", None, None),  # #249
    ("inspect:effects-trail", "Effects trail", STATES, WORLD_ON),  # #255
)
TAB_SECTIONS: Mapping[Tab, tuple[Section, ...]] = {
    Tab.WORLD: WORLD_SECTIONS,
    Tab.EFFECTS: EFFECTS_SECTIONS,
}


def section_of(tab: Tab, name: str | None) -> Section | None:
    """``tab``'s registry entry for the URL name ``name``, or ``None`` (not a section)."""
    return next((entry for entry in TAB_SECTIONS.get(tab, ()) if entry[0] == name), None)


def sections(
    tab: Tab, story_id: str, current: str | None, effective: Mapping[str, Any] | None
) -> list[SectionLink]:
    """``tab``'s registered sections in registry order (#64's nav rule: an unregistered one is
    left out), ``current`` (a full URL name) marked active, each greyed by
    ``module_gate(module, effective, level)`` (an ungated section never). A tab other than World
    or Effects raises ``KeyError``."""
    links = []
    for name, label, module, level in TAB_SECTIONS[tab]:
        try:
            url = reverse(name, args=[story_id])
        except NoReverseMatch:
            continue
        unavailable = module_gate(module, effective, level) if module is not None else None
        links.append(SectionLink(name, label, url, name == current, unavailable))
    return links
