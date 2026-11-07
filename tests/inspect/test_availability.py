"""``tab_availability`` (#135): which Inspect tabs a story's effective configuration serves, and
the reasons it names (one mapping for the rules and a route's 409 ``module_off``); the M5 gates
(#188): ``m5_gate``, its wrappers, the level-aware ``unavailable_for`` and ``gate_refusal``; the
M6 gates (#239, D1): ``m6_gate``, ``module_gate`` and their wordings."""

from typing import Any

import pytest
from django.http import QueryDict

from console.engine.client import EngineError
from console.inspect import (
    CLOCK,
    GATE_UNREAD,
    LAZY_RESOLUTION,
    M5_MODULES,
    M6_MODULES,
    MULTI_CHARACTER,
    NESTED_BELIEFS,
    PARTITIONS,
    PERCEPTION,
    PERCEPTION_LEVELS,
    SCHEDULED_STOPS,
    SETTINGS,
    STATES,
    TESTIMONY,
    TIME_CLAIMS,
    UNREAD,
    WORLD_DEFAULT_LEVEL,
    WORLD_LEVELS,
    GateRefusal,
    Tab,
    Unavailable,
    clock_level,
    clock_served,
    gate_refusal,
    m5_gate,
    m6_gate,
    module_gate,
    module_off_unavailable,
    nested_beliefs_on,
    partitions_on,
    perception_level,
    renders_per_character,
    setting,
    states_level,
    states_served,
    tab_availability,
    unavailable_for,
)
from tests.inspect.helpers import (
    lite,
    lite_m6,
    structured,
    structured_v2,
    structured_v3,
    with_settings,
)

PIPELINE_LITE = Unavailable(
    "pipeline",
    "pipeline",
    "Needs the structured pipeline (`pipeline: structured`; this story runs `lite`)",
)
EPISODIC = Unavailable(
    "episodic",
    "memory.episodic.enabled",
    "Needs the `episodic` module (`memory.episodic.enabled` is off under this story's profile)",
)
COMMITMENT = Unavailable(
    "commitment",
    "commitment.enabled",
    "Needs the `commitment` module (`commitment.enabled` is off under this story's profile)",
)
ALL_ON: dict[Tab, Unavailable | None] = dict.fromkeys(Tab)
# The M4 recordings (``lite()``, ``structured()``) carry no M5 key, so Knowledge's partitions gate
# (#189) reads them as unread.
M4_BASE: dict[Tab, Unavailable | None] = {**ALL_ON, Tab.KNOWLEDGE: UNREAD}


def greyed(**tabs: Unavailable) -> dict[Tab, Unavailable | None]:
    return {**M4_BASE, **{Tab(name): reason for name, reason in tabs.items()}}


CASES: list[tuple[str, dict[str, Any], dict[Tab, Unavailable | None]]] = [
    ("structured v2: every tab", structured_v2(), ALL_ON),
    ("structured (M4 recording): knowledge unread", structured(), greyed()),
    (
        "lite: memory, retrieval, canon, ledger, scene",
        lite(),
        greyed(
            memory=EPISODIC,
            retrieval=EPISODIC,
            canon=PIPELINE_LITE,
            ledger=PIPELINE_LITE,
            scene=PIPELINE_LITE,
        ),
    ),
    (
        "structured, episodic off",
        structured(memory__episodic__enabled=False),
        greyed(memory=EPISODIC, retrieval=EPISODIC),
    ),
    (
        "structured, episodic switch missing counts as off",
        structured(memory__episodic__enabled=None),
        greyed(memory=EPISODIC, retrieval=EPISODIC),
    ),
    (
        "structured, episodic not a boolean true counts as off",
        structured(memory__episodic__enabled="yes"),
        greyed(memory=EPISODIC, retrieval=EPISODIC),
    ),
    (
        "structured, commitment off: ledger names commitment",
        structured(commitment__enabled=False),
        greyed(ledger=COMMITMENT),
    ),
    (
        "verification off, commitment on: ledger and canon enabled",
        structured(verification__level="off"),
        greyed(),
    ),
    (
        "lite with commitment on: ledger names pipeline",
        lite(commitment__enabled=True),
        greyed(
            memory=EPISODIC,
            retrieval=EPISODIC,
            canon=PIPELINE_LITE,
            ledger=PIPELINE_LITE,
            scene=PIPELINE_LITE,
        ),
    ),
    (
        "lite with episodic on: memory and retrieval enabled",
        lite(memory__episodic__enabled=True),
        greyed(canon=PIPELINE_LITE, ledger=PIPELINE_LITE, scene=PIPELINE_LITE),
    ),
    (
        "both pipeline and episodic off: memory names episodic",
        lite(),
        greyed(
            memory=EPISODIC,
            retrieval=EPISODIC,
            canon=PIPELINE_LITE,
            ledger=PIPELINE_LITE,
            scene=PIPELINE_LITE,
        ),
    ),
]


@pytest.mark.parametrize(
    ("config", "expected"), [(c, e) for _, c, e in CASES], ids=[name for name, _, _ in CASES]
)
def test_tab_availability(config: dict[str, Any], expected: dict[Tab, Unavailable | None]) -> None:
    found = tab_availability(config["effective"])
    assert list(found) == list(Tab)  # an entry for all ten tabs, in bar order
    assert found == expected


def test_overview_and_predicates_are_always_served() -> None:
    empty: dict[str, Any] = {}
    for effective in (empty, lite()["effective"], structured()["effective"]):
        found = tab_availability(effective)
        assert found[Tab.OVERVIEW] is None
        assert found[Tab.PREDICATES] is None


def test_a_missing_pipeline_drops_the_level_clause() -> None:
    found = tab_availability(structured(pipeline=None)["effective"])
    reason = found[Tab.CANON]
    assert reason == Unavailable(
        "pipeline", "pipeline", "Needs the structured pipeline (`pipeline: structured`)"
    )
    assert found[Tab.LEDGER] == reason


def test_tab_labels_and_bar_order() -> None:
    assert [t.label for t in Tab] == [
        "Overview",
        "Memory",
        "Retrieval",
        "Canon",
        "Knowledge",
        "World",
        "Effects",
        "Predicates",
        "Ledger",
        "Scene",
    ]
    assert [t.value for t in Tab] == [
        "overview",
        "memory",
        "retrieval",
        "canon",
        "knowledge",
        "world",
        "effects",
        "predicates",
        "ledger",
        "scene",
    ]


def test_setting_reads_dotted_paths() -> None:
    effective = {"memory": {"episodic": {"enabled": True}}, "pipeline": "lite"}
    assert setting(effective, "memory.episodic.enabled") is True
    assert setting(effective, "pipeline") == "lite"
    assert setting(effective, "memory.consolidation.enabled") is None
    assert setting(effective, "pipeline.level") is None  # a step that is not an object


def test_reason_mapping() -> None:
    assert unavailable_for("consolidation") == Unavailable(
        "consolidation",
        "memory.consolidation.enabled",
        "Needs the `consolidation` module (`memory.consolidation.enabled` is off under this "
        "story's profile)",
    )
    assert unavailable_for("telepathy") == Unavailable(
        "telepathy", None, "Needs the `telepathy` module (off under this story's configuration)"
    )


def module_off(**details: Any) -> EngineError:
    return EngineError("module_off", "off", details, 409)


def test_module_off_names_details_module() -> None:
    assert module_off_unavailable(module_off(module="episodic"), Tab.CANON) == EPISODIC
    pipeline = module_off_unavailable(module_off(module="pipeline"), Tab.CANON, lite()["effective"])
    assert pipeline == PIPELINE_LITE
    unknown = module_off_unavailable(module_off(module="mystery"), Tab.MEMORY)
    assert unknown.setting is None
    assert unknown.module == "mystery"


def test_module_off_without_a_module_names_the_tabs_rule() -> None:
    assert module_off_unavailable(module_off(), Tab.MEMORY) == EPISODIC
    assert module_off_unavailable(module_off(module=""), Tab.LEDGER) == COMMITMENT
    assert module_off_unavailable(module_off(), Tab.SCENE).module == "pipeline"
    # A tab with no rule names no module.
    assert module_off_unavailable(module_off(), Tab.PREDICATES).module == ""


# --- the M5 gates (#188, C6) ----------------------------------------------------------------------

V2 = structured_v2()["effective"]


def v2_with(**settings: Any) -> dict[str, Any]:
    """The v2 ``effective`` with ``settings`` laid over it (``__`` stands for ``.``)."""
    dotted = {path.replace("__", "."): value for path, value in settings.items()}
    effective: dict[str, Any] = with_settings(structured_v2(), dotted)["effective"]
    return effective


def lite_m5(**settings: Any) -> dict[str, Any]:
    """Lite with every M5 switch forced on, then ``settings`` laid over it."""
    on = {
        "character_knowledge.partitions": True,
        "character_knowledge.nested_beliefs.enabled": True,
        "character_knowledge.lazy_resolution": True,
        "character_knowledge.testimony": "model",
        "perception.level": "full",
        "multi_character.renders": "per_character",
    }
    on.update({path.replace("__", "."): value for path, value in settings.items()})
    effective: dict[str, Any] = with_settings(lite(), on)["effective"]
    return effective


PARTITIONS_OFF = Unavailable(
    "partitions",
    "character_knowledge.partitions",
    "Needs the `partitions` module (`character_knowledge.partitions` is off under this story's "
    "profile)",
)


def lite_clause(module: str) -> Unavailable:
    return Unavailable(
        module,
        SETTINGS[module],
        f"Needs the `{module}` module, which needs the structured pipeline "
        "(`pipeline: structured`; this story runs `lite`)",
    )


def own(module: str, reason: str) -> Unavailable:
    return Unavailable(module, SETTINGS[module], reason)


PERCEPTION_FULL_CO = own(
    "perception", "Needs `perception.level: full` (this story runs `co_location`)"
)

GATE_CASES: list[tuple[str, str, dict[str, Any] | None, str | None, Unavailable | None]] = [
    ("partitions on", PARTITIONS, V2, None, None),
    (
        "partitions off",
        PARTITIONS,
        v2_with(character_knowledge__partitions=False),
        None,
        PARTITIONS_OFF,
    ),
    (
        "partitions not True",
        PARTITIONS,
        v2_with(character_knowledge__partitions="yes"),
        None,
        PARTITIONS_OFF,
    ),
    (
        "partitions key missing",
        PARTITIONS,
        v2_with(character_knowledge__partitions=None),
        None,
        UNREAD,
    ),
    ("pipeline missing", PARTITIONS, v2_with(pipeline=None), None, UNREAD),
    ("effective None", PARTITIONS, None, None, UNREAD),
    ("lite, partitions forced on", PARTITIONS, lite_m5(), None, lite_clause("partitions")),
    ("lite, multi_character", MULTI_CHARACTER, lite_m5(), None, lite_clause("multi_character")),
    ("lite M4-shaped: switch missing", PARTITIONS, lite()["effective"], None, UNREAD),
    (
        "pipeline not a string: clause without the runs part",
        TESTIMONY,
        v2_with(pipeline=5),
        None,
        own(
            "testimony",
            "Needs the `testimony` module, which needs the structured pipeline "
            "(`pipeline: structured`)",
        ),
    ),
    (
        "perception co_location, default level",
        PERCEPTION,
        v2_with(perception__level="co_location"),
        None,
        None,
    ),
    (
        "perception co_location at full",
        PERCEPTION,
        v2_with(perception__level="co_location"),
        "full",
        PERCEPTION_FULL_CO,
    ),
    ("perception full at full", PERCEPTION, V2, "full", None),
    (
        "perception none",
        PERCEPTION,
        v2_with(perception__level="none"),
        None,
        own(
            "perception",
            "Needs `perception.level` at `co_location` or above (this story runs `none`)",
        ),
    ),
    (
        "perception unexpected string, default level",
        PERCEPTION,
        v2_with(perception__level="weird"),
        None,
        own(
            "perception",
            "Needs `perception.level` at `co_location` or above (this story runs `weird`)",
        ),
    ),
    (
        "perception unexpected string at full",
        PERCEPTION,
        v2_with(perception__level="weird"),
        "full",
        own("perception", "Needs `perception.level: full` (this story runs `weird`)"),
    ),
    (
        "perception non-string named as JSON",
        PERCEPTION,
        v2_with(perception__level=True),
        None,
        own(
            "perception",
            "Needs `perception.level` at `co_location` or above (this story runs `true`)",
        ),
    ),
    (
        "testimony defaulted",
        TESTIMONY,
        v2_with(character_knowledge__testimony="defaulted"),
        None,
        None,
    ),
    (
        "testimony off",
        TESTIMONY,
        v2_with(character_knowledge__testimony="off"),
        None,
        own(
            "testimony",
            "Needs `character_knowledge.testimony` at `model` or `defaulted` "
            "(this story runs `off`)",
        ),
    ),
    (
        "multi_character combined",
        MULTI_CHARACTER,
        v2_with(multi_character__renders="combined"),
        None,
        own(
            "multi_character",
            "Needs `multi_character.renders: per_character` (this story runs `combined`)",
        ),
    ),
    (
        "nested beliefs off",
        NESTED_BELIEFS,
        v2_with(character_knowledge__nested_beliefs__enabled=False),
        None,
        own(
            "nested_beliefs",
            "Needs the `nested_beliefs` module (`character_knowledge.nested_beliefs.enabled` is "
            "off under this story's profile)",
        ),
    ),
    (
        "lazy resolution off",
        LAZY_RESOLUTION,
        v2_with(character_knowledge__lazy_resolution=False),
        None,
        own(
            "lazy_resolution",
            "Needs the `lazy_resolution` module (`character_knowledge.lazy_resolution` is off "
            "under this story's profile)",
        ),
    ),
    (
        "testimony model, partitions off",
        TESTIMONY,
        v2_with(character_knowledge__partitions=False),
        None,
        PARTITIONS_OFF,
    ),
    (
        "perception full, partitions off",
        PERCEPTION,
        v2_with(character_knowledge__partitions=False),
        "full",
        PARTITIONS_OFF,
    ),
    (
        "nested on, partitions off",
        NESTED_BELIEFS,
        v2_with(character_knowledge__partitions=False),
        None,
        PARTITIONS_OFF,
    ),
    (
        "lazy on, partitions off",
        LAZY_RESOLUTION,
        v2_with(character_knowledge__partitions=False),
        None,
        PARTITIONS_OFF,
    ),
    (
        "nested on, partitions key missing",
        NESTED_BELIEFS,
        v2_with(character_knowledge__partitions=None),
        None,
        UNREAD,
    ),
    (
        "nested beliefs not an object",
        NESTED_BELIEFS,
        v2_with(character_knowledge__nested_beliefs=True),
        None,
        UNREAD,
    ),
    (
        "an explicit null reads as missing",
        MULTI_CHARACTER,
        with_settings(structured_v2(), {})["effective"] | {"multi_character": None},
        None,
        UNREAD,
    ),
    (
        "multi_character needs no partitions",
        MULTI_CHARACTER,
        v2_with(character_knowledge__partitions=False),
        None,
        None,
    ),
]


@pytest.mark.parametrize(
    ("module", "effective", "level", "expected"),
    [case[1:] for case in GATE_CASES],
    ids=[case[0] for case in GATE_CASES],
)
def test_m5_gate(
    module: str,
    effective: dict[str, Any] | None,
    level: str | None,
    expected: Unavailable | None,
) -> None:
    assert m5_gate(module, effective, level) == expected


def test_v2_fixture_serves_every_m5_module_at_every_level() -> None:
    assert M5_MODULES == (
        "partitions",
        "nested_beliefs",
        "lazy_resolution",
        "testimony",
        "perception",
        "multi_character",
    )
    for module in M5_MODULES:
        for level in (None, *PERCEPTION_LEVELS):
            assert m5_gate(module, V2, level) is None, (module, level)


def test_m5_gate_refuses_an_unknown_module_and_level() -> None:
    with pytest.raises(KeyError):
        m5_gate("telepathy", V2)
    with pytest.raises(ValueError, match="perception level"):
        m5_gate(PERCEPTION, V2, "partial")


def test_gate_unread_reason() -> None:
    assert Unavailable("", None, GATE_UNREAD) == UNREAD
    assert GATE_UNREAD == "Couldn't read this story's configuration — reload"


WRAPPER_CASES: list[tuple[str, dict[str, Any] | None, bool, bool, bool]] = [
    ("v2", V2, True, True, True),
    ("partitions off", v2_with(character_knowledge__partitions=False), False, False, True),
    ("nested off", v2_with(character_knowledge__nested_beliefs__enabled=False), True, False, True),
    ("combined", v2_with(multi_character__renders="combined"), True, True, False),
    ("lite forced on", lite_m5(), False, False, False),
    ("unread", None, False, False, False),
]


@pytest.mark.parametrize(
    ("effective", "partitions", "nested", "per_character"),
    [case[1:] for case in WRAPPER_CASES],
    ids=[case[0] for case in WRAPPER_CASES],
)
def test_gate_wrappers(
    effective: dict[str, Any] | None, partitions: bool, nested: bool, per_character: bool
) -> None:
    assert partitions_on(effective) is partitions
    assert nested_beliefs_on(effective) is nested
    assert renders_per_character(effective) is per_character


@pytest.mark.parametrize(
    ("effective", "expected"),
    [
        (V2, "full"),
        (lite_m5(perception__level="co_location"), "co_location"),  # whatever the pipeline
        (v2_with(perception__level="weird"), "weird"),
        (v2_with(perception__level=None), None),
        (v2_with(perception__level=3), None),
        (None, None),
    ],
)
def test_perception_level_reads_the_raw_value(
    effective: dict[str, Any] | None, expected: str | None
) -> None:
    assert perception_level(effective) == expected


PLAIN_CASES: list[tuple[str, str | None, str]] = [
    (
        PARTITIONS,
        None,
        "Needs the `partitions` module (`character_knowledge.partitions` is off under this "
        "story's profile)",
    ),
    (PERCEPTION, None, "Needs `perception.level` at `co_location` or above"),
    (PERCEPTION, "full", "Needs `perception.level: full`"),
    (MULTI_CHARACTER, None, "Needs `multi_character.renders: per_character`"),
    (TESTIMONY, None, "Needs `character_knowledge.testimony` at `model` or `defaulted`"),
]


@pytest.mark.parametrize(("module", "level", "reason"), PLAIN_CASES)
def test_unavailable_for_without_effective_is_the_plain_wording(
    module: str, level: str | None, reason: str
) -> None:
    assert unavailable_for(module, None, level) == own(module, reason)
    assert unavailable_for(module, v2_with(pipeline=None), level) == own(module, reason)
    assert "this story runs" not in reason


def test_m4_reasons_are_unchanged_lite_or_not() -> None:
    for effective in (None, lite()["effective"], structured()["effective"], V2, lite_m5()):
        assert unavailable_for("episodic", effective) == EPISODIC
        assert unavailable_for("commitment", effective) == COMMITMENT
    assert unavailable_for("pipeline", lite_m5()) == PIPELINE_LITE


MODULE_OFF_CASES: list[tuple[str, EngineError, dict[str, Any] | None, str | None, Unavailable]] = [
    (
        "partitions on structured",
        module_off(module="partitions"),
        v2_with(character_knowledge__partitions=False),
        None,
        PARTITIONS_OFF,
    ),
    (
        "partitions on lite",
        module_off(module="partitions"),
        lite_m5(),
        None,
        lite_clause("partitions"),
    ),
    (
        "multi_character on lite",
        module_off(module="multi_character"),
        lite_m5(),
        None,
        lite_clause("multi_character"),
    ),
    (
        "testimony, partitions off",
        module_off(module="testimony"),
        v2_with(character_knowledge__partitions=False),
        None,
        PARTITIONS_OFF,
    ),
    (
        "perception at full on co_location",
        module_off(module="perception"),
        v2_with(perception__level="co_location"),
        "full",
        PERCEPTION_FULL_CO,
    ),
    (
        "stale: the config shows it served",
        module_off(module="perception"),
        V2,
        "full",
        own("perception", "Needs `perception.level: full`"),
    ),
]


@pytest.mark.parametrize(
    ("error", "effective", "level", "expected"),
    [case[1:] for case in MODULE_OFF_CASES],
    ids=[case[0] for case in MODULE_OFF_CASES],
)
def test_module_off_reads_the_same_as_the_gate(
    error: EngineError,
    effective: dict[str, Any] | None,
    level: str | None,
    expected: Unavailable,
) -> None:
    # ``tab`` omitted: an M5 caller outside an Inspect tab.
    assert module_off_unavailable(error, effective=effective, level=level) == expected
    module = error.details["module"]
    gate = m5_gate(module, effective, level)
    if gate is not None:
        assert gate == expected
    assert module_off_unavailable(error, Tab.OVERVIEW, effective, level=level) == expected


def test_module_off_without_a_module_or_tab_names_no_module() -> None:
    assert module_off_unavailable(module_off()).module == ""


GATED: dict[str, tuple[str, str | None]] = {
    "whisper_to": (PERCEPTION, "full"),
    "renders": (MULTI_CHARACTER, None),
}

REFUSAL_CASES: list[tuple[str, str, dict[str, Any] | None, GateRefusal | None]] = [
    (
        "a carried field at full on co_location",
        "whisper_to=x",
        v2_with(perception__level="co_location"),
        GateRefusal("whisper_to", PERCEPTION_FULL_CO),
    ),
    (
        "an off-looking value still counts",
        "whisper_to=off",
        v2_with(perception__level="co_location"),
        GateRefusal("whisper_to", PERCEPTION_FULL_CO),
    ),
    (
        "any of a repeated field",
        "whisper_to=&whisper_to=y",
        v2_with(perception__level="co_location"),
        GateRefusal("whisper_to", PERCEPTION_FULL_CO),
    ),
    ("a blank value", "whisper_to=++&renders=", v2_with(perception__level="co_location"), None),
    ("an uncarried field", "other=1", None, None),
    ("effective None", "renders=a", None, GateRefusal("renders", UNREAD)),
    (
        "gated order: first carried closed field",
        "renders=a&whisper_to=x",
        None,
        GateRefusal("whisper_to", UNREAD),
    ),
    ("an open gate", "whisper_to=x&renders=a", V2, None),
    (
        "the first open, the second closed",
        "whisper_to=x&renders=a",
        v2_with(multi_character__renders="combined"),
        GateRefusal(
            "renders",
            own(
                "multi_character",
                "Needs `multi_character.renders: per_character` (this story runs `combined`)",
            ),
        ),
    ),
]


@pytest.mark.parametrize(
    ("posted", "effective", "expected"),
    [case[1:] for case in REFUSAL_CASES],
    ids=[case[0] for case in REFUSAL_CASES],
)
def test_gate_refusal(
    posted: str, effective: dict[str, Any] | None, expected: GateRefusal | None
) -> None:
    refusal = gate_refusal(QueryDict(posted), effective, GATED)
    assert refusal == expected
    if refusal is not None:
        assert refusal.reason == refusal.unavailable.reason


# --- the M6 gates (#239, D1) ----------------------------------------------------------------------

V3 = structured_v3()["effective"]


def v3_with(**settings: Any) -> dict[str, Any]:
    """The v3 ``effective`` with ``settings`` laid over it (``__`` stands for ``.``)."""
    dotted = {path.replace("__", "."): value for path, value in settings.items()}
    effective: dict[str, Any] = with_settings(structured_v3(), dotted)["effective"]
    return effective


def lite_world(**paths: Any) -> dict[str, Any]:
    effective: dict[str, Any] = lite_m6(**paths)["effective"]
    return effective


STATES_OFF = own("states", "Needs `states.level` at `prompt_only` or above (this story runs `off`)")
STATES_ON_PROMPT = own("states", "Needs `states.level: on` (this story runs `prompt_only`)")
CLOCK_OFF = own("clock", "Needs `clock.level` at `prompt_only` or above (this story runs `off`)")
STATES_ON_LITE = own(
    "states",
    "Needs `states.level: on`, which needs the structured pipeline "
    "(`pipeline: structured`; this story runs `lite`)",
)
STOPS_OFF = own(
    "scheduled_stops",
    "Needs the `scheduled_stops` module (`clock.scheduled_stops` is off under this story's "
    "profile)",
)

M6_GATE_CASES: list[tuple[str, str, dict[str, Any] | None, str | None, Unavailable | None]] = [
    ("states on, structured, default level", STATES, V3, None, None),
    ("states on, structured, at on", STATES, V3, "on", None),
    (
        "states prompt_only on lite",
        STATES,
        lite_world(**{"states.level": "prompt_only"}),
        None,
        None,
    ),
    (
        "states prompt_only on lite, at on: the pipeline clause",
        STATES,
        lite_world(**{"states.level": "prompt_only"}),
        "on",
        STATES_ON_LITE,
    ),
    ("states off, structured", STATES, v3_with(states__level="off"), None, STATES_OFF),
    ("states off, lite", STATES, lite_world(), None, STATES_OFF),
    (
        "states prompt_only at on, structured",
        STATES,
        v3_with(states__level="prompt_only"),
        "on",
        STATES_ON_PROMPT,
    ),
    (
        "an unexpected value is named raw",
        STATES,
        v3_with(states__level="partial"),
        None,
        own(
            "states",
            "Needs `states.level` at `prompt_only` or above (this story runs `partial`)",
        ),
    ),
    (
        "a non-string value is named as JSON",
        CLOCK,
        v3_with(clock__level=True),
        None,
        own("clock", "Needs `clock.level` at `prompt_only` or above (this story runs `true`)"),
    ),
    ("states key missing", STATES, v3_with(states__level=None), None, UNREAD),
    ("pipeline missing", STATES, v3_with(pipeline=None), None, UNREAD),
    ("effective None", STATES, None, None, UNREAD),
    ("an M5-shaped config is unread", CLOCK, V2, None, UNREAD),
    ("clock off on lite", CLOCK, lite_world(), None, CLOCK_OFF),
    (
        "pipeline not a string: the clause without the runs part",
        STATES,
        v3_with(pipeline=5),
        "on",
        own(
            "states",
            "Needs `states.level: on`, which needs the structured pipeline "
            "(`pipeline: structured`)",
        ),
    ),
    (
        "a flag true with the clock off: the clock's reason",
        SCHEDULED_STOPS,
        v3_with(clock__level="off"),
        None,
        CLOCK_OFF,
    ),
    (
        "a flag false with the clock on: the boolean wording",
        SCHEDULED_STOPS,
        v3_with(clock__scheduled_stops=False),
        None,
        STOPS_OFF,
    ),
    (
        "a non-boolean flag: the boolean wording",
        SCHEDULED_STOPS,
        v3_with(clock__scheduled_stops="yes"),
        None,
        STOPS_OFF,
    ),
    (
        "a flag on lite at any level: its pipeline clause",
        TIME_CLAIMS,
        lite_world(**{"clock.level": "prompt_only", "clock.time_claims": True}),
        "prompt_only",
        lite_clause("time_claims"),
    ),
    ("a flag's level is ignored", TIME_CLAIMS, V3, "prompt_only", None),
    ("a flag with clock.level missing", TIME_CLAIMS, v3_with(clock__level=None), None, UNREAD),
]


@pytest.mark.parametrize(
    ("module", "effective", "level", "expected"),
    [case[1:] for case in M6_GATE_CASES],
    ids=[case[0] for case in M6_GATE_CASES],
)
def test_m6_gate(
    module: str,
    effective: dict[str, Any] | None,
    level: str | None,
    expected: Unavailable | None,
) -> None:
    assert m6_gate(module, effective, level) == expected
    assert module_gate(module, effective, level) == expected
    if expected is not None and expected is not UNREAD:
        # unavailable_for words a closed gate the same way (a 409 module_off reads as the gate).
        assert unavailable_for(module, effective, level) == expected


def test_m6_wrappers_and_plain_readers() -> None:
    assert [states_served(V3), states_served(V3, "on"), clock_served(V3, "on")] == [True] * 3
    prompt = lite_world(**{"states.level": "prompt_only", "clock.level": "prompt_only"})
    assert [states_served(prompt), states_served(prompt, "on")] == [True, False]
    assert [clock_served(prompt), clock_served(prompt, "on")] == [True, False]
    assert [states_served(None), clock_served(lite_world())] == [False, False]
    assert (states_level(V3), clock_level(V3)) == ("on", "on")
    # Plain readers, not gates: the raw string whatever the pipeline, None otherwise.
    assert states_level(lite_world(**{"states.level": "weird"})) == "weird"
    assert (states_level(V2), clock_level(None)) == (None, None)
    assert clock_level(v3_with(clock__level=3)) is None


def test_m6_constants() -> None:
    assert M6_MODULES == ("clock", "time_claims", "scheduled_stops", "states")
    assert WORLD_LEVELS == ("off", "prompt_only", "on")
    assert WORLD_DEFAULT_LEVEL == "prompt_only"
    assert [SETTINGS[m] for m in M6_MODULES] == [
        "clock.level",
        "clock.time_claims",
        "clock.scheduled_stops",
        "states.level",
    ]


@pytest.mark.parametrize("module", ["states", "clock"])
@pytest.mark.parametrize(
    ("effective", "label"),
    [
        (v3_with(states__level="off", clock__level="off"), "structured"),
        (lite_world(), "lite"),
    ],
)
def test_m6_module_off_reads_the_same_as_the_gate(
    module: str, effective: dict[str, Any], label: str
) -> None:
    error = module_off(module=module)
    gate = m6_gate(module, effective)
    assert gate is not None
    assert gate == (STATES_OFF if module == "states" else CLOCK_OFF)
    assert module_off_unavailable(error, Tab.WORLD, effective) == gate
    assert module_off_unavailable(error, effective=effective) == gate


def test_m6_module_off_without_effective_is_the_plain_wording() -> None:
    plain_states = own("states", "Needs `states.level` at `prompt_only` or above")
    assert module_off_unavailable(module_off(module="states")) == plain_states
    assert unavailable_for(STATES, v3_with(pipeline=None)) == plain_states
    assert unavailable_for(STATES, None, "on") == own("states", "Needs `states.level: on`")
    assert unavailable_for(CLOCK, None) == own(
        "clock", "Needs `clock.level` at `prompt_only` or above"
    )
    assert unavailable_for(SCHEDULED_STOPS, None) == STOPS_OFF
    # A switch missing while pipeline is present: the plain wording too.
    assert unavailable_for(STATES, v3_with(states__level=None)) == plain_states
    # A flag whose clock.level is missing: its plain (boolean) wording.
    assert unavailable_for(TIME_CLAIMS, v3_with(clock__level=None)) == own(
        "time_claims",
        "Needs the `time_claims` module (`clock.time_claims` is off under this story's profile)",
    )
    # A stale 409 (the config shows the module served): the plain wording.
    assert module_off_unavailable(module_off(module="states"), Tab.WORLD, V3) == plain_states
    for reason in (plain_states.reason, STOPS_OFF.reason):
        assert "this story runs" not in reason


def test_m6_module_off_at_the_sections_level() -> None:
    effective = v3_with(states__level="prompt_only")
    error = module_off(module="states")
    assert module_off_unavailable(error, Tab.EFFECTS, effective, level="on") == STATES_ON_PROMPT
    assert module_off_unavailable(module_off(), Tab.WORLD, effective).reason == (
        "Needs a module that is off under this story's configuration"
    )


M6_GATED: dict[str, tuple[str, str | None]] = {
    "whisper_to": (PERCEPTION, "full"),
    "value": (STATES, None),
    "planned": (CLOCK, None),
}


@pytest.mark.parametrize(
    ("posted", "effective", "expected"),
    [
        ("value=12", v3_with(states__level="off"), GateRefusal("value", STATES_OFF)),
        ("value=12", lite_world(), GateRefusal("value", STATES_OFF)),
        ("planned=130", lite_world(), GateRefusal("planned", CLOCK_OFF)),
        (
            "whisper_to=x&value=1",
            v3_with(perception__level="co_location", states__level="off"),
            GateRefusal("whisper_to", PERCEPTION_FULL_CO),
        ),
        ("value=12", None, GateRefusal("value", UNREAD)),
        # Complement: an open gate, and a blank value.
        ("value=12&planned=1", V3, None),
        ("value=+&planned=", lite_world(), None),
    ],
    ids=[
        "a states field on an off story",
        "a states field on lite",
        "a clock field on lite",
        "an M5 field first, unchanged",
        "effective None",
        "open gates",
        "blank values",
    ],
)
def test_gate_refusal_covers_m6_fields(
    posted: str, effective: dict[str, Any] | None, expected: GateRefusal | None
) -> None:
    assert gate_refusal(QueryDict(posted), effective, M6_GATED) == expected


def test_module_gate_routes_and_refuses() -> None:
    off = v3_with(character_knowledge__partitions=False, states__level="off")
    assert module_gate(PARTITIONS, off) == m5_gate(PARTITIONS, off) == PARTITIONS_OFF
    assert module_gate(STATES, off) == m6_gate(STATES, off) == STATES_OFF
    for unknown in ("episodic", "pipeline", "mystery"):
        with pytest.raises(KeyError):
            module_gate(unknown, V3)
        with pytest.raises(KeyError):
            m6_gate(unknown, V3)
    with pytest.raises(KeyError):
        m6_gate(PARTITIONS, V3)
    for bad in ("full", "off", "co_location"):
        with pytest.raises(ValueError, match="not an M6 gate level"):
            module_gate(STATES, V3, bad)
        with pytest.raises(ValueError, match="not an M6 gate level"):
            unavailable_for(STATES, V3, bad)
    with pytest.raises(ValueError, match="not an M6 gate level"):
        m6_gate(TIME_CLAIMS, V3, "full")  # a flag validates level, then ignores it
    # M5 level validation is unchanged: a world level is not a perception level.
    with pytest.raises(ValueError, match="not a perception level"):
        module_gate(PERCEPTION, V3, "on")


def test_v3_fixture_serves_every_module() -> None:
    for module in M6_MODULES:
        assert m6_gate(module, V3, "on") is None
        assert m6_gate(module, V3) is None
    for module in M5_MODULES:
        for level in (None, *PERCEPTION_LEVELS[1:]):
            assert m5_gate(module, V3, level) == m5_gate(module, V2, level) is None
    assert tab_availability(V3) == ALL_ON


def test_m6_leaves_the_m4_and_m5_reasons_unchanged() -> None:
    for effective in (None, V2, V3, lite_world()):
        assert unavailable_for("episodic", effective) == EPISODIC
        assert unavailable_for("commitment", effective) == COMMITMENT
    assert unavailable_for("pipeline", lite_world()) == PIPELINE_LITE
    assert m5_gate(PARTITIONS, lite_world()) == UNREAD  # Lite has no M5 keys


# --- #294: ``lite_rewrites`` (E5) ----------------------------------------------------------------

MAIN_REASONS = {
    "states": Unavailable(
        "states", "states.level", "Needs `states.level` at `prompt_only` or above"
    ),
    "clock": Unavailable("clock", "clock.level", "Needs `clock.level` at `prompt_only` or above"),
    "episodic": Unavailable(
        "episodic",
        "memory.episodic.enabled",
        "Needs the `episodic` module (`memory.episodic.enabled` is off under this story's profile)",
    ),
    "commitment": Unavailable(
        "commitment",
        "commitment.enabled",
        "Needs the `commitment` module (`commitment.enabled` is off under this story's profile)",
    ),
    "perception": Unavailable(
        "perception", "perception.level", "Needs `perception.level` at `co_location` or above"
    ),
}


def test_lite_rewrites_wording() -> None:
    text = "History rewrites need a planner for this story's pipeline (`lite_rewrites` is off)"
    expected = Unavailable("lite_rewrites", None, text)
    assert unavailable_for("lite_rewrites") == expected
    assert unavailable_for("lite_rewrites", V2) == expected


@pytest.mark.parametrize("module", sorted(MAIN_REASONS))
def test_other_wordings_are_as_on_main(module: str) -> None:
    assert unavailable_for(module) == MAIN_REASONS[module]
