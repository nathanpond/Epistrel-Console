"""#313: a certification run's job page (E7's ``certification_run`` kind), over #292's
``m7/certification_run_running.json`` and ``certification_run.json`` (variants inline, E16): it
polls every 5 s until the run ends, settles the request's #44 row on the first terminal read, and
summarises the report by the keys the Engine writes."""

import copy
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.job_poll import RETRYING_TEXT
from console.models import EngineOperation
from console.views import certifications as views
from console.views import story_pages
from console.views.certifications import NO_CERTIFICATION
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

RUN = "01JM7CR0000000000000000001"
JOB = "01JM7JB0000000000000000005"
CERT = "01JM7CT0000000000000000001"
STORY = "01JM7ST0000000000000000001"
URL = f"/engine/certification-runs/{RUN}/"
PARTIAL = f"{URL}?partial=1"
ROUTE = "/certification-runs/{run_id}"


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def run(name: str = "certification_run", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7(name)
    body.update(changes)
    return body


def canary(**changes: Any) -> dict[str, Any]:
    """A completed canary run that measured a miss (the Engine's report shape at origin/main)."""
    report = {
        "verdict": "fail",
        "suites": {"quality": "fail", "smoke": "pass"},
        "metrics": [
            {
                "id": "NFR-QUAL-3.recall",
                "requirement": "NFR-QUAL-3",
                "value": 0.91,
                "floor": 0.95,
                "sample_size": 150,
                "verdict": "fail",
            },
            {
                "id": "smoke.turn_completed",
                "requirement": "smoke",
                "value": 1.0,
                "floor": 0.99,
                "sample_size": 200,
                "verdict": "pass",
            },
        ],
        "missed_floors": ["NFR-QUAL-3.recall"],
        "run_id": "full-4710ae26cb73-2026-11-02",
    }
    base = run(
        id="01JM7CR0000000000000000002",
        kind="canary",
        job_id=None,
        certification_id=None,
        requested_by="system",
        report=report,
    )
    base.update(changes)
    return base


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def queued_row(job_id: str = JOB) -> EngineOperation:
    """The #314 request's 202 row (#44), still ``queued``."""
    return EngineOperation.objects.create(
        kind="request_certification_run_profile",
        http_method="POST",
        path="/engine-profiles/00000000000000000000PRF002/certification-runs",
        http_status=202,
        outcome="queued",
        job_id=job_id,
        response={"job_id": job_id, "run_id": RUN},
    )


def poll_block(html: str) -> str:
    found = re.search(r'<div id="job-poll"[^>]*>', html)
    assert found is not None
    return found.group(0)


def test_running_polls_every_5s(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=run("certification_run_running"))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<title>Certification run …00000001 · Engine</title>" in html
    assert '<a href="/engine/certifications/" aria-current="page">Certifications</a>' in html
    assert poll_block(html) == (
        f'<div id="job-poll" class="job-poll" hx-get="{URL}?partial=1" hx-trigger="every 5s" '
        'hx-swap="outerHTML">'
    )
    assert '<strong class="job-status-text">Running</strong>' in html
    assert f"Job <code>{RUN}</code>" in html
    kind = '<span title="certification">Requested certification</span>'
    assert f'<dd class="kind">{kind}</dd>' in html
    assert '<p class="suites">Suites: <code>quality</code>, <code>smoke</code></p>' in html
    assert '<p class="seeds">Seeds: 3</p>' in html
    assert '<p class="sample">Sample: every case</p>' in html
    assert "<dt>Model calls</dt><dd>1,200</dd>" in html
    assert "<dt>Tokens</dt><dd>2,400,000</dd>" in html
    assert "<dt>Duration</dt><dd>1h 30m</dd>" in html
    assert "<dt>Cost</dt><dd>USD 18.5</dd>" in html
    assert "Requested by " in html
    assert "console:nate" in html
    assert "Finished —" in html
    assert "run-report" not in html
    assert NO_CERTIFICATION not in html


def test_the_completed_run_renders_no_trigger(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=run())
    html = operator_client.get(URL).content.decode()
    assert "hx-trigger" not in html
    assert '<strong class="job-status-text">Completed</strong>' in html


def test_completed_links_the_certification(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=run())
    html = operator_client.get(URL).content.decode()
    assert (
        '<p class="recorded-certification">Recorded certification '
        f'<a href="/engine/certifications/{CERT}/"><code title="{CERT}">…00000001</code></a></p>'
    ) in html
    # The fixture's report has none of the summarised keys: all of it goes under More.
    assert 'class="report-more"' in html
    assert "floors_passed" in html
    assert "Overall:" not in html
    assert "Outcome:" not in html


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (run, '<span title="certification">Requested certification</span>'),
        (canary, '<span title="canary">Canary check</span>'),
        # **Complement:** a kind the Engine doesn't store today is humanised, raw in ``title``.
        (lambda: run(kind="nightly_check"), '<span title="nightly_check">Nightly check</span>'),
    ],
    ids=["certification", "canary", "unknown"],
)
def test_the_kind_text(operator_client: Client, engine: FakeEngine, body: Any, kind: str) -> None:
    """#348: the run page's Kind reads the Engine's ``kind`` (``certification`` or ``canary``,
    ``core/tables/certifications.py``) through ``KIND_TEXT``."""
    answer = body()
    engine.respond("GET", ROUTE, 200, json=answer)
    html = operator_client.get(f"/engine/certification-runs/{answer['id']}/").content.decode()
    assert f'<dd class="kind">{kind}</dd>' in html


def test_no_certification_recorded(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=run(certification_id=None))
    html = operator_client.get(URL).content.decode()
    assert f'<p class="recorded-certification">{NO_CERTIFICATION}</p>' in html


def test_the_report_summary(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=canary())
    html = operator_client.get("/engine/certification-runs/01JM7CR0000000000000000002/").content
    page = html.decode()
    assert '<dd class="kind"><span title="canary">Canary check</span></dd>' in page
    assert "Scheduled by the Engine · " in page
    assert '<p class="overall">Overall: <span title="fail">failed</span></p>' in page
    assert '<p class="outcome">Outcome: <span title="">Missed</span></p>' in page
    assert '<tr><td><code>quality</code></td><td><span title="fail">failed</span></td></tr>' in page
    assert '<tr><td><code>smoke</code></td><td><span title="pass">passed</span></td></tr>' in page
    assert '<p class="missed-floors">Missed: <code>NFR-QUAL-3.recall</code></p>' in page
    assert (
        '<tr class="missed"><td><code>NFR-QUAL-3.recall</code></td><td>NFR-QUAL-3</td>'
        "<td>0.91</td><td>0.95</td><td>150</td>"
        '<td><span title="fail">failed</span> (missed)</td></tr>'
    ) in page
    assert "<tr><td><code>smoke.turn_completed</code></td>" in page
    assert "full-4710ae26cb73-2026-11-02" in page  # an unknown key, under More
    # A canary records no certification: no recorded line at all.
    assert "recorded-certification" not in page


def test_an_explicit_outcome_wins(operator_client: Client, engine: FakeEngine) -> None:
    body = canary()
    body["report"].update(outcome="inconclusive", missed_floors=[])
    engine.respond("GET", ROUTE, 200, json=body)
    page = operator_client.get(URL).content.decode()
    assert '<p class="outcome">Outcome: <span title="inconclusive">Inconclusive</span></p>' in page


def test_no_outcome_is_guessed(operator_client: Client, engine: FakeEngine) -> None:
    body = canary()
    body["report"]["missed_floors"] = []
    engine.respond("GET", ROUTE, 200, json=body)
    assert "Outcome:" not in operator_client.get(URL).content.decode()


def test_the_story_is_linked(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=run(story_id=STORY, sample=40))
    page = operator_client.get(URL).content.decode()
    assert (
        f'<dd class="story"><a href="/stories/{STORY}/inspect/"><code title="{STORY}">'
        "…00000001</code></a></dd>"
    ) in page
    assert '<p class="sample">Sample: 40 per suite</p>' in page


def test_first_terminal_poll_settles_the_request_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    row = queued_row()
    engine.respond("GET", ROUTE, 200, json=run())
    first = operator_client.get(PARTIAL)
    assert first.status_code == 200
    assert "<title>" not in first.content.decode()
    row.refresh_from_db()
    assert (row.outcome, row.error_code) == (EngineOperation.Outcome.OK, "")
    # **Complement:** a second poll changes nothing.
    settled = (row.outcome, row.error_code, row.response, row.http_status)
    operator_client.get(PARTIAL)
    row.refresh_from_db()
    assert (row.outcome, row.error_code, row.response, row.http_status) == settled
    assert EngineOperation.objects.count() == 1


def test_a_running_poll_settles_nothing(operator_client: Client, engine: FakeEngine) -> None:
    row = queued_row()
    engine.respond("GET", ROUTE, 200, json=run("certification_run_running"))
    html = operator_client.get(PARTIAL).content.decode()
    assert 'hx-trigger="every 5s"' in html
    row.refresh_from_db()
    assert row.outcome == EngineOperation.Outcome.QUEUED


def test_a_canary_run_settles_nothing(operator_client: Client, engine: FakeEngine) -> None:
    row = queued_row()
    engine.respond("GET", ROUTE, 200, json=canary())
    response = operator_client.get(PARTIAL)
    assert response.status_code == 200
    row.refresh_from_db()
    assert row.outcome == EngineOperation.Outcome.QUEUED


def test_failed_run_shows_error_and_settles_failed(
    operator_client: Client, engine: FakeEngine
) -> None:
    row = queued_row()
    engine.respond(
        "GET",
        ROUTE,
        200,
        json=run(status="failed", certification_id=None, report=None, error="TimeoutError"),
    )
    html = operator_client.get(URL).content.decode()
    assert '<strong class="job-status-text">Failed</strong>' in html
    assert '<p class="warning"><strong>TimeoutError</strong></p>' in html
    assert "hx-trigger" not in html
    assert f'<p class="recorded-certification">{NO_CERTIFICATION}</p>' in html
    row.refresh_from_db()
    assert (row.outcome, row.error_code) == (EngineOperation.Outcome.ERROR, "TimeoutError")


def test_partial_only_the_block(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ROUTE, 200, json=run("certification_run_running"))
    html = operator_client.get(PARTIAL).content.decode()
    assert html.lstrip().startswith('<div id="job-poll"')
    assert "<title>" not in html
    assert "engine-links" not in html
    assert '<strong class="job-status-text">Running</strong>' in html


def test_partial_retries_on_503(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        ROUTE,
        503,
        json={"error": {"code": "engine_unavailable", "message": "busy", "details": {}}},
    )
    html = operator_client.get(PARTIAL).content.decode()
    assert 'hx-trigger="every 5s"' in html
    assert RETRYING_TEXT in html


def test_partial_shows_the_panel_for_other_failures(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        ROUTE,
        422,
        json={"error": {"code": "validation_error", "message": "bad", "details": {}}},
    )
    html = operator_client.get(PARTIAL).content.decode()
    assert "hx-trigger" not in html
    assert 'class="engine-error' in html


def test_run_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        ROUTE,
        404,
        json={"error": {"code": "certification_run_not_found", "message": "no", "details": {}}},
    )
    page = operator_client.get(URL)
    assert page.status_code == 404
    assert '<p class="not-found">No such certification run</p>' in page.content.decode()
    assert "<title>Certification run …00000001 · Engine</title>" in page.content.decode()
    partial = operator_client.get(PARTIAL)
    assert partial.status_code == 200
    html = partial.content.decode()
    assert '<p class="not-found">No such certification run</p>' in html
    assert "hx-trigger" not in html


def test_a_malformed_run_id_on_the_partial_is_404_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get("/engine/certification-runs/nope/?partial=1")
    assert response.status_code == 404
    assert engine.requests == []
