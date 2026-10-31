"""The Inspect Overview (#151): the header line, the pipeline, the capabilities the Engine reports,
the module table and the capability-map link, all from the shell's one effective-config read."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.inspect import (
    CAPABILITY_MAP_URL,
    MODULE_ROWS,
    capability_label,
    module_rows,
    overview_fingerprint,
)
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import (
    EFFECTIVE,
    FIXTURES,
    STORY,
    STORY_PATH,
    lite,
    structured,
    structured_v2,
    with_settings,
)
from tests.play.helpers import story_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/"
MAP_LABEL = "Capability map (Engine docs, main)"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def overview(client: Client, engine: FakeEngine, body: dict[str, Any]) -> str:
    engine.respond("GET", EFFECTIVE, 200, json=body)
    response = client.get(URL)
    assert response.status_code == 200
    return response.content.decode()


def capability_items(html: str) -> list[str]:
    found = re.search(r'<ul class="capabilities">(.*?)</ul>', html, re.S)
    assert found is not None
    return [item.strip() for item in re.findall(r"<li>(.*?)</li>", found.group(1), re.S)]


def module_table(html: str) -> dict[str, tuple[str, str, str]]:
    """``module → (row class, state cell, path cell)`` in table order."""
    rows = re.findall(
        r'<tr class="module-(\w+)( muted)?">\s*<td>\w+</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>',
        html,
        re.S,
    )
    return {module: (muted.strip(), state, path) for module, muted, state, path in rows}


def with_capabilities(body: dict[str, Any], values: list[str]) -> dict[str, Any]:
    return {**body, "capabilities": values}


# --- capabilities ---------------------------------------------------------------------------------


def test_structured_config_renders_capability_labels_and_module_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = overview(operator_client, engine, structured())
    assert capability_items(html) == [
        '<span title="structured">Structured pipeline</span>',
        '<span title="episodic">Episodic memory</span>',
        '<span title="consolidation">Consolidation</span>',
        '<span title="verification:entailment">Verification (entailment)</span>',
        '<span title="contradiction_checking">Contradiction checking</span>',
        '<span title="commitment">Commitment</span>',
        '<span title="repair">Repair attempts</span>',
        '<span title="pins">Pinned memories</span>',
        '<span title="polish">Polish</span>',
    ]
    rows = module_table(html)
    assert rows["pipeline"] == ("", "structured", "<code>pipeline</code>")
    assert rows["repair"] == ("", "on · 1 attempt", "<code>repair.max_attempts</code>")
    assert rows["rolling_summary"][:2] == ("muted", "off")
    assert rows["verification"][1] == "entailment"
    assert rows["budgets"][1] == "always on"
    assert rows["models"][1] == "always on"
    # One read: the shell's effective-config and story reads, nothing else.
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
    ]


def test_pipeline_first_then_engine_order_and_unknown_values_as_code(
    operator_client: Client, engine: FakeEngine
) -> None:
    values = [
        "budgets",
        "contradiction_checking",
        "episodic",
        "frobnicate",
        "models",
        "mystery:high",
        "repair",
        "structured",
        "verification:entailment",
    ]
    html = overview(operator_client, engine, with_capabilities(structured(), values))
    assert capability_items(html) == [
        '<span title="structured">Structured pipeline</span>',
        '<span title="budgets">Budgets (always on)</span>',
        '<span title="contradiction_checking">Contradiction checking</span>',
        '<span title="episodic">Episodic memory</span>',
        "<code>frobnicate</code>",
        '<span title="models">Model routing (always on)</span>',
        "<code>mystery:high</code>",
        '<span title="repair">Repair attempts</span>',
        '<span title="verification:entailment">Verification (entailment)</span>',
    ]


def test_no_capability_is_listed_that_the_response_did_not_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = overview(operator_client, engine, with_capabilities(structured(), ["episodic"]))
    assert capability_items(html) == ['<span title="episodic">Episodic memory</span>']
    assert "floor" not in html.lower()
    assert "Structured pipeline<" not in html  # the pipeline level isn't derived from effective


def test_lite_story_shows_its_pipeline_on_its_own_line_above_the_capabilities(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = overview(operator_client, engine, lite())
    line = '<p class="overview-pipeline"><code>pipeline</code>: lite</p>'
    assert line in html
    assert html.index(line) < html.index("<h2>Capabilities</h2>")
    assert "(unused under lite)" in html
    assert html.count("(unused under lite)") == 1


# --- module table ---------------------------------------------------------------------------------


ENGINE_ORDER = [
    "pipeline",
    "rolling_summary",
    "episodic",
    "consolidation",
    "pins",
    "partitions",
    "nested_beliefs",
    "lazy_resolution",
    "testimony",
    "perception",
    "verification",
    "contradiction_checking",
    "commitment",
    "multi_character",
    "repair",
    "polish",
    "budgets",
    "models",
]
M5_ROWS = {
    "partitions",
    "nested_beliefs",
    "lazy_resolution",
    "testimony",
    "perception",
    "multi_character",
}


def test_module_rows_follow_module_rows_order() -> None:
    assert [module for module, _ in MODULE_ROWS] == ENGINE_ORDER


def test_m5_module_rows_follow_engine_order(operator_client: Client, engine: FakeEngine) -> None:
    rows = module_table(overview(operator_client, engine, structured_v2()))
    assert list(rows) == ENGINE_ORDER
    assert [path for _, _, path in rows.values()] == [
        f"<code>{path}</code>" for _, path in MODULE_ROWS
    ]
    assert rows["partitions"] == ("", "on", "<code>character_knowledge.partitions</code>")
    assert rows["nested_beliefs"][:2] == ("", "on")
    assert rows["lazy_resolution"][:2] == ("", "on")
    assert rows["testimony"][:2] == ("", "model")
    assert rows["perception"][:2] == ("", "full")
    assert rows["multi_character"][:2] == ("", "per_character")


def test_m5_known_levels_read_verbatim(operator_client: Client, engine: FakeEngine) -> None:
    body = with_settings(
        structured_v2(),
        {"character_knowledge.testimony": "defaulted", "perception.level": "co_location"},
    )
    rows = module_table(overview(operator_client, engine, body))
    assert rows["testimony"][:2] == ("", "defaulted")
    assert rows["perception"][:2] == ("", "co_location")


def test_m5_off_values_read_off(operator_client: Client, engine: FakeEngine) -> None:
    body = with_settings(
        structured_v2(),
        {
            "character_knowledge.nested_beliefs.enabled": False,
            "character_knowledge.lazy_resolution": False,
            "character_knowledge.testimony": "off",
            "perception.level": "none",
            "multi_character.renders": "combined",
        },
    )
    rows = module_table(overview(operator_client, engine, body))
    for module in ("nested_beliefs", "lazy_resolution", "testimony", "perception"):
        assert rows[module][:2] == ("muted", "off"), module
    assert rows["multi_character"][:2] == ("muted", "off")
    assert rows["partitions"][:2] == ("", "on")
    # Complement: none of the off values renders as a raw <code> state.
    assert not any("<code>" in rows[module][1] for module in M5_ROWS)


def test_m5_unknown_level_is_raw() -> None:
    # The schema-validating fake refuses an undelivered level, so the row is checked directly.
    effective = structured_v2(perception__level="model")["effective"]
    rows = {row.module: row for row in module_rows(effective)}
    assert (rows["perception"].kind, rows["perception"].value) == ("raw", "model")
    assert not rows["perception"].off
    html = render_to_string("console/inspect/overview.html", {"modules": [rows["perception"]]})
    assert module_table(html)["perception"] == (
        "",
        "<code>model</code>",
        "<code>perception.level</code>",
    )


def test_an_m4_config_shows_no_m5_rows(operator_client: Client, engine: FakeEngine) -> None:
    rows = module_table(overview(operator_client, engine, structured()))
    assert len(rows) == 12
    assert not M5_ROWS & set(rows)
    assert list(rows) == [module for module in ENGINE_ORDER if module not in M5_ROWS]


def test_m5_capability_labels(operator_client: Client, engine: FakeEngine) -> None:
    items = capability_items(overview(operator_client, engine, structured_v2()))
    for item in (
        '<span title="partitions">Per-character partitions</span>',
        '<span title="nested_beliefs">Nested beliefs</span>',
        '<span title="lazy_resolution">Lazy resolution</span>',
        '<span title="testimony:model">Testimony (model)</span>',
        '<span title="perception:full">Perception (full)</span>',
        '<span title="multi_character:per_character">'
        "Multi-character renders (per_character)</span>",
    ):
        assert item in items
    assert not any(item.startswith("<code>") for item in items)
    assert capability_label("perception:co_location") == "Perception (co_location)"
    assert capability_label("testimony") == "Testimony"
    assert capability_label("multi_character") == "Multi-character renders"


def dial_row(html: str, module: str) -> str | None:
    found = re.search(rf'<tr class="{module}-dials">.*?</tr>', html, re.S)
    return found.group(0) if found is not None else None


def caption(html: str, module: str) -> str:
    row = dial_row(html, module)
    assert row is not None
    found = re.search(r'<div class="dials">(.*?)\s*<ul>', row, re.S)
    assert found is not None
    return found.group(1)


def dial_items(row: str) -> list[str]:
    return re.findall(r"<li>(.*?)</li>", row)


def test_module_dials_under_their_rows(operator_client: Client, engine: FakeEngine) -> None:
    html = overview(operator_client, engine, structured_v2())
    for module in ("pipeline", "partitions", "multi_character"):
        row = dial_row(html, module)
        assert row is not None, module
        # Directly under its module's row.
        own = html.index(f'<tr class="module-{module}')
        assert html.index(row) > own
        assert '<tr class="module-' not in html[own + 1 : html.index(row)]
    partitions = dial_row(html, "partitions")
    assert partitions is not None
    assert caption(html, "partitions") == "Character knowledge dials"
    assert dial_items(partitions) == [
        "<code>character_knowledge.max_adjudications_per_turn</code>: 2",
        "<code>character_knowledge.max_lazy_resolutions_per_turn</code>: 20",
        "<code>character_knowledge.nested_beliefs.depth</code>: 2",
    ]
    multi = dial_row(html, "multi_character")
    assert multi is not None
    assert caption(html, "multi_character") == "Multi-character dials"
    assert dial_items(multi) == ["<code>multi_character.interjection_max_words</code>: 6"]
    assert dial_row(html, "nested_beliefs") is None  # depth sits under partitions


def test_module_dials_leave_out_missing_dials_and_empty_groups() -> None:
    effective = with_settings(
        structured_v2(),
        {
            "character_knowledge.max_lazy_resolutions_per_turn": None,
            "character_knowledge.nested_beliefs.enabled": False,
            "multi_character.interjection_max_words": None,
        },
    )["effective"]
    rows = {row.module: row for row in module_rows(effective)}
    group = rows["partitions"].dials
    assert group is not None
    assert [dial.path for dial in group.dials] == [
        "character_knowledge.max_adjudications_per_turn",
        "character_knowledge.nested_beliefs.depth",  # shown while nested beliefs are off
    ]
    assert rows["multi_character"].dials is None
    html = render_to_string("console/inspect/overview.html", {"modules": list(rows.values())})
    assert dial_row(html, "multi_character") is None


@pytest.mark.parametrize(
    ("settings", "notes"),
    [
        (
            {"pipeline": "lite"},
            {
                "pipeline": " (unused under lite)",
                "partitions": " (unused under lite)",
                "multi_character": " (unused under lite)",
            },
        ),
        (
            {"character_knowledge.partitions": False, "multi_character.renders": "combined"},
            {
                "pipeline": "",
                "partitions": " (unused while off)",
                "multi_character": " (unused while off)",
            },
        ),
        ({}, {"pipeline": "", "partitions": "", "multi_character": ""}),
        # A RAW state is not off.
        ({"multi_character.renders": "sometimes"}, {"multi_character": ""}),
    ],
    ids=["lite", "off", "on", "raw"],
)
def test_dial_captions_unused_rule(settings: dict[str, Any], notes: dict[str, str]) -> None:
    effective = with_settings(structured_v2(), settings)["effective"]
    rows = {row.module: row for row in module_rows(effective)}
    for module, note in notes.items():
        group = rows[module].dials
        assert group is not None
        assert group.note == note, module
    html = render_to_string(
        "console/inspect/overview.html", {"modules": list(rows.values()), "pipeline": "lite"}
    )
    for module, note in notes.items():
        assert caption(html, module).endswith(f"dials{note}")


def test_dial_captions_render_on_the_page(operator_client: Client, engine: FakeEngine) -> None:
    body = with_settings(structured_v2(), {"multi_character.renders": "combined"})
    html = overview(operator_client, engine, body)
    assert caption(html, "multi_character") == "Multi-character dials (unused while off)"
    assert caption(html, "partitions") == "Character knowledge dials"


def _collapsed(fragment: str) -> str:
    return re.sub(r"\s+", " ", fragment).strip()


def test_pipeline_dials_unchanged(operator_client: Client, engine: FakeEngine) -> None:
    golden = (FIXTURES / "golden" / "pipeline_dials.html").read_text()
    row = dial_row(overview(operator_client, engine, structured()), "pipeline")
    assert row is not None
    assert _collapsed(row) == _collapsed(golden)


def test_module_table_has_one_row_per_delivered_module_in_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = module_table(overview(operator_client, engine, structured_v2()))
    assert list(rows) == [module for module, _ in MODULE_ROWS]
    assert [path for _, _, path in rows.values()] == [
        f"<code>{path}</code>" for _, path in MODULE_ROWS
    ]


def test_absent_switch_has_no_row_and_zero_repair_attempts_read_off(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = structured(pins=None, repair__max_attempts=0, verification__level="off")
    rows = module_table(overview(operator_client, engine, body))
    assert "pins" not in rows
    assert rows["repair"][:2] == ("muted", "off")
    assert rows["verification"][:2] == ("muted", "off")


def test_unexpected_switch_value_renders_verbatim_as_code() -> None:
    # The schema-validating fake refuses a mistyped switch, so the row is checked directly.
    rows = {
        row.module: row for row in module_rows(structured(pins__enabled="sometimes")["effective"])
    }
    assert (rows["pins"].kind, rows["pins"].value) == ("raw", "sometimes")
    html = render_to_string("console/inspect/overview.html", {"modules": [rows["pins"]]})
    assert module_table(html)["pins"][1] == "<code>sometimes</code>"


def test_structured_pipeline_dials_with_thousands_separators(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = overview(operator_client, engine, structured(render__working_memory_beats=None))
    assert "Structured pipeline dials" in html
    assert "<li><code>render.max_beats</code>: 6</li>" in html
    assert "<li><code>render.prompt_token_budget</code>: 6,000</li>" in html
    assert "render.working_memory_beats" not in html
    assert "(unused under lite)" not in html


def test_every_effective_setting_links_config(operator_client: Client, engine: FakeEngine) -> None:
    html = overview(operator_client, engine, structured())
    assert f'Every effective setting: <a href="/stories/{STORY}/config/">Config</a>' in html
    assert "<pre" not in html  # no raw-JSON "More" block


# --- capability map -------------------------------------------------------------------------------


def test_capability_map_link_is_the_main_branch_doc(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = overview(operator_client, engine, structured())
    assert CAPABILITY_MAP_URL == (
        "https://github.com/nathanpond/Epistrel/blob/main/docs/capability-map.md"
    )
    assert f'<a class="capability-map" href="{CAPABILITY_MAP_URL}">{MAP_LABEL}</a>' in html
    assert "/blob/v" not in html
    assert not any(r.url.path in ("/version", "/health") for r in engine.requests)


# --- header line ----------------------------------------------------------------------------------


def header(html: str) -> str:
    found = re.search(r'<p class="overview-header">(.*?)</p>', html, re.S)
    assert found is not None
    return " ".join(found.group(1).split())


def test_header_line_profile_fingerprint_quality_and_config(
    operator_client: Client, engine: FakeEngine
) -> None:
    line = header(overview(operator_client, engine, structured()))
    assert "Profile <code>00000000000000000000PRF002</code> v1 (pinned)" in line
    full = "sha256:" + "7" * 64
    assert f'fingerprint <code title="{full}">sha256:{"7" * 12}</code>' in line
    assert '<span title="uncertified">Uncertified</span>' in line
    assert line.endswith(f'<a href="/stories/{STORY}/config/">Config</a>')


def test_header_line_pinned_and_deleted(operator_client: Client, engine: FakeEngine) -> None:
    body = structured()
    body["profile"] = {**body["profile"], "pinned": True, "deleted": True}
    assert " v1 (pinned, deleted) " in header(overview(operator_client, engine, body))


def test_header_line_unflagged_profile(operator_client: Client, engine: FakeEngine) -> None:
    body = structured()
    body["profile"] = {**body["profile"], "pinned": False, "deleted": False}
    line = header(overview(operator_client, engine, body))
    assert "v1 · fingerprint" in line


def test_header_line_unprefixed_and_empty_fingerprint(
    operator_client: Client, engine: FakeEngine
) -> None:
    line = header(
        overview(operator_client, engine, {**structured(), "fingerprint": "abcdef0123456789xyz"})
    )
    assert '<code title="abcdef0123456789xyz">abcdef012345</code>' in line
    engine.reset()
    engine.respond("GET", STORY_PATH, 200, json=story_json())
    assert "fingerprint — ·" in header(
        overview(operator_client, engine, {**structured(), "fingerprint": ""})
    )


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("sha256:0123456789abcdef", "sha256:0123456789ab"),
        ("0123456789abcdef", "0123456789ab"),
        ("", "—"),
    ],
)
def test_overview_fingerprint(value: str, shown: str) -> None:
    assert overview_fingerprint(value) == shown
