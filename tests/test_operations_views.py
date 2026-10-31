"""/operations/ and /operations/<id>/ (#44): the list with its filters, the detail page with
the turn report and guardrail verdicts, and "Fetch from Engine"."""

import re
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.inspect import short_id
from console.models import EngineOperation
from console.views import operations as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

STORY_A = "01JA0000000000000000000STA"
STORY_B = "01JA0000000000000000000STB"
TURN_OP = "01JA0000000000000000000OP3"
SCOPE = "Operations performed by this Console. Other Engine clients' operations are not shown."

pytestmark = pytest.mark.django_db


def guardrail_error(policy_id: str, *, locator: str | None = None) -> dict[str, Any]:
    violation: dict[str, Any] = {
        "policy_id": policy_id,
        "name": f"{policy_id} name",
        "message": f"{policy_id} message",
    }
    if locator:
        violation["locator"] = locator
    return {
        "error": {
            "code": "guardrail_violation",
            "message": "Input violates 1 policy.",
            "details": {"violations": [violation]},
        }
    }


def make_row(**fields: Any) -> EngineOperation:
    defaults: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY_A}/respond",
        "story_id": STORY_A,
        "outcome": "ok",
        "http_status": 200,
        "request": {"body": {}, "headers": {}},
    }
    return EngineOperation.objects.create(**{**defaults, **fields})


def listed_ids(html: str) -> list[int]:
    return [int(pk) for pk in re.findall(r'<td><a href="/operations/(\d+)/">', html)]


@pytest.fixture
def rows(operator: Any) -> dict[str, EngineOperation]:
    clean_turn = make_row(response=engine_fixture("respond"), timeline_revision=7)
    minors = make_row(
        outcome="error",
        http_status=422,
        error_code="guardrail_violation",
        response=guardrail_error("minors_no_sexual_content", locator="player_input.say"),
        is_guardrail_rejection=True,
    )
    violence = make_row(
        story_id=STORY_B,
        path=f"/stories/{STORY_B}/respond",
        outcome="error",
        http_status=422,
        error_code="guardrail_violation",
        response=guardrail_error("no_graphic_violence"),
        is_guardrail_rejection=True,
    )
    truncate = make_row(kind="truncate", path=f"/stories/{STORY_A}/truncate")
    other_operator = make_row(operator_username="sam", principal="console:sam", outcome="queued")
    unreachable = make_row(
        story_id=STORY_B, outcome="unreachable", error_code="timeout", http_status=None
    )
    return {
        "clean_turn": clean_turn,
        "minors": minors,
        "violence": violence,
        "truncate": truncate,
        "other_operator": other_operator,
        "unreachable": unreachable,
    }


def test_anonymous_access_redirects_to_login(client: Client) -> None:
    for path in ("/operations/", "/operations/1/"):
        response = client.get(path)
        assert response.status_code == 302
        assert response["Location"].startswith("/accounts/login/")


def test_list_is_newest_first_with_the_scope_header(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    html = operator_client.get("/operations/").content.decode()
    assert SCOPE in html
    assert listed_ids(html) == sorted((r.pk for r in rows.values()), reverse=True)


def test_list_shows_the_columns(operator_client: Client, rows: dict[str, EngineOperation]) -> None:
    row = rows["clean_turn"]
    row.idempotency_key = "01JA0000000000000000000KEY"
    row.engine_operation_id = TURN_OP
    row.config_revision = 4
    row.save()
    html = operator_client.get("/operations/").content.decode()
    for header in (
        "Time",
        "Operator",
        "Principal",
        "Kind",
        "Story",
        "Outcome / error code",
        "Timeline revision",
        "Idempotency key",
        "Config revision",
        "Engine operation id",
    ):
        assert f"<th>{header}</th>" in html
    assert f'<a href="/stories/{STORY_A}/"><code>{STORY_A}</code></a>' in html
    assert f'<a href="/operations/{row.pk}/"><code>{TURN_OP}</code></a>' in html
    assert "<code>console:nate</code>" in html
    assert "<code>01JA0000000000000000000KEY</code>" in html
    assert "queued (unconfirmed)" in html
    assert "<code>guardrail_violation</code>" in html


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (f"story={STORY_B}", {"violence", "unreachable"}),
        ("kind=truncate", {"truncate"}),
        ("outcome=error", {"minors", "violence"}),
        ("outcome=unreachable", {"unreachable"}),
        ("operator=sam", {"other_operator"}),
        ("policy=minors_no_sexual_content", {"minors"}),
        ("policy=no_graphic_violence", {"violence"}),
        ("guardrail=1", {"minors", "violence"}),
        ("guardrail=1&policy=minors_no_sexual_content", {"minors"}),
        (f"story={STORY_A}&outcome=ok&kind=respond&operator=nate", {"clean_turn"}),
    ],
)
def test_each_filter_narrows(
    operator_client: Client, rows: dict[str, EngineOperation], query: str, expected: set[str]
) -> None:
    html = operator_client.get(f"/operations/?{query}").content.decode()
    by_pk = {row.pk: name for name, row in rows.items()}
    assert {by_pk[pk] for pk in listed_ids(html)} == expected


def test_policy_filter_excludes_a_rejection_by_another_policy(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    html = operator_client.get("/operations/?policy=minors_no_sexual_content").content.decode()
    assert rows["violence"].pk not in listed_ids(html)
    assert rows["minors"].pk in listed_ids(html)


def test_guardrail_only_excludes_a_clean_turn(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    html = operator_client.get("/operations/?guardrail=1").content.decode()
    assert rows["clean_turn"].pk not in listed_ids(html)


def test_filter_selects_offer_what_the_log_holds(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    html = operator_client.get("/operations/").content.decode()
    for value in (
        STORY_A,
        STORY_B,
        "truncate",
        "sam",
        "minors_no_sexual_content",
        "no_graphic_violence",
    ):
        assert f'<option value="{value}">' in html


def test_fallback_turn_policy_appears_in_the_policy_select(
    operator_client: Client, operator: Any
) -> None:
    body = engine_fixture("respond")
    body["guardrail"] = {
        "violations": [
            {"policy_id": "no_slurs", "name": "No slurs", "message": "m", "kind": "wordlist"}
        ],
        "fallback_used": True,
    }
    row = make_row(response=body, is_guardrail_rejection=True)
    html = operator_client.get("/operations/?policy=no_slurs").content.decode()
    assert '<option value="no_slurs" selected>' in html
    assert listed_ids(html) == [row.pk]


def test_list_pages_by_50_and_keeps_filters(operator_client: Client, operator: Any) -> None:
    for _ in range(51):
        make_row(kind="truncate")
    first = operator_client.get("/operations/?kind=truncate").content.decode()
    assert len(listed_ids(first)) == 50
    assert "?kind=truncate&amp;page=2" in first
    second = operator_client.get("/operations/?kind=truncate&page=2").content.decode()
    assert len(listed_ids(second)) == 1


def test_detail_shows_request_response_and_the_turn_report(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    row = rows["clean_turn"]
    row.request = {"body": {"player_input": {"say": "hi"}}, "headers": {"Prefer": "x"}}
    row.save()
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert SCOPE in html
    assert "Turn report" in html
    assert "Model calls (2)" in html
    assert "<code>openai/gpt-test</code>" in html
    assert "generation 1, guardrail_check 1" in html  # call counts
    assert "max calls 10, used 2, reserved guardrail_output 1" in html
    assert "950 ms" in html  # elapsed
    assert "not queued" in html
    assert "12 ms" in html  # first call offset
    assert "fallback used: no" in html
    assert "&quot;say&quot;: &quot;hi&quot;" in html  # request as formatted JSON
    assert "&quot;timeline_revision&quot;: 7" in html  # response as formatted JSON
    # No Engine operation id recorded: no Fetch button.
    assert "Fetch from Engine" not in html


def test_operation_detail_warnings_use_short_ids(operator_client: Client) -> None:
    """#202: the operation detail's Warnings row has no labels and no story: short ids (the
    full id in ``title``) and no Retrieval link."""
    character, memory = "01JA0000000000000000000CH1", "01JA0000000000000000000ME1"
    row = make_row(
        response={
            "model_calls": [],
            "elapsed_ms": 5,
            "warnings": [
                {"kind": "primary_not_present", "character_id": character},
                {"kind": "pin_quarantined", "memory_id": memory},
            ],
        }
    )
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    warnings = html.split("<dt>Warnings</dt>")[1].split("</dd>")[0]
    assert f"<li>{short_id(character)} was named but isn&#x27;t in the scene" in warnings
    assert (
        f'<li>Pinned memory <code title="{memory}">{short_id(memory)}</code> was quarantined by '
        "the output gate</li>"
    ) in warnings
    assert "<a " not in warnings
    assert "/inspect/retrieval/" not in html


def test_detail_shows_input_violations(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    html = operator_client.get(f"/operations/{rows['minors'].pk}/").content.decode()
    section = html.split("<h2>Input rejected</h2>", 1)[1].split("</section>", 1)[0]
    assert "<code>minors_no_sexual_content</code>" in section
    assert "minors_no_sexual_content name" in section
    assert "minors_no_sexual_content message" in section
    assert "<code>player_input.say</code>" in section
    assert "Turn report" not in html
    # Absent locator shows "—".
    other = operator_client.get(f"/operations/{rows['violence'].pk}/").content.decode()
    assert "<td>—</td>" in other.split("<h2>Input rejected</h2>", 1)[1]


def test_detail_shows_output_violations_with_several_locators(
    operator_client: Client, operator: Any
) -> None:
    body = engine_fixture("respond")
    body["guardrail"] = {
        "violations": [
            {"policy_id": "no_slurs", "name": "No slurs", "message": "Keep it clean", "kind": "x"}
        ],
        "fallback_used": True,
    }
    row = make_row(response=body, is_guardrail_rejection=True)
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    section = html.split("<h2>Output</h2>", 1)[1].split("</section>", 1)[0]
    assert "<code>no_slurs</code>" in section
    assert "Keep it clean" in section
    assert "fallback used: yes" in html

    details = guardrail_error("p1")
    details["error"]["details"]["violations"][0]["locators"] = ["a.b", "c.d"]
    multi = make_row(outcome="error", error_code="guardrail_violation", response=details)
    html = operator_client.get(f"/operations/{multi.pk}/").content.decode()
    assert "<code>a.b</code><br><code>c.d</code>" in html


def test_a_503_with_model_calls_in_details_is_a_turn_row(
    operator_client: Client, operator: Any
) -> None:
    error = {
        "error": {
            "code": "model_unavailable",
            "message": "The model is unavailable.",
            "details": {"model_calls": engine_fixture("respond")["model_calls"], "elapsed_ms": 40},
        }
    }
    row = make_row(outcome="error", http_status=503, error_code="model_unavailable", response=error)
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert "Turn report" in html
    assert "40 ms" in html


def test_a_live_input_rejection_shows_the_gate_spend_and_no_turn_panel(
    operator_client: Client, operator: Any
) -> None:
    """#131: a 422 ``guardrail_violation``'s ``model_calls`` is the input gate's count."""
    rejection = engine_fixture("respond_guardrail_violation")
    row = make_row(
        outcome="error",
        http_status=422,
        error_code="guardrail_violation",
        response=rejection,
        is_guardrail_rejection=True,
    )
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    section = html.split("<h2>Input rejected</h2>", 1)[1].split("</section>", 1)[0]
    assert '<p class="input-gate">Input gate: 1 model call, 3 ms</p>' in section
    assert "<code>minors_no_sexual_content</code>" in section
    assert "could not be read" not in html
    assert "Turn report" not in html


def test_detail_of_an_unknown_row_is_404(operator_client: Client) -> None:
    assert operator_client.get("/operations/999999/").status_code == 404


@pytest.fixture
def fetch_engine(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def test_fetch_from_engine_shows_the_engine_record(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    fetch_engine.respond(
        "GET", "/operations/{operation_id}", 200, json=engine_fixture("get_operation")
    )
    viewpoint = "character:01JA0000000000000000000CH1"
    row = make_row(
        engine_operation_id=TURN_OP,
        request={"body": {"output_viewpoint": viewpoint}, "headers": {}},
    )
    page = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert "Fetch from Engine" in page
    assert fetch_engine.requests == []  # nothing fetched until asked

    html = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    (sent,) = fetch_engine.requests
    assert sent.url.path == f"/operations/{TURN_OP}"
    assert sent.url.params["viewpoint"] == viewpoint
    assert sent.headers["x-epistrel-principal"] == "console:nate"
    section = html.split("<h2>Engine's view</h2>", 1)[1]
    assert "<dt>Status</dt><dd>committed</dd>" in section
    assert "<code>console:nate</code>" in section
    assert "&quot;story&quot;: 4" in section  # config_revisions
    assert "Turn report" in section  # the stored result is a turn
    # A read: never recorded.
    assert EngineOperation.objects.count() == 1


def test_fetch_without_a_viewpoint_sends_none(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    record = engine_fixture("get_operation")
    record["result"] = {"status": 200, "body": {"timeline_revision": 9, "api_key": "sk-x"}}
    fetch_engine.respond("GET", "/operations/{operation_id}", 200, json=record)
    row = make_row(kind="truncate", engine_operation_id=TURN_OP)
    html = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    assert "viewpoint" not in fetch_engine.requests[0].url.params
    assert "&quot;timeline_revision&quot;: 9" in html
    assert "sk-x" not in html  # the Engine's result is shown redacted


def test_fetch_outside_the_window_says_so(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    record = engine_fixture("get_operation")
    del record["result"]
    fetch_engine.respond("GET", "/operations/{operation_id}", 200, json=record)
    row = make_row(engine_operation_id=TURN_OP)
    html = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    assert "not available (outside the Engine's result window)" in html


@pytest.mark.parametrize(("status", "code"), [(404, "not_found"), (409, "idempotency_conflict")])
def test_fetch_shows_the_engine_error_in_the_panel(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine, status: int, code: str
) -> None:
    error = {"error": {"code": code, "message": "nope", "details": {}}}
    fetch_engine.respond("GET", "/operations/{operation_id}", status, json=error)
    row = make_row(engine_operation_id=TURN_OP)
    response = operator_client.get(f"/operations/{row.pk}/?fetch=1")
    assert response.status_code == 200
    html = response.content.decode()
    assert f"Engine error {status} {code}" in html
    assert 'class="engine-error"' in html
    # The rest of the page still renders.
    assert "<h2>Request</h2>" in html


def test_fetch_on_a_row_without_an_engine_id_does_nothing(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    row = make_row()
    html = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    assert fetch_engine.requests == []
    assert "Fetch from Engine" not in html


def test_the_admin_shows_the_log_read_only(
    operator_client: Client, rows: dict[str, EngineOperation]
) -> None:
    row = rows["minors"]
    assert operator_client.get("/admin/console/engineoperation/").status_code == 200
    page = operator_client.get(f"/admin/console/engineoperation/{row.pk}/change/")
    assert page.status_code == 200
    assert 'name="kind"' not in page.content.decode()  # no editable inputs
    assert operator_client.get("/admin/console/engineoperation/add/").status_code == 403
    delete = operator_client.post(f"/admin/console/engineoperation/{row.pk}/delete/")
    assert delete.status_code == 403
    assert EngineOperation.objects.filter(pk=row.pk).exists()


# --- #64: principal and entity filters, the Entity column ----------------------------------------

CHAR = "01JA0000000000000000000CHR"


@pytest.fixture
def entity_rows(operator: Any) -> dict[str, EngineOperation]:
    return {
        "nate_story": make_row(entity_type="story", entity_id=STORY_A),
        "alice_story": make_row(principal="alice", entity_type="story", entity_id=STORY_A),
        "alice_character": make_row(
            principal="alice",
            kind="publish_catalog_character",
            path=f"/catalog/characters/{CHAR}/publish",
            story_id="",
            entity_type="character",
            entity_id=CHAR,
        ),
        "no_entity": make_row(kind="update_admin_settings", story_id="", path="/admin/settings"),
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("principal=alice", {"alice_story", "alice_character"}),
        ("principal=console:nate", {"nate_story", "no_entity"}),
        ("entity_type=character", {"alice_character"}),
        ("entity_type=story", {"nate_story", "alice_story"}),
        (f"entity_id={STORY_A}", {"nate_story", "alice_story"}),
        (f"entity_type=story&entity_id={CHAR}", set()),
        (f"entity_type=character&entity_id=+{CHAR}+", {"alice_character"}),
        ("principal=alice&entity_type=story", {"alice_story"}),
    ],
)
def test_principal_and_entity_filters_narrow(
    operator_client: Client, entity_rows: dict[str, EngineOperation], query: str, expected: set[str]
) -> None:
    html = operator_client.get(f"/operations/?{query}").content.decode()
    by_pk = {row.pk: name for name, row in entity_rows.items()}
    assert {by_pk[pk] for pk in listed_ids(html)} == expected


def test_the_filter_selects_list_the_distinct_principals_and_entity_types(
    operator_client: Client, entity_rows: dict[str, EngineOperation]
) -> None:
    page = operator_client.get("/operations/?principal=alice").content.decode()
    html = page[page.index('class="filters"') :]  # not the nav's acting selector
    principals = html[html.index('<select name="principal">') :]
    principals = principals[: principals.index("</select>")]
    assert '<option value="alice" selected>alice</option>' in principals
    assert '<option value="console:nate">console:nate</option>' in principals
    types = html[html.index('<select name="entity_type">') :]
    types = types[: types.index("</select>")]
    assert '<option value="character">Character</option>' in types
    assert '<option value="story">Story</option>' in types
    assert 'name="entity_id" value=""' in html


def test_the_entity_column_links_through_the_registry(
    operator_client: Client, entity_rows: dict[str, EngineOperation]
) -> None:
    html = operator_client.get("/operations/").content.decode()
    assert "<th>Entity</th>" in html
    assert f'Story <a href="/stories/{STORY_A}/"><code>{STORY_A}</code></a>' in html
    # #65 built the catalog character page: the id links to it.
    assert f'Character <a href="/catalog/characters/{CHAR}/"><code>{CHAR}</code></a>' in html


def test_the_detail_shows_entity_and_job(
    operator_client: Client, entity_rows: dict[str, EngineOperation]
) -> None:
    row = entity_rows["alice_character"]
    row.job_id = "01JA0000000000000000000JOB"
    row.save()
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert "<dt>Entity type</dt><dd><code>character</code> (Character)</dd>" in html
    link = f'<a href="/catalog/characters/{CHAR}/"><code>{CHAR}</code></a>'
    assert f"<dt>Entity id</dt><dd>{link}</dd>" in html
    assert "<dt>Job id</dt><dd><code>01JA0000000000000000000JOB</code></dd>" in html
    # #76 built the principal pages: the principal links to its page.
    principal = '<a href="/principals/alice/"><code>alice</code></a>'
    assert f"<dt>Principal</dt><dd>{principal}</dd>" in html
