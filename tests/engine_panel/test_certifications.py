"""#313: the Engine panel's Certifications list and a certification's page (FR-PROF-15/16; E11,
E13, E18, E22, E23, D17, D19, C13), over #292's ``m7/certifications.json``,
``certifications_bad_item.json`` and ``certification.json`` (one-off variants inline, E16)."""

import copy
import html as markup
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.certification import CANARY_LIST_REQUEST
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.paging import CURSOR_DROPPED
from console.views import certifications as views
from console.views import profiles as profile_views
from console.views import story_pages
from console.views.certifications import CANARY_NOTE
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.profile_helpers import fake_engine, fresh_cache

pytestmark = pytest.mark.django_db

LIST = "/engine/certifications/"
CERT = "01JM7CT0000000000000000001"
STALE_CERT = "01JM7CT0000000000000000002"
CANARY_RUN = "01JM7CR0000000000000000002"
DETAIL = f"/engine/certifications/{CERT}/"
FULL_FP = "sha256:4710ae26cb731c016d2b86c747e7d3e40e7e9b0a3cfda00bfe60118230039b4b"
PROFILE = "00000000000000000000PRF002"
HTMX = {"HX-Request": "true"}
M7_URLCONF = "tests.urls_with_m7_pages"


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def certification(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("certification")
    body.update(changes)
    return body


def stale_canary(**changes: Any) -> dict[str, Any]:
    stale = {
        "status": "stale",
        "invalidated_at": "2026-11-02T09:14:00Z",
        "invalidated_reason": "canary",
        "invalidated_by_run_id": CANARY_RUN,
    }
    return certification(**{**stale, **changes})


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(profile_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def reads(fake: FakeEngine, prefix: str = "/certifications") -> list[Any]:
    return [r for r in fake.requests if r.url.path.startswith(prefix)]


def query_of(request: Any) -> dict[str, list[str]]:
    return parse_qs(request.url.query.decode())


def body_rows(html: str) -> list[str]:
    tbody = re.search(r'<table class="certifications">.*?<tbody>(.*?)</tbody>', html, re.S)
    assert tbody is not None
    return re.findall(r"<tr[^>]*>.*?</tr>", tbody.group(1), re.S)


def text(fragment: str) -> str:
    return markup.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def section(html: str, cls: str) -> str:
    found = re.search(rf'<section class="{cls}">(.*?)</section>', html, re.S)
    assert found is not None, cls
    return found.group(1)


def hrefs(html: str) -> list[str]:
    return [markup.unescape(h) for h in re.findall(r'href="([^"]+)"', html)]


# --- the list -------------------------------------------------------------------------------------


def test_list_one_read(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications", 200, json=m7("certifications"))
    response = operator_client.get(LIST)
    html = response.content.decode()
    assert response.status_code == 200
    assert len(reads(engine)) == 1
    assert query_of(reads(engine)[0]) == {}  # E22: no limit, no filters
    assert "<title>Certifications · Engine</title>" in html
    assert '<a href="/engine/certifications/" aria-current="page">Certifications</a>' in html
    valid, stale = body_rows(html)
    assert (
        f'<a href="/engine/certifications/{CERT}/"><code title="{CERT}">…00000001</code></a>'
        in valid
    )
    assert '<span class="quality quality-certified" title="valid">Valid</span>' in valid
    assert (
        '<span class="quality quality-stale" title="stale (canary)">'
        "Stale — a canary eval fell below a floor</span>"
    ) in stale
    assert "6 passed · 1 failed · 1 insufficient" in valid
    assert '<td class="tier"><span title="other">Other</span></td>' in valid
    assert text(re.search(r'<td class="recorded">.*?</td>', valid, re.S).group(0)).startswith(  # type: ignore[union-attr]
        "Run · 2026-11-05 13:30:00 UTC"
    )
    assert "Import · " in text(stale)
    # Profile: the preset, then the short id "@v<version>" linked at that version.
    assert (
        '<span title="full">Full</span> <a class="profile-link" '
        f'href="/profiles/{PROFILE}/?version=4"><code title="{PROFILE}">…00PRF002</code>@v4</a>'
    ) in valid
    # Fingerprint: short, the full one in title, and a filter link.
    assert f'<code class="hash" title="{FULL_FP}">4710ae26cb73</code>' in valid
    assert f"{LIST}?fingerprint={FULL_FP.replace(':', '%3A')}" in hrefs(valid)
    assert '<p class="count">2 certifications on this page</p>' in html
    assert "Clear filters" not in html


def test_filters_pass_through(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications", 200, json=m7("certifications"))
    html = operator_client.get(
        LIST,
        {
            "fingerprint": f"  {FULL_FP.upper().replace('SHA256', 'sha256')} ",
            "profile_id": PROFILE.lower(),
        },
    ).content.decode()
    (sent,) = reads(engine)
    assert query_of(sent) == {"fingerprint": [FULL_FP], "profile_id": [PROFILE]}
    assert '<p class="count">2 certifications on this page, matching filters</p>' in html
    assert '<a class="clear-filters" href="/engine/certifications/">Clear filters</a>' in html
    # A row's filter link keeps the profile filter and sets its own fingerprint.
    link = next(h for h in hrefs(body_rows(html)[1]) if "fingerprint=" in h)
    assert parse_qs(urlsplit(link).query) == {
        "profile_id": [PROFILE],
        "fingerprint": ["sha256:f7f66a6e79302222222222222222222222222222222222222222222222222222"],
    }


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"fingerprint": "sha256:4710ae26cb73"}, "Enter a full fingerprint (sha256:…)"),
        ({"fingerprint": "4710ae26" * 8}, "Enter a full fingerprint (sha256:…)"),
        ({"profile_id": "nope"}, "Enter a profile id"),
    ],
)
def test_an_invalid_filter_makes_no_call(
    operator_client: Client, engine: FakeEngine, params: dict[str, str], error: str
) -> None:
    html = operator_client.get(LIST, params).content.decode()
    assert reads(engine) == []
    assert markup.escape(error) in html or error in html
    assert '<table class="certifications">' not in html
    assert "No certifications" not in html


def test_blank_filters_are_no_filter(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications", 200, json=m7("certifications"))
    operator_client.get(LIST, {"fingerprint": " ", "profile_id": ""})
    assert query_of(reads(engine)[0]) == {}


def test_paging_keeps_filters(operator_client: Client, engine: FakeEngine) -> None:
    page = m7("certifications")
    page["next_cursor"] = "01JM7CT0000000000000000002"
    engine.respond("GET", "/certifications", 200, json=page)
    html = operator_client.get(LIST, {"profile_id": PROFILE, "stray": "x"}).content.decode()
    nxt = re.search(r'<a class="next-page" href="([^"]+)">Next page</a>', html)
    assert nxt is not None
    assert parse_qs(urlsplit(markup.unescape(nxt.group(1))).query) == {
        "profile_id": [PROFILE],
        "cursor": ["01JM7CT0000000000000000002"],
    }
    assert "First page" not in html  # no cursor was sent
    html = operator_client.get(
        LIST, {"profile_id": PROFILE, "cursor": "01JM7CT0000000000000000002"}
    ).content.decode()
    assert query_of(reads(engine)[-1]) == {
        "profile_id": [PROFILE],
        "cursor": ["01JM7CT0000000000000000002"],
    }
    first = re.search(r'<a class="first-page" href="([^"]+)">First page</a>', html)
    assert first is not None
    assert markup.unescape(first.group(1)) == f"{LIST}?profile_id={PROFILE}"


def test_a_bad_cursor_is_dropped(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications", 200, json=m7("certifications"))
    html = operator_client.get(LIST, {"cursor": "not-a-ulid"}).content.decode()
    assert query_of(reads(engine)[0]) == {}
    assert CURSOR_DROPPED in html
    assert "First page" not in html


@pytest.mark.parametrize(
    ("params", "empty"),
    [
        ({}, "No certifications"),
        ({"profile_id": PROFILE}, "No certifications match these filters"),
        ({"cursor": CERT}, "No more certifications after this one"),
    ],
)
def test_empty_texts(
    operator_client: Client, engine: FakeEngine, params: dict[str, str], empty: str
) -> None:
    engine.respond("GET", "/certifications", 200, json={"items": [], "next_cursor": None})
    html = operator_client.get(LIST, params).content.decode()
    assert f'<p class="empty">{empty}</p>' in html
    assert '<p class="count">' not in html


def test_bad_item_is_one_row(operator_client: Client, engine: FakeEngine) -> None:
    bad = m7("certifications_bad_item")  # not schema-valid: answered unvalidated
    engine.side_effect("GET", "/certifications", lambda _r: httpx.Response(200, json=bad))
    html = operator_client.get(LIST).content.decode()
    rows = body_rows(html)
    assert len(rows) == 3
    rejected = rows[2]
    assert 'class="rejected"' in rejected
    assert "This certification couldn&#x27;t be read" in rejected
    assert "01JM7CT0000000000000000009" in rejected
    assert "<summary>More</summary>" in rejected
    assert '<p class="notice rejected-notice">1 certification couldn&#x27;t be read</p>' in html
    assert '<p class="count">2 certifications on this page</p>' in html


def test_a_422_after_the_form_passed_is_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        "/certifications",
        422,
        json={"error": {"code": "validation_error", "message": "bad", "details": {}}},
    )
    response = operator_client.get(LIST, {"profile_id": PROFILE})
    html = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in html
    assert '<form method="get" action="/engine/certifications/"' in html


# --- the detail -----------------------------------------------------------------------------------


def floors_section(html: str, key: str) -> str:
    found = re.search(rf'<section class="floor-table {key}">(.*?)</section>', html, re.S)
    assert found is not None, key
    return found.group(1)


def test_detail_floors_tables(operator_client: Client, engine: FakeEngine) -> None:
    cert = certification()
    cert["floors_failed"] = [
        {
            "metric_id": "NFR-QUAL-3.recall",
            "rule": "mean",
            "floor": 0.95,
            "mean": 0.91,
            "sd": 0.02,
            "n": 150,
            "seeds": 3,
            "upper_bound_95": None,
            "verdict": "fail",
            "intervention_type": None,
        }
    ]
    cert["floors_insufficient"][0].update(n=120, upper_bound_95=0.025)
    engine.respond("GET", "/certifications/{certification_id}", 200, json=cert)
    response = operator_client.get(DETAIL)
    html = response.content.decode()
    assert response.status_code == 200
    assert len(reads(engine)) == 1
    assert "<title>Certification …00000001 · Engine</title>" in html
    assert '<a href="/engine/certifications/" aria-current="page">Certifications</a>' in html
    headings = re.findall(r"<h4>(.*?)</h4>", html)
    assert headings == ["Passed (6)", "Failed (1)", "Insufficient evidence (1)"]
    failed = floors_section(html, "floors_failed")
    assert "<code>NFR-QUAL-3.recall</code>" in failed
    assert "<td>0.91 ± 0.02</td>" in failed
    assert "<td>0.95</td>" in failed
    assert '<td class="upper-bound">—</td>' in failed
    assert '<td class="intervention">—</td>' in failed
    insufficient = floors_section(html, "floors_insufficient")
    assert '<td class="upper-bound">0.025</td>' in insufficient
    assert "<td>120</td>" in insufficient
    assert '<span title="repair_rung_1">Repair rung 1</span>' in insufficient
    assert "<td>—</td>" in insufficient  # a null mean
    assert '<span title="rate">Rate</span>' in floors_section(html, "floors_passed")
    # What it certifies, the intervention types, the run and the recording.
    certifies = section(html, "certifies")
    assert f'<code class="hash">{FULL_FP}</code>' in certifies
    assert (
        f'<a class="other-certifications" href="{LIST}?fingerprint={FULL_FP.replace(":", "%3A")}">'
        "Other certifications of this fingerprint</a>"
    ) in certifies
    assert "generation → <code>claude-haiku-5-5</code>" in certifies
    assert '<span title="full">Full</span>' in certifies
    types = section(html, "intervention-types")
    assert "Covered: None" in text(types)
    assert "Lacking evidence: Repair rung 1" in text(types)
    run = section(html, "certification-run-facts")
    assert '<p class="judge">Judge calibrated: Yes</p>' in run
    assert '<p class="seed-count">Seeds: 3</p>' in run
    assert '<p class="model-tier">Model tier: <span title="other">Other</span></p>' in run
    assert "<dt><code>NFR-QUAL-2.false_support</code></dt><dd>345</dd>" in run
    assert "<dt>kind</dt><dd>hosted</dd>" in run
    assert '<code class="hash">' + "3" * 64 + "</code>" in run
    assert "<code>docs/evals/full-4710ae26cb73-2026-11-05.md</code>" in run
    assert "Recorded 2026-11-05 13:30:00 UTC via run" in text(run)
    status = section(html, "certification-status")
    assert text(status).startswith("Valid since 2026-11-05 13:00:00 UTC")


def test_an_empty_floors_table_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", "/certifications/{certification_id}", 200, json=certification(floors_failed=[])
    )
    html = operator_client.get(DETAIL).content.decode()
    failed = floors_section(html, "floors_failed")
    assert "<h4>Failed (0)</h4>" in failed
    assert '<p class="empty">None</p>' in failed
    assert "<table" not in failed


def test_an_untypable_floor_is_one_row(operator_client: Client, engine: FakeEngine) -> None:
    cert = certification()
    cert["floors_failed"] = [{"metric_id": "NFR-QUAL-3.recall", "rule": "mean"}]
    engine.respond("GET", "/certifications/{certification_id}", 200, json=cert)
    html = operator_client.get(DETAIL).content.decode()
    failed = floors_section(html, "floors_failed")
    assert "<h4>Failed (1)</h4>" in failed
    assert '<tr class="rejected">' in failed
    assert "This floor couldn&#x27;t be read" in failed
    assert "<summary>More</summary>" in failed


def test_stale_canary_links_the_run(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications/{certification_id}", 200, json=stale_canary())
    html = operator_client.get(DETAIL).content.decode()
    status = section(html, "certification-status")
    assert text(status).startswith(
        "Stale since 2026-11-02 09:14:00 UTC — a canary eval fell below a floor; the canary run"
    )
    assert (
        f'<a class="canary-run" href="/engine/certification-runs/{CANARY_RUN}/">the canary run</a>'
    ) in status
    assert (
        f'<p class="canary-note">{CANARY_NOTE} · '
        f'<a class="engine-request" href="{CANARY_LIST_REQUEST}">Engine request</a></p>'
    ) in status
    assert html.count(CANARY_LIST_REQUEST) == 1


def test_a_canary_stale_one_without_a_run_keeps_the_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        "/certifications/{certification_id}",
        200,
        json=stale_canary(invalidated_by_run_id=None),
    )
    status = section(operator_client.get(DETAIL).content.decode(), "certification-status")
    assert "canary-run" not in status
    assert CANARY_NOTE in status


@pytest.mark.parametrize(
    "cert",
    [
        certification(
            status="stale", invalidated_at="2026-11-02T09:14:00Z", invalidated_reason="drift"
        ),
        certification(),
    ],
    ids=["drift", "valid"],
)
def test_no_run_link_and_no_note_otherwise(
    operator_client: Client, engine: FakeEngine, cert: dict[str, Any]
) -> None:
    engine.respond("GET", "/certifications/{certification_id}", 200, json=cert)
    html = operator_client.get(DETAIL).content.decode()
    assert "canary-run" not in html
    assert CANARY_NOTE not in html
    assert CANARY_LIST_REQUEST not in html
    if cert["status"] == "stale":
        assert text(section(html, "certification-status")).startswith(
            "Stale since 2026-11-02 09:14:00 UTC — the model changed under the same version"
        )


def test_stale_without_time_or_reason(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/certifications/{certification_id}",
        200,
        json=certification(status="stale", invalidated_reason="manual"),
    )
    assert text(section(operator_client.get(DETAIL).content.decode(), "certification-status")) == (
        "Stale — withdrawn by an operator"
    )


def test_invalidate_link_d19(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications/{certification_id}", 200, json=certification())
    assert "Invalidate…" not in operator_client.get(DETAIL).content.decode()
    with override_settings(ROOT_URLCONF=M7_URLCONF):
        html = operator_client.get(DETAIL).content.decode()
        link = re.search(r'<a class="button invalidate" href="([^"]+)">Invalidate…</a>', html)
        assert link is not None
        target = urlsplit(markup.unescape(link.group(1)))
        assert target.path == f"/engine/certifications/{CERT}/invalidate/"
        assert parse_qs(target.query) == {
            "fingerprint": [FULL_FP],
            "profile": ["Full"],
            "next": [DETAIL],
        }
        engine.respond("GET", "/certifications/{certification_id}", 200, json=stale_canary())
        assert "Invalidate…" not in operator_client.get(DETAIL).content.decode()


def test_invalidate_link_names_an_unnamed_profile_by_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", "/certifications/{certification_id}", 200, json=certification(preset=None)
    )
    with override_settings(ROOT_URLCONF=M7_URLCONF):
        html = operator_client.get(DETAIL + "?x=1").content.decode()
    link = re.search(r'class="button invalidate" href="([^"]+)"', html)
    assert link is not None
    query = parse_qs(urlsplit(markup.unescape(link.group(1))).query)
    assert query["profile"] == [PROFILE]
    assert query["next"] == [DETAIL]


@pytest.mark.parametrize(
    "url",
    ["/engine/certifications/nope/", "/engine/certification-runs/nope/"],
)
def test_malformed_id_404_no_call(operator_client: Client, engine: FakeEngine, url: str) -> None:
    response = operator_client.get(url)
    assert response.status_code == 404
    assert engine.requests == []
    assert "the id in the address isn&#x27;t an Engine id" in response.content.decode()


def test_a_lower_case_id_renders_in_place(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications/{certification_id}", 200, json=certification())
    response = operator_client.get(f"/engine/certifications/ {CERT.lower()}/")
    assert response.status_code == 200
    assert reads(engine)[0].url.path == f"/certifications/{CERT}"


def test_not_found_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/certifications/{certification_id}",
        404,
        json={"error": {"code": "certification_not_found", "message": "no", "details": {}}},
    )
    response = operator_client.get(DETAIL)
    html = response.content.decode()
    assert response.status_code == 404
    assert '<p class="not-found">No such certification</p>' in html
    assert "<title>Certification …00000001 · Engine</title>" in html


def test_profile_page_links_its_certifications(
    operator_client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_id = engine_fixture("get_engine_profile")["id"]
    for _ in fresh_cache():
        for _fake in fake_engine(monkeypatch):
            html = operator_client.get(f"/profiles/{profile_id}/").content.decode()
    version = re.search(r'<dd class="version">(.*?)</dd>', html, re.S)
    assert version is not None
    assert version.group(1).endswith(
        f'Versions</a> · <a class="certifications" href="{LIST}?profile_id={profile_id}">'
        "Certifications</a>"
    )


# --- D17: storyless pages -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "route"),
    [
        (LIST, "/certifications"),
        (DETAIL, "/certifications/{certification_id}"),
        ("/engine/certification-runs/01JM7CR0000000000000000001/", "/certification-runs/{run_id}"),
        (
            "/engine/certification-runs/01JM7CR0000000000000000001/?partial=1",
            "/certification-runs/{run_id}",
        ),
    ],
    ids=["list", "detail", "run", "run-partial"],
)
def test_erasure_pending_storyless(
    operator_client: Client, engine: FakeEngine, url: str, route: str
) -> None:
    engine.respond(
        "GET",
        route,
        503,
        json={"error": {"code": "erasure_pending", "message": "held", "details": {}}},
        headers={"Retry-After": "5"},
    )
    response = operator_client.get(url)
    html = response.content.decode()
    assert '<p class="warning held-message" role="status">' in html
    assert "Delete</a>" not in html
    if "partial" in url:
        assert response.status_code == 200
        assert "hx-trigger" not in html
        assert "<title>" not in html
    else:
        assert response.status_code == 503
        assert response["Retry-After"] == "5"
        assert '<a href="/engine/certifications/" aria-current="page">Certifications</a>' in html
