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
- Overview, Predicates: always served (the Engine serves the predicate registry under every
  profile).

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


def _pipeline_clause(module: str, pipeline: Any) -> Unavailable:
    runs = f"; this story runs `{pipeline}`" if isinstance(pipeline, str) and pipeline else ""
    reason = (
        f"Needs the `{module}` module, which needs the structured pipeline "
        f"(`pipeline: structured`{runs})"
    )
    return Unavailable(module, SETTINGS[module], reason)


def unavailable_for(
    module: str, effective: Mapping[str, Any] | None = None, level: str | None = None
) -> Unavailable:
    """The :class:`Unavailable` naming ``module``. ``effective`` supplies the pipeline level the
    story runs (left out of the reason when unknown); for an M5 module it also supplies the
    pipeline clause, the partitions prerequisite and the value "(this story runs …)" names, in
    :func:`m5_gate`'s order, and ``level`` is the perception level asked for."""
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
    level)``, in its order) that ``data`` carries with any non-blank value (an "off"-looking one
    included) and whose :func:`m5_gate` is closed; ``None`` when every carried field is served.
    ``effective`` ``None`` (a failed read) refuses every carried field with
    :data:`GATE_UNREAD`. The view calls it before any Engine write and makes none when it
    refuses."""
    for field, (module, level) in gated.items():
        if not any(str(value).strip() for value in data.getlist(field)):
            continue
        gate = m5_gate(module, effective, level)
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
        Tab.PREDICATES: None,
        Tab.LEDGER: _ledger(effective),
        Tab.SCENE: structured,
    }


# The module a tab's rule names, for a 409 ``module_off`` without ``details.module``.
RULE_MODULES: Mapping[Tab, str] = {
    Tab.MEMORY: EPISODIC,
    Tab.RETRIEVAL: EPISODIC,
    Tab.CANON: PIPELINE,
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
# table (one row per module the Engine delivers in M4, in its ``schema.MODULES`` order), and a link
# to the Engine's capability map. Nothing is derived Console-side: ``floor`` and required-field
# repair are never in ``capabilities`` and are not shown.

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
    "budgets": "Budgets",
    "models": "Model routing",
    "repair": "Repair attempts",
    "contradiction_checking": "Contradiction checking",
    "verification": "Verification",
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


# One row per module the Engine delivers in M4, in its ``schema.MODULES`` order: (module, switch).
MODULE_ROWS: tuple[tuple[str, str], ...] = (
    ("pipeline", "pipeline"),
    ("rolling_summary", "memory.rolling_summary.enabled"),
    ("episodic", "memory.episodic.enabled"),
    ("consolidation", "memory.consolidation.enabled"),
    ("pins", "pins.enabled"),
    ("verification", "verification.level"),
    ("contradiction_checking", "verification.contradiction_checking"),
    ("commitment", "commitment.enabled"),
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

# A row's state kinds: "on" / "off" / "always on" read as words, a ``level`` verbatim, ``attempts``
# "on · N attempt(s)", ``raw`` an unexpected value verbatim in ``<code>``.
ON, OFF, ALWAYS, LEVEL, ATTEMPTS, RAW = "on", "off", "always on", "level", "attempts", "raw"


@dataclass(frozen=True)
class ModuleRow:
    """One module table row: the module, the switch path it reads, its state kind and the value
    (a level, an attempt count, or a raw value)."""

    module: str
    path: str
    kind: str
    value: Any = None

    @property
    def off(self) -> bool:
        return self.kind == OFF


def _state(module: str, value: Any) -> tuple[str, Any]:
    if module in ALWAYS_ON:
        return ALWAYS, None
    if isinstance(value, bool):
        return (ON if value else OFF), None
    if module == "repair" and isinstance(value, int):
        return (ATTEMPTS, value) if value > 0 else (OFF, None)  # the Engine's rule
    if module in ("pipeline", "verification") and isinstance(value, str) and value:
        return (OFF, None) if value == OFF else (LEVEL, value)
    return RAW, value


def module_rows(effective: Mapping[str, Any]) -> list[ModuleRow]:
    """The module table: a row per :data:`MODULE_ROWS` entry whose switch is in ``effective``
    (the Engine sends only delivered settings, so an undelivered module has no row)."""
    rows = []
    for module, path in MODULE_ROWS:
        value = setting(effective, path)
        if value is None:
            continue
        rows.append(ModuleRow(module, path, *_state(module, value)))
    return rows


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


def pipeline_dials(effective: Mapping[str, Any]) -> list[Dial]:
    """The ``render.*`` dials present in ``effective``, in :data:`RENDER_DIALS` order."""
    return [
        Dial(path, value)
        for path in RENDER_DIALS
        if (value := setting(effective, path)) is not None
    ]


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
