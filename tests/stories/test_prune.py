"""#317: the story's Prune page (``stories:prune``) and its typed-name confirm
(``stories:prune-confirm``), against the schema-validating fake Engine over #292's
``m7/story.json`` cast, ``full_v4()`` and the ``m7/prune_*.json`` answers: the GET (one
``list_prune_runs``, never a prune), the dry run (``ignore_warnings: false``, narrowing), the
confirm (E9: the story's name typed exactly; opening it calls nothing), the apply (one POST with
the dry run's revision as the expected revision) and the refusals."""

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from html import unescape
from typing import Any, ClassVar

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console import pruning, twostage
from console.confirm import TYPED_NAME_MISMATCH
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import prune as prune_views
from console.views import story_pages
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import full_v4
from tests.play.helpers import error_json
from tests.rewrites.helpers import STORY, m7

pytestmark = pytest.mark.django_db

NAME = "Harbour Ledger"
URL = f"/stories/{STORY}/prune/"
CONFIRM_URL = f"{URL}confirm/"
PRUNE = f"/stories/{STORY}/prune"
RUNS = f"/stories/{STORY}/prune-runs"
STORY_READ = f"/stories/{STORY}"
CONFIG_READ = f"/stories/{STORY}/effective-config"
PRUNE_ROUTE = "/stories/{story_id}/prune"
RUNS_ROUTE = "/stories/{story_id}/prune-runs"
TAIL = "01JM7CN0000000000000000011"
DORMANT = "01JM7CN0000000000000000012"
KEY = "01JM7KY0000000000000000009"
TOKEN = re.compile(r'name="confirm_token" value="([^"]+)"')
REPORT = re.compile(r'name="report_token" value="([^"]+)"')


def story(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("story")
    body.update(changes)
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    fake.respond("GET", RUNS_ROUTE, 200, json=m7("prune_runs"))
    fake.respond("POST", PRUNE_ROUTE, 200, json=m7("prune_impact"))

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(prune_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


@pytest.fixture
def client(operator_client: Client) -> Client:
    return operator_client


def calls(fake: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in fake.requests]


def prunes(fake: FakeEngine) -> list[httpx.Request]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == PRUNE]


def body_of(request: httpx.Request) -> Any:
    return json.loads(request.content)


def text(response: Any) -> str:
    return unescape(response.content.decode())


def dry_run(client: Client, **data: Any) -> Any:
    return client.post(URL, {"idempotency_key": KEY, **data})


def token_of(html: str) -> str:
    found = TOKEN.findall(html)
    assert found, "no confirm token on the page"
    return str(found[0])


def first_stage(client: Client) -> str:
    response = dry_run(client)
    assert response.status_code == 200
    return token_of(response.content.decode())


def confirm(client: Client, token: str, typed: str | None = NAME, **extra: Any) -> Any:
    data = {"confirm_token": token, "idempotency_key": KEY, "confirmed": "1", **extra}
    if typed is not None:
        data["typed_name"] = typed
    return client.post(CONFIRM_URL, data)


@contextmanager
def later(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every signed token past its age (``twostage.MAX_AGE_S``)."""
    moment = time.time() + twostage.MAX_AGE_S + 5
    with monkeypatch.context() as patched:
        patched.setattr(time, "time", lambda: moment)
        yield


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def section(html: str, cls: str) -> str:
    found = re.search(rf'<section class="{cls}">(.*?)</section>', html, re.S)
    assert found, cls
    return found.group(1)


# --- the GET --------------------------------------------------------------------------------------


def test_get_reads_runs_and_never_prunes(client: Client, engine: FakeEngine) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", RUNS)]
    assert not prunes(engine)  # complement
    html = text(response)
    assert "<title>Prune · Harbour Ledger</title>" in html
    assert ' aria-current="page">' not in html  # E11: no nav row is current
    assert pruning.DRY_RUN_LINE in html
    form = re.search(r'<form method="post" action="[^"]+" class="dry-run">(.*?)</form>', html, re.S)
    assert form is not None
    inputs = re.findall(r"<input [^>]*>", form.group(1))
    assert all('type="hidden"' in i for i in inputs)  # E21: only the key (and CSRF)
    assert ">Run a dry run</button>" in html


def test_policy_line(client: Client, engine: FakeEngine) -> None:
    html = text(client.get(URL))
    assert "Automatic pruning: off · Never pruned · " in html
    settings = f'<a class="settings-link" href="/stories/{STORY}/settings/">Change in Settings</a>'
    assert settings in html
    pruning_on = {
        "policy": {"older_than_days": 30},
        "last_prune_at": "2026-11-02T03:00:00Z",
        "next_run_at": "2026-11-08T03:00:00Z",
    }
    engine.respond("GET", "/stories/{story_id}", 200, json=story(pruning=pruning_on))
    html = text(client.get(URL))
    line = re.search(r'<p class="policy-line">(.*?)</p>', html, re.S)
    assert line is not None
    assert line.group(1).startswith("Automatic pruning: older than 30 days · Next run <time")
    assert "2026-11-08 03:00:00 UTC" in line.group(1)
    assert "Last pruned <time" in line.group(1)
    assert "Never pruned" not in line.group(1)


def test_policy_line_absent_and_malformed() -> None:
    assert pruning.policy_line(None).text == "Automatic pruning: —"
    assert pruning.policy_line(None).show_last is False

    class Odd:
        policy: ClassVar[dict[str, int]] = {"keep_days": 30}
        last_prune_at = None
        next_run_at = "2026-11-08T03:00:00Z"

    line = pruning.policy_line(Odd())
    assert (line.text, line.malformed, line.next_run_at) == ("Automatic pruning: on", True, None)


def test_past_prunings_rows(client: Client, engine: FakeEngine) -> None:
    html = text(client.get(URL))
    past = section(html, "past-prunings")
    assert '<p class="count">2 prunings</p>' in past
    headers = "".join(
        f"<th>{h}</th>" for h in ("When", "Trigger", "By", "Revision", "Pruned", "More")
    )
    assert headers in past
    rows = re.findall(r'<tr class="prune-run" id="prune-(\w+)">(.*?)</tr>', past, re.S)
    assert [r[0] for r in rows] == ["01JM7PR0000000000000000001", "01JM7PR0000000000000000002"]
    manual, policy = (r[1] for r in rows)
    assert ">Manual</span>" in manual
    assert "<code>console:nate</code>" in manual
    assert "Revision 21" in manual
    assert "1 discarded tail, 1 dormant continuation" in manual
    assert ">By policy (older than 30 days)</span>" in policy
    assert "Revision 18" in policy
    assert "1 discarded tail, 0 dormant continuations" in policy
    assert "&quot;older_than_days&quot;: 30" in past or '"older_than_days": 30' in html
    # Complement: one run that doesn't parse (``at: 5``).
    bad = m7("prune_runs")
    bad["prune_runs"].insert(1, {**bad["prune_runs"][0], "prune_id": "x", "at": 5})

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bad)

    engine.side_effect("GET", RUNS_ROUTE, answer)
    past = section(text(client.get(URL)), "past-prunings")
    assert "1 pruning couldn't be read — see More" in past
    assert past.count('class="prune-run"') == 2
    assert "This pruning couldn't be read" in past
    assert '<p class="count">2 prunings</p>' in past


def test_no_runs_reads_never_pruned(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUNS_ROUTE, 200, json={"prune_runs": []})
    html = client.get(URL).content.decode()
    assert '<p class="none">This story has never been pruned.</p>' in html
    assert '<table class="prune-runs">' not in html


def test_a_failed_runs_read_keeps_the_page(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RUNS_ROUTE, 503, json=error_json("database_unavailable"))
    html = text(client.get(URL))
    past = section(html, "past-prunings")
    assert 'class="engine-error' in past
    assert "Automatic pruning: off" in html
    assert ">Run a dry run</button>" in html


# --- the dry run ----------------------------------------------------------------------------------


def test_dry_run_sends_ignore_warnings_false(client: Client, engine: FakeEngine) -> None:
    response = dry_run(client)
    assert response.status_code == 200
    [sent] = prunes(engine)
    assert body_of(sent) == {"ignore_warnings": False}
    assert sent.headers["Idempotency-Key"] == KEY
    assert "X-Epistrel-Expected-Revision" not in sent.headers  # complement
    # The prune goes first, then the frame's reads, then the page's one runs read (E24).
    assert calls(engine) == [
        ("POST", PRUNE),
        ("GET", STORY_READ),
        ("GET", CONFIG_READ),
        ("GET", RUNS),
    ]


def test_dry_run_renders_the_report(client: Client, engine: FakeEngine) -> None:
    html = text(dry_run(client))
    report = section(html, "prune-report")
    assert "Dry run at story revision 20 — nothing was changed" in report
    assert report.index("Discarded tails (1)") < report.index("Dormant continuations (1)")
    assert "turn 3 · variant 1 → turn 4 · variant 1" in report
    assert "turn 4 · variant 2 → turn 4 · variant 2" in report
    assert "2 operations" in report
    assert "1 operation<" in report
    assert f'<input type="checkbox" name="target" value="{TAIL}" checked>' in report
    assert f'<input type="checkbox" name="target" value="{DORMANT}" checked>' in report
    assert f"<code>{TAIL}</code>" in report
    assert "2026-11-04 16:00:00 UTC" in report
    assert "Undo the rewind that discarded turn 3 · variant 1 → turn 4 · variant 1" in report
    assert "Swap back to turn 4 · variant 2 → turn 4 · variant 2" in report
    assert "Fork turn 4 · variant 2 → turn 4 · variant 2 into its own chain" in report
    assert "4 audit events, 1 outbox row, 2 ratings (marked pruned)" in report
    assert "An amendment names its records" in report
    assert "01JM7AM0000000000000000001" in report
    assert (
        f'<a href="/stories/{STORY}/history/#variants">To keep turn 4 · variant 2 → turn 4 · '
        "variant 2, fork it before pruning</a>"
    ) in report
    assert ">Dry run with only the ticked</button>" in report
    assert ">Prune these… (next: confirm)</button>" in report
    assert f'action="{CONFIRM_URL}"' in report
    assert pruning.PRUNE_ALL_NOTE in report
    # A page showing a report: the full re-run form after it, then Past prunings.
    assert html.index("prune-report") < html.index("Run a full dry run again")
    assert html.index("Run a full dry run again") < html.index("Past prunings")
    payload = twostage.unsign("prune", token_of(html))
    assert payload.target_id == STORY
    assert payload.body == {"targets": [TAIL, DORMANT], "ignore_warnings": True}
    assert payload.base == {
        "timeline_revision": 20,
        "story_name": NAME,
        "counts": {"discarded_tails": 1, "dormant_continuations": 1, "operations": 3},
    }


def test_narrowed_dry_run_sends_the_ticked_targets(client: Client, engine: FakeEngine) -> None:
    report_token = REPORT.findall(dry_run(client).content.decode())[0]
    engine.reset()
    engine.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    engine.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    engine.respond("GET", RUNS_ROUTE, 200, json=m7("prune_runs"))
    engine.respond("POST", PRUNE_ROUTE, 200, json=m7("prune_impact"))
    dry_run(client, narrowed="1", target=[f" {TAIL} ", "", TAIL], report_token=report_token)
    [sent] = prunes(engine)
    assert body_of(sent) == {"ignore_warnings": False, "targets": [TAIL]}
    # Complement: none ticked → the last report again with the error, no Engine write.
    engine.reset()
    engine.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    engine.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    engine.respond("GET", RUNS_ROUTE, 200, json=m7("prune_runs"))
    response = dry_run(client, narrowed="1", report_token=report_token)
    html = text(response)
    assert response.status_code == 200
    assert pruning.TICK_AT_LEAST_ONE in html
    assert "Dry run at story revision 20" in html
    assert not prunes(engine)


def test_narrowing_with_an_expired_or_bad_report(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_token = REPORT.findall(dry_run(client).content.decode())[0]
    before = len(prunes(engine))
    with later(monkeypatch):
        html = text(dry_run(client, narrowed="1", report_token=report_token))
    assert pruning.TICK_AT_LEAST_ONE in html
    assert "prune-report" not in html  # the bare page
    response = dry_run(client, narrowed="1", report_token=report_token + "x")
    assert response.status_code == 302
    assert response["Location"] == URL
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert len(prunes(engine)) == before


def test_too_many_ticked(client: Client, engine: FakeEngine) -> None:
    report_token = REPORT.findall(dry_run(client).content.decode())[0]
    before = len(prunes(engine))
    targets = [f"01JM7CN{n:019d}" for n in range(201)]
    html = text(dry_run(client, narrowed="1", target=targets, report_token=report_token))
    assert pruning.TICK_AT_MOST in html
    assert len(prunes(engine)) == before


def test_nothing_to_prune_offers_no_confirm(client: Client, engine: FakeEngine) -> None:
    empty = m7("prune_impact")
    impact = empty["impact"]
    impact["to_delete"] = {"discarded_tails": [], "dormant_continuations": []}
    impact["lost_operations"], impact["suggestions"] = [], []
    engine.respond("POST", PRUNE_ROUTE, 200, json=empty)
    html = text(dry_run(client))
    assert pruning.NOTHING_TO_PRUNE in html
    assert "An amendment names its records" in html  # the Left-out table stays
    assert CONFIRM_URL not in html
    assert "confirm_token" not in html


# --- the confirm ----------------------------------------------------------------------------------


def test_confirm_page_makes_no_call(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    engine.reset()
    response = client.post(CONFIRM_URL, {"confirm_token": token, "idempotency_key": KEY})
    assert response.status_code == 200
    html = text(response)
    assert "<title>Prune — Epistrel Console</title>" in html
    assert "Prune <em>Harbour Ledger</em>?" in html
    assert (
        "This permanently deletes the records of 1 discarded tail and 1 dormant continuation "
        "(3 operations)."
    ) in html
    assert pruning.CONFIRM_MARKERS in html
    assert pruning.CONFIRM_KEPT in html
    assert "<dt>Dry run at revision</dt><dd><code>20</code></dd>" in html
    assert "Type <strong>Harbour Ledger</strong> to confirm" in html
    assert '<input name="typed_name" autocomplete="off">' in html
    assert 'class="danger">Prune permanently</button>' in html
    assert f'name="idempotency_key" value="{KEY}"' in html  # the key carried forward
    assert engine.requests == []  # complement


@pytest.mark.parametrize("typed", ["harbour ledger", "", None, STORY])
def test_wrong_typed_name_prunes_nothing(
    client: Client, engine: FakeEngine, typed: str | None
) -> None:
    token = first_stage(client)
    response = confirm(client, token, typed)
    assert response.status_code == 200
    assert TYPED_NAME_MISMATCH in text(response)
    assert not [r for r in prunes(engine) if body_of(r)["ignore_warnings"]]  # complement


def test_matching_name_applies_once(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    engine.respond("POST", PRUNE_ROUTE, 200, json=m7("prune_applied"))
    engine.respond("GET", "/stories/{story_id}", 200, json=story(timeline_revision=21))
    response = confirm(client, token, f"  {NAME}  ")
    assert response.status_code == 200
    applies = [r for r in prunes(engine) if body_of(r)["ignore_warnings"]]
    [sent] = applies
    assert body_of(sent) == {"targets": [TAIL, DORMANT], "ignore_warnings": True}
    assert sent.headers["X-Epistrel-Expected-Revision"] == "20"
    assert sent.headers["Idempotency-Key"] == KEY
    html = text(response)
    result = section(html, "prune-result")
    assert "Pruned: 1 discarded tail and 1 dormant continuation" in result
    assert "Deleted: Events 9, Operations 3" in result
    assert "Story revision now 21" in result
    assert "Prune <code" in result
    assert "01JM7PR0000000000000000001</code>" in result
    assert "01JM7QP0000000000000000011" in result  # the operation id, in More
    assert f'href="/stories/{STORY}/history/">Back to History</a>' in result
    assert f'href="{URL}">Past prunings</a>' in result
    assert "— idempotency key <code>01JM7KY0000000000000000001</code>" in result
    assert "Timeline revision 21" in html  # the frame read after the apply
    assert ("GET", RUNS) not in calls(engine)[-3:]  # the result reads no runs
    assert flashes(response) == []


def test_cancel_and_get_confirm_send_nothing(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    engine.reset()
    html = client.post(CONFIRM_URL, {"confirm_token": token, "idempotency_key": KEY})
    assert f'<a href="{URL}">Cancel</a>' in html.content.decode()
    response = client.get(CONFIRM_URL)
    assert response.status_code == 302
    assert response["Location"] == URL
    assert engine.requests == []


def test_expired_token_calls_nothing(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = first_stage(client)
    engine.reset()
    with later(monkeypatch):
        response = confirm(client, token)
    assert response.status_code == 302
    assert response["Location"] == URL
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    tampered = confirm(client, token[:-2] + "xx")
    assert tampered.status_code == 302
    body = {"targets": [], "ignore_warnings": True}
    other = twostage.sign("prune", "01JM7ST0000000000000000002", body, {"timeline_revision": 20})
    assert confirm(client, other).status_code == 302
    assert engine.requests == []


def test_missing_key_rerenders_and_calls_nothing(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    engine.reset()
    response = client.post(
        CONFIRM_URL, {"confirm_token": token, "confirmed": "1", "typed_name": NAME}
    )
    assert response.status_code == 200
    assert "This confirmation was incomplete" in text(response)
    assert engine.requests == []


def test_revision_conflict_sends_back_to_the_dry_run(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    engine.respond(
        "POST",
        PRUNE_ROUTE,
        409,
        json=error_json("revision_conflict", "moved", current_revision=21),
    )
    response = confirm(client, token)
    assert response.status_code == 200
    html = text(response)
    assert "The story changed since you loaded this page (now revision 21) — reload" in html
    assert pruning.RERUN_TEXT in html
    assert len([r for r in prunes(engine) if body_of(r)["ignore_warnings"]]) == 1
    assert "prune-report" not in html
    assert "prune-result" not in html
    assert ">Run a dry run</button>" in html


def test_replanned_answer_is_not_a_success(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    html = text(confirm(client, token))  # the fake still answers applied: false
    assert pruning.REPLANNED_TEXT in html
    assert "Pruned:" not in html  # complement
    assert "prune-report" in html


def test_unknown_outcome_rerenders_the_confirm(client: Client, engine: FakeEngine) -> None:
    token = first_stage(client)
    engine.fail("POST", PRUNE_ROUTE, httpx.ConnectError("down"))
    response = confirm(client, token)
    html = text(response)
    assert 'class="engine-error' in html
    assert "Prune permanently" in html
    assert f'name="idempotency_key" value="{KEY}"' in html
    assert f'value="{token}"' in html
    assert 'name="confirmed" value="1"' in html


@pytest.mark.parametrize(
    ("status", "body", "headers", "expected"),
    [
        (
            422,
            error_json("prune_target_ineligible", "no", target=TAIL, reason="in_flight"),
            None,
            "One of those targets can't be pruned any more (In flight) — run the dry run again",
        ),
        (
            409,
            error_json("story_archived", "archived"),
            None,
            "This story is archived — unarchive it to prune the story.",
        ),
        (
            409,
            error_json("story_paused", "paused", cause="backfill"),
            None,
            "Paused: backfill in progress",
        ),
        (
            423,
            error_json("story_busy", "busy", reason="in_flight"),
            {"Retry-After": "7"},
            " · retry after 7 s",
        ),
    ],
)
def test_refusals(
    client: Client,
    engine: FakeEngine,
    status: int,
    body: dict[str, Any],
    headers: dict[str, str] | None,
    expected: str,
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers)

    engine.side_effect("POST", PRUNE_ROUTE, answer)
    response = dry_run(client)
    assert response.status_code == 200
    html = text(response)
    refusal = re.search(r'<p class="refusal" role="alert">(.*?)</p>', html, re.S)
    assert refusal is not None
    assert expected in refusal.group(1)
    assert len(prunes(engine)) == 1
    assert ("GET", RUNS) in calls(engine)  # the bare page's reads


def test_target_unknown_wording(client: Client, engine: FakeEngine) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=error_json("prune_target_unknown", "no", target="x"))

    engine.side_effect("POST", PRUNE_ROUTE, answer)
    html = text(dry_run(client))
    assert "One of those targets can't be pruned any more (it no longer exists)" in html


def test_unmapped_error_is_the_panel_in_the_frame(client: Client, engine: FakeEngine) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=error_json("story_not_found", "gone"))

    engine.side_effect("POST", PRUNE_ROUTE, answer)
    response = dry_run(client)
    assert response.status_code == 200
    html = text(response)
    assert 'class="engine-error' in html
    assert "story_not_found" in html
    assert ">Run a dry run</button>" not in html


def test_importing_story_is_the_importing_page(client: Client, engine: FakeEngine) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json=error_json("story_importing", "importing", stage="restoring")
        )

    engine.side_effect("GET", "/stories/{story_id}", answer)
    response = client.get(URL)
    assert response.status_code == 409
    assert "This story is being imported" in text(response)
    assert ("GET", RUNS) not in calls(engine)
