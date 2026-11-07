"""#312: E13's quality badge wherever a quality status appears — Config, the turn report (the Play
inspector, the turn POST and the operation detail page) and the profile list — and the
"Certifications for this configuration" link beside each fingerprint (E6 ``certifications_url``,
D19: plain text while ``engine:certifications`` is unregistered; #313 registered it, so the
plain cases go through ``tests.certification_helpers.unregistered_certification_pages``)."""

import copy
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import play, profiles, story_config
from tests.certification_helpers import unregistered_certification_pages
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import EFFECTIVE, answer_effective_config
from tests.fixtures import engine_fixture
from tests.inspect.helpers import full_v4
from tests.play.helpers import HTMX, RESPOND, STORY, TURN_OP, answer_page, post_turn, respond_json
from tests.profile_helpers import ITEM, LIST, SCHEMA, fresh_cache, profile, schema_body

pytestmark = pytest.mark.django_db

M7_URLS = "tests.urls_with_m7_pages"
CONFIG = f"/stories/{STORY}/config/"
VERSIONS = "/engine-profiles/{profile_id}/versions"
SETTINGS = "/admin/settings"
CERT = "01JM7CT0000000000000000001"
FULL_FP = "sha256:4710ae26cb731c016d2b86c747e7d3e40e7e9b0a3cfda00bfe60118230039b4b"
LINK_TEXT = "Certifications for this configuration"
FLOORS = (
    "Floors passed: NFR-QUAL-2.false_contradiction, NFR-QUAL-2.false_support, NFR-QUAL-4.recall, "
    "NFR-QUAL-5.valid_after_retry, NFR-QUAL-9.within_range, smoke.turn_completed"
)
CERTIFIED: dict[str, Any] = {
    "status": "certified",
    "certification_id": CERT,
    "floors_passed": FLOORS.removeprefix("Floors passed: ").split(", "),
}

# (case id, quality_status, visible badge text, badge class, title)
CASES = [
    ("certified", CERTIFIED, "Certified", "quality quality-certified", "certified"),
    *[
        (
            f"stale-{reason}",
            {"status": "stale", "certification_id": CERT, "invalidated_reason": reason},
            text_,
            "quality quality-stale",
            f"stale ({reason})",
        )
        for reason, text_ in (
            ("canary", "Stale — a canary eval fell below a floor"),
            ("drift", "Stale — the model changed under the same version"),
            ("manual", "Stale — withdrawn by an operator"),
            ("later", "Stale — Later"),
        )
    ],
    ("uncertified", {"status": "uncertified"}, "Uncertified", "quality", "uncertified"),
]
CASE_IDS = [c[0] for c in CASES]


def badge(css: str, title: str, shown: str) -> str:
    return f'<span class="{css}" title="{title}">{shown}</span>'


def fingerprint_query(fingerprint: str = FULL_FP) -> str:
    return f"/engine/certifications/?fingerprint={quote(fingerprint, safe='')}"


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """One fake Engine behind Config, Play and the profile list."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_guarantees(fake)
    answer_effective_config(fake)  # the play header (Config's own read is answered below)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (story_config, play, profiles):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    presets = engine_fixture("m7/presets")
    fake.respond("GET", ITEM, 200, json=profile())
    fake.respond("GET", LIST, 200, json=presets)
    fake.respond("GET", LIST, 200, json=presets, query={"preset": "true"})
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    fake.respond("GET", VERSIONS, 200, json=engine_fixture("list_engine_profile_versions"))
    fake.respond("GET", SETTINGS, 200, json=engine_fixture("get_deployment_settings"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def certification_reads(engine: FakeEngine) -> list[str]:
    return [r.url.path for r in engine.requests if r.url.path.startswith("/certifications")]


# --- Config ---------------------------------------------------------------------------------------


def config_page(client: Client, engine: FakeEngine, quality_status: Any, **changes: Any) -> str:
    body = {**full_v4(), "quality_status": quality_status, **changes}
    engine.respond("GET", EFFECTIVE, 200, json=body)
    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    response = client.get(CONFIG)
    assert response.status_code == 200
    return str(response.content.decode())


def dd(html: str, term: str) -> str:
    match = re.search(rf"<dt>{term}</dt><dd[^>]*>(.*?)</dd>", html, re.S)
    assert match, f"no {term}"
    return match.group(1)


@pytest.mark.parametrize(("case", "status", "shown", "css", "title"), CASES, ids=CASE_IDS)
def test_config_badge(
    operator_client: Client,
    engine: FakeEngine,
    case: str,
    status: dict[str, Any],
    shown: str,
    css: str,
    title: str,
) -> None:
    quality = dd(config_page(operator_client, engine, status), "Quality status")
    assert quality.startswith(badge(css, title, shown))
    if case == "certified":
        assert (  # #313 registered engine:certification
            f'<a class="certification-link" href="/engine/certifications/{CERT}/">'
            f'<code title="{CERT}">'
        ) in quality
        assert f'<span class="quality-floors">{FLOORS}</span>' in quality
    if case == "uncertified":
        # exactly, then #314's link to request a run
        certify = f'<a class="certify-link" href="{CONFIG.removesuffix("config/")}certify/">'
        tail = f" · {certify}Request a certification run…</a>"
        assert quality == badge("quality", "uncertified", "Uncertified") + tail


def test_config_links_under_the_m7_urlconf(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Complement:** unregistered pages give plain text (D19); registered ones give links."""
    with monkeypatch.context() as patched:
        unregistered_certification_pages(patched)
        plain = config_page(operator_client, engine, CERTIFIED)
    fingerprint = dd(plain, "Fingerprint")
    assert fingerprint == f"<code>{FULL_FP}</code> · {LINK_TEXT}"
    assert "certification-link" not in dd(plain, "Quality status")
    with override_settings(ROOT_URLCONF=M7_URLS):
        linked = config_page(operator_client, engine, CERTIFIED)
    assert dd(linked, "Fingerprint") == (
        f'<code>{FULL_FP}</code> · <a class="certifications-link" href="{fingerprint_query()}">'
        f"{LINK_TEXT}</a>"
    )
    assert f'<a class="certification-link" href="/engine/certifications/{CERT}/">' in dd(
        linked, "Quality status"
    )


def test_config_without_a_fingerprint_has_no_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = config_page(operator_client, engine, CERTIFIED, fingerprint="")
    assert dd(html, "Fingerprint") == "<code></code>"
    assert LINK_TEXT not in html


# --- the turn report ------------------------------------------------------------------------------


def respond_with(quality_status: Any) -> dict[str, Any]:
    """The ``respond_play`` turn with ``quality_status`` replaced (``None``: the null a replay of
    a pre-fingerprint turn carries; the schema requires the key)."""
    body = copy.deepcopy(respond_json())
    body["quality_status"] = quality_status
    return body


def log_turn(response: dict[str, Any]) -> EngineOperation:
    return EngineOperation.objects.create(
        operator_username="nate",
        principal="console:nate",
        story_id=STORY,
        kind="respond",
        http_method="POST",
        path=f"/stories/{STORY}/respond",
        request={"body": {}, "headers": {}},
        http_status=200,
        engine_operation_id=TURN_OP,
        timeline_revision=2,
        config_revision=0,
        idempotency_key="01JB2Z8Y5Q3W6K9M1N4P7R0KY9",
        outcome="ok",
        response=response,
    )


def inspector(client: Client) -> str:
    url = reverse("stories:inspector", args=[STORY])
    response = client.get(url, {"operation": TURN_OP}, headers=HTMX)
    assert response.status_code == 200
    return str(response.content.decode())


def turn_dd(html: str, cls: str) -> str:
    match = re.search(rf'<dd class="{cls}">(.*?)</dd>', html, re.S)
    assert match, f"no {cls}"
    return match.group(1)


def turn_reports(client: Client, engine: FakeEngine, quality_status: Any) -> dict[str, str]:
    """The same report in the Play inspector, the turn POST and the operation detail page."""
    body = respond_with(quality_status)
    row = log_turn(body)
    answer_page(engine)
    engine.respond("POST", RESPOND, 200, json=body)
    detail = client.get(reverse("operations:detail", args=[row.pk])).content.decode()
    return {"inspector": inspector(client), "turn": post_turn(client), "operation": detail}


@pytest.mark.parametrize(("case", "status", "shown", "css", "title"), CASES, ids=CASE_IDS)
def test_turn_report_badge(
    operator_client: Client,
    engine: FakeEngine,
    case: str,
    status: dict[str, Any],
    shown: str,
    css: str,
    title: str,
) -> None:
    for where, html in turn_reports(operator_client, engine, status).items():
        quality = turn_dd(html, "turn-quality")
        assert quality.startswith(badge(css, title, shown)), where
        if case == "certified":
            assert f'<span class="quality-floors">{FLOORS}</span>' in quality, where
        if case == "uncertified":
            assert quality == badge("quality", "uncertified", "Uncertified"), where
        assert turn_dd(html, "turn-fingerprint").endswith(f'">{LINK_TEXT}</a>'), where  # #313
    assert certification_reads(engine) == []


def test_turn_report_links_under_the_m7_urlconf(
    operator_client: Client, engine: FakeEngine
) -> None:
    fingerprint = respond_json()["fingerprint"]
    with override_settings(ROOT_URLCONF=M7_URLS):
        reports = turn_reports(operator_client, engine, CERTIFIED)
    for where, html in reports.items():
        assert turn_dd(html, "turn-fingerprint").endswith(
            f' · <a class="certifications-link" href="{fingerprint_query(fingerprint)}">'
            f"{LINK_TEXT}</a>"
        ), where
        assert f'href="/engine/certifications/{CERT}/"' in turn_dd(html, "turn-quality"), where


def test_a_report_without_quality_reads_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** no ``quality_status``: "—", no badge; the fingerprint still links."""
    for where, html in turn_reports(operator_client, engine, None).items():
        assert turn_dd(html, "turn-quality") == "—", where
        assert LINK_TEXT in turn_dd(html, "turn-fingerprint"), where


def test_a_report_without_a_fingerprint_has_no_link() -> None:
    from django.template.loader import render_to_string

    from console.engine.models import TurnReport

    report = TurnReport.from_json({"model_calls": [], "elapsed_ms": 5})
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = render_to_string("console/_turn_report.html", {"report": report})
    assert turn_dd(html, "turn-fingerprint") == "—"
    assert turn_dd(html, "turn-quality") == "—"
    assert LINK_TEXT not in html
    assert '<span class="quality' not in html


# --- the profile list -----------------------------------------------------------------------------


def quality_cell(html: str, profile_id: str) -> str:
    row = re.search(rf'<tr id="profile-{profile_id}"[^>]*>(.*?)</tr>', html, re.S)
    assert row, profile_id
    cell = re.search(r'<td class="quality-cell">(.*?)</td>', row.group(1), re.S)
    assert cell, profile_id
    return cell.group(1)


def profile_list(client: Client) -> str:
    response = client.get("/profiles/")
    assert response.status_code == 200
    return str(response.content.decode())


def test_profile_list_quality_column(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    lite, full, standard, max_ = (p["id"] for p in engine_fixture("m7/presets")["items"])
    unregistered_certification_pages(monkeypatch)  # D19: before #313 registered its pages
    html = profile_list(operator_client)
    assert "<th>Current version</th><th>Quality</th><th>Preset</th>" in html
    full_floors = (
        "Floors passed: NFR-QUAL-2.false_contradiction, NFR-QUAL-2.false_support, "
        "NFR-QUAL-4.recall, NFR-QUAL-5.valid_after_retry, NFR-QUAL-9.within_range, "
        "smoke.turn_completed"
    )
    assert quality_cell(html, full) == (
        badge("quality quality-certified", "certified", "Certified")
        + f' <span class="quality-floors">{full_floors}</span>'
    )
    for uncertified in (lite, standard):
        assert quality_cell(html, uncertified) == badge("quality", "uncertified", "Uncertified")
    # A FloorsMet carries no reason: the bare "Stale", plain while engine:certifications is
    # unregistered.
    assert quality_cell(html, max_) == badge("quality quality-stale", "stale", "Stale")
    assert "certifications-link" not in html


def test_a_stale_profile_row_links_its_certifications(
    operator_client: Client, engine: FakeEngine
) -> None:
    presets = engine_fixture("m7/presets")["items"]
    max_ = presets[3]["id"]
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = profile_list(operator_client)
    assert quality_cell(html, max_) == (
        f'<a class="certifications-link" href="/engine/certifications/?profile_id={max_}">'
        + badge("quality quality-stale", "stale", "Stale")
        + "</a>"
    )
    # **Complement:** certified and uncertified rows have no certifications link.
    for other in presets[:3]:
        assert "certifications-link" not in quality_cell(html, other["id"])


def test_a_profile_without_quality_reads_a_dash(
    operator_client: Client, engine: FakeEngine
) -> None:
    """**Complement:** "—" with no ``quality``, and the list makes exactly main's calls."""
    bare = {k: v for k, v in profile().items() if k != "quality"}
    engine.respond("GET", LIST, 200, json={"items": [bare], "next_cursor": None})
    html = profile_list(operator_client)
    assert quality_cell(html, bare["id"]) == "—"
    assert [(r.method, r.url.path, str(r.url.params)) for r in engine.requests] == [
        ("GET", "/engine-profiles", "limit=50"),
        ("GET", "/admin/settings", ""),
    ]


# --- no extra reads, matching links ---------------------------------------------------------------


def test_no_page_reads_certifications(operator_client: Client, engine: FakeEngine) -> None:
    config_page(operator_client, engine, CERTIFIED)
    answer_page(engine)
    assert operator_client.get(reverse("stories:detail", args=[STORY])).status_code == 200
    turn_reports(operator_client, engine, {"status": "stale", "invalidated_reason": "canary"})
    profile_list(operator_client)
    assert engine.requests  # the pages did read the Engine
    assert certification_reads(engine) == []


def test_fingerprint_links_match(operator_client: Client, engine: FakeEngine) -> None:
    body = respond_with(CERTIFIED)
    body["fingerprint"] = FULL_FP  # the same configuration as Config's
    with override_settings(ROOT_URLCONF=M7_URLS):
        config = config_page(operator_client, engine, CERTIFIED)
        log_turn(body)
        report = inspector(operator_client)
    href = re.compile(r'<a class="certifications-link" href="([^"]+)">')
    (config_href,) = href.findall(dd(config, "Fingerprint"))
    (report_href,) = href.findall(turn_dd(report, "turn-fingerprint"))
    assert config_href == report_href == fingerprint_query()
    assert "?fingerprint=sha256%3A" in config_href
