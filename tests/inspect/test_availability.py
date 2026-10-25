"""``tab_availability`` (#135): which Inspect tabs a story's effective configuration serves, and
the reasons it names (one mapping for the rules and a route's 409 ``module_off``)."""

from typing import Any

import pytest

from console.engine.client import EngineError
from console.inspect import (
    Tab,
    Unavailable,
    module_off_unavailable,
    setting,
    tab_availability,
    unavailable_for,
)
from tests.inspect.helpers import lite, structured

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


def greyed(**tabs: Unavailable) -> dict[Tab, Unavailable | None]:
    return {**ALL_ON, **{Tab(name): reason for name, reason in tabs.items()}}


CASES: list[tuple[str, dict[str, Any], dict[Tab, Unavailable | None]]] = [
    ("structured: every tab", structured(), ALL_ON),
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
        ALL_ON,
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
    assert list(found) == list(Tab)  # an entry for all seven tabs, in bar order
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
        "Predicates",
        "Ledger",
        "Scene",
    ]
    assert [t.value for t in Tab] == [
        "overview",
        "memory",
        "retrieval",
        "canon",
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
