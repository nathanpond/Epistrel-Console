"""Request a certification run for a profile or a story, only after seeing its estimate (#314,
E8, E21)."""

import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.core import signing
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import certify, profiles, story_config, story_pages
from console.views.certify import ESTIMATE_NOTE, IN_PROGRESS_TEXT, UNAVAILABLE_TEXT
from tests.backfill.helpers import m7
from tests.fakes.engine import FakeEngine
from tests.fakes.guarantees import answer_guarantees
from tests.fakes.ownership import answer_relations
from tests.inspect.helpers import full_v4
from tests.profile_helpers import SCHEMA, SETTINGS, deployment, fresh_cache, schema_body
from tests.ratings.helpers import error, hidden
from tests.ratings.helpers import text as tag_free

pytestmark = pytest.mark.django_db

FULL = "00000000000000000000PRF002"
STORY = "01JM7ST0000000000000000001"
RUN = "01JM7CR0000000000000000001"
KEY = "01JM7KY0000000000000000009"
PROFILE_URL = f"/profiles/{FULL}/certify/"
STORY_URL = f"/stories/{STORY}/certify/"
PROFILE_RUNS = "/engine-profiles/{profile_id}/certification-runs"
STORY_RUNS = "/stories/{story_id}/certification-runs"
PROFILE_RUNS_PATH = f"/engine-profiles/{FULL}/certification-runs"
STORY_RUNS_PATH = f"/stories/{STORY}/certification-runs"
FINGERPRINT = "sha256:4710ae26cb731c016d2b86c747e7d3e40e7e9b0a3cfda00bfe60118230039b4b"
NUMBERS = "1,200 model calls · ~2,400,000 tokens · ~1h 30m with the calls one after another"
NOT_PRICED = "Cost: not priced by this deployment's model matrix"


def full(**changes: Any) -> dict[str, Any]:
    (found,) = [p for p in m7("presets")["items"] if p["id"] == FULL]
    return {**found, **changes}


def versions(*numbers: int) -> dict[str, Any]:
    return {
        "items": [
            {"version": n, "created_at": "2026-10-01T09:00:00Z", "created_by": "system"}
            for n in numbers
        ]
    }


def estimate(**changes: Any) -> dict[str, Any]:
    body = m7("certification_estimate")
    body["estimate"].update(changes)
    return body


def story_estimate(**changes: Any) -> dict[str, Any]:
    body = m7("story_certification_estimate")
    body["estimate"].update(changes)
    return body


@pytest.fixture(autouse=True)
def _fresh_schema_cache() -> Iterator[None]:
    yield from fresh_cache()


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    answer_relations(fake)
    answer_guarantees(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (certify, profiles, story_pages, story_config):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", "/engine-profiles/{profile_id}", 200, json=full())
    fake.respond("GET", "/engine-profiles/{profile_id}/versions", 200, json=versions(1, 2, 3, 4))
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    fake.respond("GET", SETTINGS, 200, json=deployment())
    fake.respond("GET", SCHEMA, 200, json=schema_body())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def routes(fake: FakeEngine) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in fake.requests]


def posts(fake: FakeEngine, path: str) -> list[Any]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == path]


def body(request: Any) -> Any:
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def form_data(**changes: str) -> dict[str, str]:
    return {"suites": "", "seeds": "3", "sample": "50", "idempotency_key": KEY, **changes}


def confirm_data(html: str, **extra: str) -> dict[str, str]:
    two_stage = html[html.index('class="two-stage"') :]
    return {
        "confirm_token": hidden(two_stage, "confirm_token"),
        "idempotency_key": hidden(two_stage, "idempotency_key"),
        **extra,
    }


def estimated(client: Client, engine: FakeEngine, url: str = PROFILE_URL, **changes: str) -> str:
    response = client.post(url, form_data(**changes))
    assert response.status_code == 200
    return str(response.content.decode())


def text(page: str) -> str:
    """The page's text, entities unescaped."""
    return unescape(tag_free(page))


def title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.S)
    assert match
    return match.group(1).strip()


def options(html: str) -> list[tuple[str, str, bool]]:
    select = html[html.index('name="version"') :]
    select = select[: select.index("</select>")]
    return [
        (value, label, "selected" in attrs)
        for value, attrs, label in re.findall(r'<option value="([^"]*)"([^>]*)>([^<]*)<', select)
    ]


# --- the form, with no call on GET ----------------------------------------------------------------


def test_profile_get_makes_no_certification_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(PROFILE_URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert routes(engine) == [
        f"GET /engine-profiles/{FULL}",
        f"GET /engine-profiles/{FULL}/versions",
    ]
    assert not [r for r in engine.requests if r.url.path.endswith("/certification-runs")]
    assert title(html) == "Request a certification run · Full"
    assert options(html) == [
        ("", "Latest (v4)", True),
        ("4", "v4", False),
        ("3", "v3", False),
        ("2", "v2", False),
        ("1", "v1", False),
    ]
    seeds = re.search(r'<input type="number" name="seeds"[^>]*>', html)
    sample = re.search(r'<input type="number" name="sample"[^>]*>', html)
    assert seeds
    assert sample
    for tag in (seeds.group(0), sample.group(0)):  # C10: no HTML min/max
        assert 'step="1"' in tag
        assert "min=" not in tag
        assert "max=" not in tag
    assert 'value="3"' in seeds.group(0)
    shown = text(html)
    assert "the estimate lists them" in shown
    assert "Under 300 cases" in shown
    form = html[html.index('class="certify-form"') :]
    assert form[: form.index("</form>")].count('type="submit"') == 1
    assert ">Estimate</button>" in html
    assert "Start the run" not in html


def test_profile_get_preselects_a_known_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(f"{PROFILE_URL}?version=2").content.decode()
    assert ("2", "v2", True) in options(html)
    html = operator_client.get(f"{PROFILE_URL}?version=9").content.decode()
    assert options(html)[0] == ("", "Latest (v4)", True)  # ignored, no notice
    assert "Ignored" not in html


def test_a_failed_versions_read_leaves_latest_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", "/engine-profiles/{profile_id}/versions", 503, json=error("database_unavailable")
    )
    html = operator_client.get(PROFILE_URL).content.decode()
    assert options(html) == [("", "Latest (v4)", True)]
    assert certify.VERSIONS_UNREAD in text(html)
    # **Complement:** a version outside the (unread) versions is Django's choice error, no call.
    engine.reset()
    response = operator_client.post(PROFILE_URL, form_data(version="2"))
    assert "Select a valid choice" in response.content.decode()
    assert posts(engine, PROFILE_RUNS_PATH) == []


def test_profile_read_404_and_other_failures(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/engine-profiles/{profile_id}", 404, json=error("not_found"))
    response = operator_client.get(PROFILE_URL)
    assert response.status_code == 404
    assert "No such profile" in response.content.decode()
    assert 'class="certify-form"' not in response.content.decode()
    engine.respond("GET", "/engine-profiles/{profile_id}", 503, json=error("database_unavailable"))
    response = operator_client.get(PROFILE_URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in html
    assert 'class="certify-form"' not in html


def test_story_get_makes_no_certification_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(STORY_URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert routes(engine) == [f"GET /stories/{STORY}", f"GET /stories/{STORY}/effective-config"]
    assert title(html) == "Request a certification run · Harbour Ledger"
    assert 'name="version"' not in html
    for name in ("suites", "seeds", "sample"):
        assert f'name="{name}"' in html
    assert 'class="story-links"' in html  # #296's frame


# --- estimate first; nothing is written -----------------------------------------------------------


def test_estimate_sends_confirm_false_and_shows_numbers(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=estimate(cost=None))
    response = operator_client.post(PROFILE_URL, form_data())
    html = response.content.decode()
    (sent,) = posts(engine, PROFILE_RUNS_PATH)
    assert body(sent) == {"confirm": False, "seeds": 3, "sample": 50}
    assert sent.headers["Idempotency-Key"] == KEY
    shown = text(html)
    assert NUMBERS in shown
    assert NOT_PRICED in shown
    assert f'Fingerprint: <code class="hash">{FINGERPRINT}</code>' in html
    assert (
        f'<a class="certifications-link" href="/engine/certifications/?fingerprint='
        f'{FINGERPRINT.replace(":", "%3A")}">Certifications for this configuration</a>'
    ) in html
    assert "Suites: quality, smoke" in shown
    assert ESTIMATE_NOTE in shown
    assert ">Start the run</button>" in html
    # The form stays editable above the estimate, with what was posted.
    assert html.index('class="certify-form"') < html.index('class="certification-estimate"')
    assert re.search(r'name="sample"[^>]*value="50"', html)
    # **Complement:** no 202 was answered, no #44 row is queued, and there's no redirect.
    assert response.status_code == 200
    assert not EngineOperation.objects.filter(outcome=EngineOperation.Outcome.QUEUED).exists()
    # The confirm carries a fresh key, never the estimate's.
    assert confirm_data(html)["idempotency_key"] != KEY


@pytest.mark.parametrize(("cost", "shown"), [(12.5, "Cost: USD 12.50"), (0, "Cost: USD 0.00")])
def test_cost_shown_when_priced(
    operator_client: Client, engine: FakeEngine, cost: float, shown: str
) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=estimate(cost=cost))
    assert shown in text(estimated(operator_client, engine))


def test_estimate_sends_the_set_members_only(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=estimate())
    estimated(
        operator_client, engine, suites=" quality,smoke  quality,, Extra ", sample="", version="2"
    )
    (sent,) = posts(engine, PROFILE_RUNS_PATH)
    assert body(sent) == {
        "confirm": False,
        "suites": ["quality", "smoke", "Extra"],
        "seeds": 3,
        "version": 2,
    }


def test_duration_hours() -> None:
    from console.impact import duration_text

    assert duration_text(7800) == "2h 10m"
    assert duration_text(7200) == "2h 0m"
    assert duration_text(3599) == "59m 59s"
    assert duration_text(59) == "59 s"


# --- start the run --------------------------------------------------------------------------------


def test_start_resends_the_signed_body(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=estimate())
    html = estimated(operator_client, engine)
    data = confirm_data(html)
    engine.reset()
    engine.respond("POST", PROFILE_RUNS, 202, json=m7("certification_queued"))
    # **Complement:** a changed seeds field posted beside the token is ignored.
    response = operator_client.post(PROFILE_URL, {**data, "seeds": "1"})
    (sent,) = posts(engine, PROFILE_RUNS_PATH)
    assert body(sent) == {"confirm": True, "suites": ["quality", "smoke"], "seeds": 3, "sample": 50}
    assert sent.headers["Idempotency-Key"] == data["idempotency_key"]
    assert response.status_code == 303
    assert response["Location"] == f"/engine/certification-runs/{RUN}/"
    (flash,) = flashes(response)
    assert flash.startswith("Certification run queued — 1,200 model calls estimated — ")
    assert flash.endswith("idempotency key <code>01JM7KY0000000000000000001</code>")
    row = EngineOperation.objects.get(outcome=EngineOperation.Outcome.QUEUED)
    assert (row.entity_type, row.entity_id) == ("engine_profile", FULL)
    assert row.job_id == "01JM7JB0000000000000000005"


def test_an_expired_token_makes_zero_calls(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=estimate())
    data = confirm_data(estimated(operator_client, engine))
    stale = signing.loads(data["confirm_token"], salt="console.twostage.certify-profile")
    engine.reset()
    for token in (
        "forged",
        twostage.sign(
            "certify-profile", "00000000000000000000PRF001", stale["body"], stale["base"]
        ),
        twostage.sign("certify-story", FULL, stale["body"], stale["base"]),
        twostage.sign("certify-profile", FULL, {"confirm": False}, stale["base"]),
    ):
        response = operator_client.post(PROFILE_URL, {**data, "confirm_token": token})
        assert response.status_code == 303
        assert response["Location"] == PROFILE_URL
        assert twostage.EXPIRED_TEXT in flashes(response)
    unsign = twostage.unsign
    monkeypatch.setattr(
        twostage, "unsign", lambda kind, token, max_age=-1: unsign(kind, token, max_age=max_age)
    )
    response = operator_client.post(PROFILE_URL, data)
    assert twostage.EXPIRED_TEXT in flashes(response)
    assert engine.requests == []


def test_story_expired_token_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(STORY_URL, {"confirm_token": "forged", "idempotency_key": KEY})
    assert response.status_code == 303
    assert twostage.EXPIRED_TEXT in flashes(response)
    assert engine.requests == []


def test_story_request_never_sends_version(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", STORY_RUNS, 200, json=story_estimate(cost=None))
    html = estimated(operator_client, engine, STORY_URL, version="2")
    (sent,) = posts(engine, STORY_RUNS_PATH)
    assert body(sent) == {"confirm": False, "seeds": 3, "sample": 50}
    assert NUMBERS in text(html)
    data = confirm_data(html)
    engine.reset()
    queued = {**m7("certification_queued"), "timeline_revision": 20}
    engine.respond("POST", STORY_RUNS, 202, json=queued)
    response = operator_client.post(STORY_URL, data)
    (sent,) = posts(engine, STORY_RUNS_PATH)
    assert "version" not in body(sent)
    assert body(sent)["confirm"] is True
    assert response.status_code == 303
    assert response["Location"] == f"/engine/certification-runs/{RUN}/"
    row = EngineOperation.objects.get(outcome=EngineOperation.Outcome.QUEUED)
    assert row.story_id == STORY


def test_run_id_at_estimate_is_bad_response(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=m7("certification_queued"))
    response = operator_client.post(PROFILE_URL, form_data())
    html = response.content.decode()
    assert response.status_code == 200
    assert "bad_response" in html
    assert "Start the run" not in html
    assert 'class="certification-estimate"' not in html
    assert hidden(html, "idempotency_key") == KEY  # kept: something may have been queued
    assert len(posts(engine, PROFILE_RUNS_PATH)) == 1


def test_a_2xx_without_run_id_on_confirm_is_bad_response(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", PROFILE_RUNS, 200, json=estimate())
    data = confirm_data(estimated(operator_client, engine))
    engine.reset()
    engine.respond("POST", PROFILE_RUNS, 202, json=estimate())  # no run_id
    response = operator_client.post(PROFILE_URL, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert "bad_response" in html
    assert confirm_data(html) == data  # E21: the same confirm, token and key
    assert len(posts(engine, PROFILE_RUNS_PATH)) == 1


# --- refusals change nothing and say why ----------------------------------------------------------

REFUSALS = [
    pytest.param(
        "profile",
        409,
        error("certification_run_in_progress", run_id=RUN, fingerprint=FINGERPRINT),
        None,
        IN_PROGRESS_TEXT,
        id="in_progress",
    ),
    pytest.param(
        "profile", 503, error("certification_unavailable"), None, UNAVAILABLE_TEXT, id="unavailable"
    ),
    pytest.param(
        "profile",
        422,
        error(
            "validation_error",
            "invalid request",
            field="body.suites.0",
            errors=[{"field": "body.suites.0", "message": "suite 'nope' is not enabled"}],
        ),
        None,
        "suite 'nope' is not enabled",
        id="validation_suites",
    ),
    pytest.param(
        "story",
        423,
        error("story_busy", reason="in_flight"),
        {"Retry-After": "4"},
        "Story busy — another turn is running · retry after 4 s",
        id="story_busy",
    ),
    pytest.param(
        "story",
        409,
        error(
            "story_importing",
            job_id="01JM7JB0000000000000000007",
            stage="restoring",
            progress={"done": 2, "total": 5},
        ),
        None,
        "This story is being imported",
        id="story_importing",
    ),
]


@pytest.mark.parametrize(("subject", "status", "answer", "headers", "shown"), REFUSALS)
@pytest.mark.parametrize("stage", ["estimate", "confirm"])
def test_refusals_change_nothing(
    operator_client: Client,
    engine: FakeEngine,
    subject: str,
    status: int,
    answer: dict[str, Any],
    headers: dict[str, str] | None,
    shown: str,
    stage: str,
) -> None:
    url, route, path = (
        (PROFILE_URL, PROFILE_RUNS, PROFILE_RUNS_PATH)
        if subject == "profile"
        else (STORY_URL, STORY_RUNS, STORY_RUNS_PATH)
    )
    data: dict[str, str] = form_data(suites="nope")
    if stage == "confirm":
        good = estimate() if subject == "profile" else story_estimate()
        engine.respond("POST", route, 200, json=good)
        data = confirm_data(estimated(operator_client, engine, url))
        engine.reset()
    engine.respond("POST", route, status, json=answer, headers=headers)  # E16: inline error_json
    response = operator_client.post(url, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert shown in text(html)
    assert len(posts(engine, path)) == 1  # **Complement:** only the one POST, no retry
    assert not EngineOperation.objects.filter(outcome=EngineOperation.Outcome.QUEUED).exists()
    assert ">Start the run</button>" not in html
    if answer["error"]["code"] == "validation_error":
        field = html[html.index('class="field field-suites"') :]
        assert "suite &#x27;nope&#x27; is not enabled" in field[: field.index("</p>")]
    if answer["error"]["code"] == "certification_run_in_progress":
        query = FINGERPRINT.replace(":", "%3A")
        assert (
            f'<a href="/engine/certifications/?fingerprint={query}">{IN_PROGRESS_TEXT}</a>' in html
        )
        assert f'href="/engine/certification-runs/{RUN}/">The run in progress</a>' in html
    if stage == "confirm":
        assert certify.REESTIMATE_TEXT in text(html)
        assert re.search(r'name="suites"[^>]*value="quality, smoke"', html)


def test_profile_404_at_request_is_no_such_profile(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", PROFILE_RUNS, 404, json=error("not_found"))
    response = operator_client.post(PROFILE_URL, form_data())
    assert response.status_code == 200
    assert certify.NOT_FOUND_TEXT in text(response.content.decode())


def test_other_errors_are_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", STORY_RUNS, 404, json=error("not_found"))
    response = operator_client.post(STORY_URL, form_data())
    html = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in html
    assert "not_found" in html
    assert hidden(html, "idempotency_key") != KEY  # an answered refusal mints a fresh key


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"seeds": "0"}, "seeds", "Enter a whole number from 1 to 5"),
        ({"seeds": "6"}, "seeds", "Enter a whole number from 1 to 5"),
        ({"seeds": "2.5"}, "seeds", "Enter a whole number from 1 to 5"),
        ({"seeds": ""}, "seeds", "Enter a whole number from 1 to 5"),
        ({"sample": "9"}, "sample", "Enter a whole number of at least 10"),
        ({"sample": "ten"}, "sample", "Enter a whole number of at least 10"),
    ],
)
def test_invalid_numbers_no_call(
    operator_client: Client, engine: FakeEngine, changes: dict[str, str], field: str, message: str
) -> None:
    for url in (PROFILE_URL, STORY_URL):
        engine.reset()
        response = operator_client.post(url, form_data(**changes))
        html = response.content.decode()
        assert response.status_code == 200
        row = html[html.index(f'class="field field-{field}"') :]
        assert message in row[: row.index("</p>")]
        assert not [r for r in engine.requests if r.method == "POST"]


# --- entry links ----------------------------------------------------------------------------------


def test_entry_links(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"/profiles/{FULL}/").content.decode()
    actions = html[html.index('class="profile-actions"') :]
    actions = actions[: actions.index("</p>")]
    assert f'<a class="button certify" href="{PROFILE_URL}">Request a certification run…</a>' in (
        actions
    )
    # An older version links its own version, preselected on the page.
    engine.respond("GET", "/engine-profiles/{profile_id}", 200, json=full(version=2))
    html = operator_client.get(f"/profiles/{FULL}/?version=2").content.decode()
    assert f'href="{PROFILE_URL}?version=2">Request a certification run…</a>' in html
    # A deleted profile hides it, as Edit is hidden.
    engine.respond(
        "GET", "/engine-profiles/{profile_id}", 200, json=full(deleted_at="2026-11-01T00:00:00Z")
    )
    html = operator_client.get(f"/profiles/{FULL}/").content.decode()
    assert "Request a certification run…" not in html
    # Config's quality line.
    engine.respond("GET", "/engine-profiles", 200, json=m7("presets"))
    engine.respond("GET", "/engine-profiles", 200, json=m7("presets"), query={"preset": "true"})
    config = operator_client.get(f"/stories/{STORY}/config/").content.decode()
    quality = re.search(r"<dt>Quality status</dt><dd[^>]*>(.*?)</dd>", config, re.S)
    assert quality
    assert quality.group(1).endswith(
        f' · <a class="certify-link" href="{STORY_URL}">Request a certification run…</a>'
    )
