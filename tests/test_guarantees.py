"""The guarantee table on the profile page and a story's Config page, and the impact report's
links to its rows (#57)."""

import copy
import re
from collections.abc import Iterator
from html import unescape
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client

from console.engine.client import EngineClient
from console.engine.models import GuaranteeStatus, GuaranteeTable, ImpactReport
from console.engine_access import engine_client_for
from console.guarantees import ROW_CAVEATS, caveats, needs_text, status_text
from console.views import story_config
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import (
    PROFILE_GUARANTEES,
    STORY_GUARANTEES,
    answer_guarantees,
    guarantees_fixture,
)
from tests.fakes.story_config import EFFECTIVE, effective_config
from tests.play.helpers import story_json
from tests.profile_helpers import ITEM, LIST, LITE, fake_engine, fresh_cache, impact, profile
from tests.ratings.helpers import calls, error, text

pytestmark = pytest.mark.django_db

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
DETAIL = f"/profiles/{LITE}/"
CONFIG = f"/stories/{STORY}/config/"
PROFILE_PATH = f"/engine-profiles/{LITE}/guarantees"
STORY_PATH = f"/stories/{STORY}/guarantees"
DOCS = "https://github.com/nathanpond/Epistrel/blob/main/docs/guarantees.md"
TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "console"
BASE_CAVEATS = (
    "The model proposes; the engine validates structured claims, effects, and permissions. "
    "A model-dependent step is only as good as its metric. "
    "Only the claims detected in an output are checked."
)
PARTITIONS_CAVEAT = "Structural knowledge separation isn't provided"
AUDIENCE_CAVEAT = "Audience filtering isn't provided"


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The profile views' fake (Lite profile, its Lite table) plus the Config page's reads."""
    for fake in fake_engine(monkeypatch):

        def client_with_fake(request: HttpRequest, fake: FakeEngine = fake) -> EngineClient:
            client = engine_client_for(request)
            client._transport = fake.transport
            return client

        monkeypatch.setattr(story_config, "engine_client_for", client_with_fake)
        fake.respond("GET", EFFECTIVE, 200, json=effective_config(story_id=STORY))
        fake.respond("GET", LIST, 200, json={"items": [profile()], "next_cursor": None})
        fake.respond("GET", "/stories/{story_id}", 200, json=story_json())
        yield fake


def get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return str(response.content.decode())


def section(html: str) -> str:
    match = re.search(r'<section class="guarantees">(.*?)</section>', html, re.S)
    assert match, "no Guarantees section"
    return match.group(1)


def row(html: str, row_id: str) -> str:
    match = re.search(rf'<tr id="g-{re.escape(row_id)}"[^>]*>(.*?)</tr>', html, re.S)
    assert match, f"no row {row_id}"
    return match.group(0)


def caption_of(html: str) -> str:
    match = re.search(r'<p class="guarantees-caption">(.*?)</p>', html, re.S)
    assert match, "no caption"
    return unescape(text(match.group(1)))


def cells(html: str, row_id: str) -> list[str]:
    return [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row(html, row_id), re.S)]


def status(**changes: Any) -> GuaranteeStatus:
    body = {
        "id": "x",
        "step": "a step",
        "kind": "deterministic",
        "metric": None,
        "requires": [],
        "setting": None,
        "purposes": [],
        "provided": True,
        "delivered_in": None,
        **changes,
    }
    parsed = GuaranteeStatus.from_json(body)
    assert parsed is not None
    return parsed


def table_of(*rows: dict[str, Any], version: int = 2) -> dict[str, Any]:
    return {"profile_id": LITE, "version": version, "rows": list(rows)}


def fixture_row(row_id: str, name: str = "lite", **changes: Any) -> dict[str, Any]:
    found = next(r for r in guarantees_fixture(name)["rows"] if r["id"] == row_id)
    return {**copy.deepcopy(found), **changes}


def parsed_table(body: dict[str, Any]) -> GuaranteeTable:
    parsed = GuaranteeTable.from_json(body)
    assert parsed is not None
    return parsed


# --- wording (console.guarantees) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("requires", "setting", "provided", "expected"),
    [
        ([{"module": "partitions"}], None, False, "`partitions`"),
        (
            [{"module": "verification", "level": "declared"}],
            None,
            True,
            "`verification` at `declared` or above",
        ),
        (
            [{"module": "clock", "level": "on", "exact": True}],
            None,
            False,
            "`clock` at `on`",
        ),
        ([{"module": "clock", "level": None, "exact": True}], None, False, "`clock`"),
        (
            [{"module": "verification", "level": "declared"}, {"module": "contradiction_checking"}],
            None,
            True,
            "`verification` at `declared` or above; `contradiction_checking`",
        ),
        (
            [{"module": "commitment"}],
            {"path": "commitment.salience", "values": ["rules_and_model", "model"]},
            True,
            "`commitment`; `commitment.salience` = `rules_and_model` or `model`",
        ),
        (
            [],
            {"path": "player_view", "values": ["character", "story", "omniscient"]},
            True,
            "`player_view` = `character`, `story` or `omniscient`",
        ),
        (
            [],
            {"path": "a.b", "values": [True, 3, {"k": 1}]},
            True,
            '`a.b` = `true`, `3` or `{"k":1}`',
        ),
        ([], {"path": "a.b", "values": []}, True, "`a.b` (any value)"),
        ([], None, True, "none (every profile)"),
        ([], None, False, "none (every profile)"),
        ([], None, None, "none (outside stories)"),
        ([{"module": "episodic"}], None, None, "`episodic`"),
    ],
)
def test_needs_wording(
    requires: list[dict[str, Any]], setting: dict[str, Any] | None, provided: Any, expected: str
) -> None:
    assert needs_text(status(requires=requires, setting=setting, provided=provided)) == expected


@pytest.mark.parametrize(
    ("provided", "delivered_in", "expected"),
    [
        (True, None, "Provided"),
        (False, None, "Not provided"),
        (False, "M5", "Not provided (delivered in M5)"),
        (None, None, "Outside stories"),
        (True, "M5", "Provided"),  # provided decides first
        (None, "M5", "Outside stories"),
    ],
)
def test_status_wording(provided: Any, delivered_in: str | None, expected: str) -> None:
    assert status_text(status(provided=provided, delivered_in=delivered_in)) == expected


def test_no_delivered_in_text_without_a_milestone() -> None:
    assert "delivered in" not in status_text(status(provided=False, delivered_in=None))


def test_caveats_follow_the_rows() -> None:
    structured = parsed_table(guarantees_fixture("structured"))
    shown = caveats(structured)
    assert shown[:3] == BASE_CAVEATS.replace(". ", ".\n").split("\n")
    assert [s.split(":")[0] for s in shown[3:]] == [PARTITIONS_CAVEAT, AUDIENCE_CAVEAT]


@pytest.mark.parametrize(
    "rows",
    [
        [  # both provided
            fixture_row("structural_knowledge_separation", provided=True, delivered_in=None),
            fixture_row("audience_filtering", provided=True, delivered_in=None),
        ],
        [],  # both absent
        [  # provided null
            fixture_row("structural_knowledge_separation", provided=None),
            fixture_row("audience_filtering", provided=None),
        ],
    ],
)
def test_a_conditional_caveat_needs_its_row_not_provided(rows: list[dict[str, Any]]) -> None:
    assert len(caveats(parsed_table(table_of(*rows)))) == 3


def test_either_row_absent_drops_only_its_sentence() -> None:
    only_audience = parsed_table(table_of(fixture_row("audience_filtering")))
    only_partitions = parsed_table(table_of(fixture_row("structural_knowledge_separation")))
    assert caveats(only_audience)[3:] == [ROW_CAVEATS[1][1]]
    assert caveats(only_partitions)[3:] == [ROW_CAVEATS[0][1]]


# --- the profile page -----------------------------------------------------------------------------


def test_the_profile_page_shows_the_table_in_the_engines_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, DETAIL)
    shown = section(html)
    ids = re.findall(r'<tr id="g-([^"]+)"', shown)
    assert ids == [r["id"] for r in guarantees_fixture("lite")["rows"]]
    assert html.index("<h2>Settings</h2>") < html.index('<h2 id="guarantees">Guarantees</h2>')
    caption = caption_of(shown)
    assert caption == "Profile Lite ( …00PRF001 ) · version 2"
    assert f'<code title="{LITE}">' in shown


def test_each_kind_metric_and_status_renders(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 200, json=guarantees_fixture("structured"))
    html = section(get(operator_client, DETAIL))
    assert cells(html, "claim_detectors") == [
        "Deterministic claim detectors claim_detectors",
        "By rules",
        "—",
        "verification at declared or above",
        "—",
        "Provided",
    ]
    assert '<td title="deterministic">By rules</td>' in row(html, "claim_detectors")
    assert cells(html, "entailment_fallback")[:3] == [
        "Entailment fallback entailment_fallback",
        "By a model",
        "NFR-QUAL-2",
    ]
    assert '<td title="model_dependent">By a model</td>' in row(html, "entailment_fallback")
    assert "<td><code>NFR-QUAL-2</code></td>" in row(html, "entailment_fallback")
    assert cells(html, "importance_scoring")[2] == "Unmeasured"
    assert cells(html, "embedding")[4] == "embedding"
    assert cells(html, "structural_knowledge_separation")[5] == "Not provided (delivered in M5)"
    assert cells(html, "rating_suggestion")[3] == "none (outside stories)"
    assert cells(html, "rating_suggestion")[5] == "Outside stories"
    assert cells(html, "rolling_summary")[5] == "Not provided"
    assert "delivered in" not in row(html, "rolling_summary")
    assert (
        "<code>commitment</code>; <code>commitment.salience</code> = <code>rules_and_model</code>"
        in row(html, "salience")
    )
    assert 'class="row-muted"' in row(html, "rolling_summary")
    assert 'class="row-muted"' in row(html, "rating_suggestion")
    assert 'class="row-muted"' in row(html, "structural_knowledge_separation")
    assert "row-muted" not in row(html, "claim_detectors")


def test_the_structured_table_shows_both_conditional_caveats(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 200, json=guarantees_fixture("structured"))
    shown = section(get(operator_client, DETAIL))
    block = re.search(r'<div class="caveats">(.*?)</div>', shown, re.S)
    assert block
    sentences = [unescape(text(p)) for p in re.findall(r"<p>(.*?)</p>", block.group(1), re.S)]
    assert " ".join(sentences[:3]) == BASE_CAVEATS
    assert sentences[3].startswith(PARTITIONS_CAVEAT)
    assert sentences[4].startswith(AUDIENCE_CAVEAT)
    assert "<code>think</code> or <code>latent</code>" in block.group(1)
    assert shown.index("guarantees-caption") < shown.index("guarantees-docs")
    assert shown.index("guarantees-docs") < shown.index('class="caveats"')
    assert shown.index('class="caveats"') < shown.index("<table")


def test_the_docs_link_is_mains_and_needs_no_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    shown = section(get(operator_client, DETAIL))
    assert f'<a href="{DOCS}">Guarantee table (Engine docs, main)</a>' in shown
    hrefs = re.findall(r'href="([^"]*guarantees\.md)"', shown)
    assert hrefs == [DOCS]
    assert not re.search(r"/v\d|/tree/|/blob/v", hrefs[0])
    assert not calls(engine, "GET", "/openapi.json")


def test_an_unknown_kind_is_the_bad_response_panel_not_a_partial_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = guarantees_fixture("lite")
    body["rows"][1]["kind"] = "oracle"
    engine.side_effect("GET", PROFILE_GUARANTEES, lambda _: httpx.Response(200, json=body))
    html = get(operator_client, DETAIL)
    shown = section(html)
    assert 'class="engine-error' in shown
    assert "<table" not in shown
    assert "<tr id=" not in shown
    assert 'class="caveats"' not in shown
    assert f'href="{DOCS}"' in shown
    assert "<h2>Settings</h2>" in html


@pytest.mark.parametrize(
    ("query", "sent"),
    [("?version=2", [("version", "2")]), ("?version=abc", []), ("", [])],
)
def test_the_version_is_forwarded_only_when_accepted(
    operator_client: Client, engine: FakeEngine, query: str, sent: list[tuple[str, str]]
) -> None:
    get(operator_client, DETAIL + query)
    (read,) = calls(engine, "GET", PROFILE_PATH)
    assert list(read.url.params.multi_items()) == sent


def test_a_profile_404_is_no_table_for_that_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 404, json=error("not_found", "no such version"))
    html = get(operator_client, DETAIL)
    shown = section(html)
    assert "No guarantee table for that version" in shown
    assert 'class="engine-error' not in shown
    assert 'class="caveats"' not in shown
    assert f'href="{DOCS}"' in shown
    assert "<h2>Settings</h2>" in html


def test_a_profile_503_is_the_panel_inside_the_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 503, json=error("database_unavailable"))
    html = get(operator_client, DETAIL)
    shown = section(html)
    assert 'class="engine-error' in shown
    assert "database_unavailable" in shown
    assert 'class="caveats"' not in shown
    assert "No guarantee table" not in shown
    assert "<h2>Settings</h2>" in html
    assert html.count('class="engine-error') == 1


def test_a_failed_profile_read_has_no_guarantees_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 404, json=error("not_found", "no such profile"))
    html = operator_client.get(DETAIL).content.decode()
    assert 'class="engine-error' in html
    assert '<section class="guarantees">' not in html


def test_a_table_for_another_version_says_reload(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 200, json={**guarantees_fixture(), "version": 3})
    caption = text(section(get(operator_client, DETAIL)))
    assert (
        "· version 3 (the table is for version 3; the profile changed while this page loaded"
        in (caption)
    )


def test_a_requested_version_needs_no_race_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 200, json={**guarantees_fixture(), "version": 3})
    shown = text(section(get(operator_client, f"{DETAIL}?version=2")))
    assert "· version 3" in shown
    assert "reload" not in shown


def test_an_empty_table_reads_none_without_caveats(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE_GUARANTEES, 200, json=table_of())
    shown = section(get(operator_client, DETAIL))
    assert '<p class="guarantees-empty">None</p>' in shown
    assert 'class="caveats"' not in shown
    assert "<table" not in shown


def test_a_duplicate_row_id_is_anchored_once(operator_client: Client, engine: FakeEngine) -> None:
    first = fixture_row("generation")
    again = fixture_row("generation", step="generation again")
    engine.respond("GET", PROFILE_GUARANTEES, 200, json=table_of(first, again))
    shown = section(get(operator_client, DETAIL))
    assert shown.count('id="g-generation"') == 1
    assert "Generation again" in shown


# --- the Config page ------------------------------------------------------------------------------


def test_the_config_page_shows_the_storys_table(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, CONFIG)
    assert len(calls(engine, "GET", STORY_PATH)) == 1
    assert not calls(engine, "GET", PROFILE_PATH)
    shown = section(html)
    assert html.index("<h2>Settings</h2>") < html.index('<h2 id="guarantees">Guarantees</h2>')
    caption = caption_of(shown)
    assert caption == (
        "Profile Lite ( …00PRF001 ) · version 2 · this story's effective configuration, "
        "overrides included"
    )
    assert cells(shown, "verification")[5] == "Not provided"
    assert cells(shown, "commitment_mechanics")[5] == "Not provided"
    assert cells(shown, "rating_suggestion")[5] == "Outside stories"
    assert PARTITIONS_CAVEAT in unescape(shown)


def test_the_overrides_form_reads_no_table(operator_client: Client, engine: FakeEngine) -> None:
    get(operator_client, f"{CONFIG}overrides/")
    assert not calls(engine, "GET", STORY_PATH)


def test_a_failed_profile_version_read_captions_the_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 503, json=error("database_unavailable"))
    assert caption_of(get(operator_client, CONFIG)).startswith("Profile …00PRF001 · version 2")


@pytest.mark.parametrize(
    ("http_status", "code"),
    [
        (404, "not_found"),
        (503, "database_unavailable"),
        (503, "erasure_pending"),
        (503, "ledger_unavailable"),
    ],
)
def test_a_failed_story_read_stays_in_the_section(
    operator_client: Client, engine: FakeEngine, http_status: int, code: str
) -> None:
    engine.respond("GET", STORY_GUARANTEES, http_status, json=error(code))
    html = get(operator_client, CONFIG)
    shown = section(html)
    assert 'class="engine-error' in shown
    assert code in shown
    assert "No guarantee table" not in shown
    assert 'class="caveats"' not in shown
    assert "<h2>Settings</h2>" in html
    assert "Assign a profile" in html


def test_a_failed_effective_config_read_makes_no_table_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 503, json=error("database_unavailable"))
    html = operator_client.get(CONFIG).content.decode()
    assert 'class="engine-error' in html
    assert '<section class="guarantees">' not in html
    assert not calls(engine, "GET", STORY_PATH)


# --- the impact report's lost guarantees ----------------------------------------------------------


def report(*lost: str) -> ImpactReport:
    parsed = ImpactReport.from_json(impact(guarantees_lost=list(lost)))
    assert parsed is not None
    return parsed


LINK = (
    '<a href="{href}#g-{id}" target="_blank" rel="noopener" '
    'title="Opens this row of the guarantee table in a new tab"><code>{id}</code></a>'
)
NOTE = (
    "Lost by at least one following story; a story with its own overrides can differ from this "
    "profile's table"
)


@pytest.mark.parametrize(
    ("mode", "href"),
    [("profile", f"/profiles/{LITE}/?version=2"), ("story", CONFIG)],
)
def test_lost_guarantees_link_their_rows(mode: str, href: str) -> None:
    html = render_to_string(
        "console/_impact_report.html",
        {
            "impact": report("verification", "claim_detectors"),
            "mode": mode,
            "guarantees_href": href,
        },
    )
    assert LINK.format(href=href, id="verification") in html
    assert LINK.format(href=href, id="claim_detectors") in html
    assert (NOTE in html) is (mode == "profile")


def test_without_an_href_lost_guarantees_stay_raw_code() -> None:
    html = render_to_string(
        "console/_impact_report.html", {"impact": report("verification"), "mode": "story"}
    )
    lost = re.search(r'<dd class="guarantees-lost">(.*?)</dd>', html, re.S)
    assert lost
    assert lost.group(1) == "<code>verification</code>"


def test_a_lost_id_names_a_row_of_the_table(operator_client: Client, engine: FakeEngine) -> None:
    lost = "claim_detectors"
    linked = render_to_string(
        "console/_impact_report.html",
        {"impact": report(lost), "mode": "story", "guarantees_href": CONFIG},
    )
    fragment = re.search(r'href="[^"]*#g-([^"]+)"', linked)
    assert fragment
    assert fragment.group(1) == lost
    assert f'<tr id="g-{lost}"' in section(get(operator_client, CONFIG))


def test_the_templates_never_say_verified() -> None:
    for name in ("_guarantees.html", "_impact_report.html"):
        assert "verified" not in (TEMPLATES / name).read_text(encoding="utf-8").lower()


def test_the_fixture_tables_match_the_schema(engine: FakeEngine) -> None:
    for name in ("lite", "structured"):
        answer_guarantees(engine, name)  # validated against the recorded schema
        assert parsed_table(guarantees_fixture(name)).rows
