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
from console.inspect import CAPABILITY_MAP_URL, MODULE_ROWS, module_rows, overview_fingerprint
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, STORY, STORY_PATH, lite, structured
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


def test_module_rows_follow_module_rows_order() -> None:
    assert [module for module, _ in MODULE_ROWS] == [
        "pipeline",
        "rolling_summary",
        "episodic",
        "consolidation",
        "pins",
        "verification",
        "contradiction_checking",
        "commitment",
        "repair",
        "polish",
        "budgets",
        "models",
    ]


def test_module_table_has_one_row_per_delivered_module_in_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = module_table(overview(operator_client, engine, structured()))
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
