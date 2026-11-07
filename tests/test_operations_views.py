"""/operations/ and /operations/<id>/ (#44): the list with its filters, the detail page with
the turn report and guardrail verdicts, and "Fetch from Engine"."""

import re
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.effects import INCOMPLETE_TEXT, INCOMPLETE_TITLE
from console.engine.client import EngineClient
from console.engine.models import OperationRecord
from console.engine_access import engine_client_for
from console.inspect import history_turn_url, short_id
from console.models import EngineOperation
from console.views import operations as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

STORY_A = "01JA0000000000000000000STA"
STORY_B = "01JA0000000000000000000STB"
TURN_OP = "01JA0000000000000000000OP3"
SCOPE = "Operations performed by this Console. Other Engine clients' operations are not shown."
STAGED_OP = "01JM6QP0000000000000000001"  # m6/operation_staged.json (#250)
INCOMPLETE_OP = "01JM6QP0000000000000000002"  # m6/operation_staged_incomplete.json
M6_STORY = "01JM6ST0000000000000000001"

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
    for path in ("/operations/", "/operations/1/", f"/operations/engine/{STAGED_OP}/"):
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


def model_calls_table(html: str) -> tuple[list[str], list[list[str]]]:
    table = html.split('<table class="model-calls">')[1].split("</table>")[0]
    head = re.findall(r"<th>(.*?)</th>", table)
    rows = [
        re.findall(r"<td[^>]*>(.*?)</td>", r) for r in re.findall(r"<tr>(.*?)</tr>", table, re.S)
    ]
    return head, [r for r in rows if r]


def test_turn_report_model_calls_show_the_stage(operator_client: Client) -> None:
    """#234: Stage after Purpose, raw in ``<code>`` (an unknown stage too), "—" when absent."""
    staged, unstaged = engine_fixture("respond")["model_calls"][:2]
    later = {**staged, "purpose": "follow_up", "stage": "later_stage"}
    row = make_row(
        response={
            "elapsed_ms": 5,
            "model_calls": [{**staged, "stage": "render"}, {**unstaged, "stage": None}, later],
        }
    )
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    head, rows = model_calls_table(html)
    assert head[:3] == ["Purpose", "Stage", "Model"]
    assert len(head) == 11
    assert "Model calls (3)" in html
    assert [r[1] for r in rows] == ["<code>render</code>", "—", "<code>later_stage</code>"]
    assert rows[2][0] == "follow_up"  # neither column humanised
    assert "Later stage" not in html


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


# --- the stages partial and the Engine operation page (#250) --------------------------------------

STAGE_NAMES = ("Intent", "Claim", "Outcome", "Completion", "Response")
BADGE = f'<span class="badge incomplete" title="{INCOMPLETE_TITLE}">{INCOMPLETE_TEXT}</span>'
ENGINE_SCOPE = (
    "The Engine&#x27;s record of one operation. Results aren&#x27;t shown here: open the "
    "operation-log entry and Fetch from Engine for a turn&#x27;s stored result."
)


def m6_operation(name: str) -> dict[str, Any]:
    record: dict[str, Any] = engine_fixture(f"m6/{name}")
    return record


def engine_view(html: str) -> str:
    return html.split("<h2>Engine's view</h2>", 1)[1]


def stage_cells(html: str) -> list[list[str]]:
    table = html.split('<table class="stages">', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r'<tr class="stage[^"]*">(.*?)</tr>', table, re.S)
    return [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in rows]


def fetched_html(client: Client, fake: FakeEngine, record: dict[str, Any]) -> str:
    fake.respond("GET", "/operations/{operation_id}", 200, json=record)
    row = make_row(engine_operation_id=record["id"])
    response = client.get(f"/operations/{row.pk}/?fetch=1")
    assert response.status_code == 200
    return engine_view(response.content.decode())


def test_engine_view_shows_staged_stages(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    section = fetched_html(operator_client, fetch_engine, m6_operation("operation_staged"))
    assert '<p class="operation-label">Label: <span title="staged_turn">Staged turn</span>' in (
        section
    )
    assert "<dt>Stages</dt><dd>" in section
    assert section.index("<dt>Config revisions</dt>") < section.index("<dt>Stages</dt>")
    cells = stage_cells(section)
    assert [re.sub(r"<[^>]+>", "", c[0]) for c in cells] == list(STAGE_NAMES)
    assert [c[1] for c in cells] == ["Done"] * 5
    assert [c[3] for c in cells] == [f"revision {n}" for n in (4, 4, 5, 6, 6)]
    assert cells[2][3] == "revision 5"  # Outcome
    assert '<td title="outcome">Outcome</td><td title="done">Done</td>' in section
    assert '<time datetime="2026-11-01T10:00:02Z"' in cells[2][2]
    assert "<th>Revision</th>" in section
    # **Complement:** a finished staged turn carries no Incomplete badge.
    assert "badge incomplete" not in section


def test_engine_view_marks_an_incomplete_staged_turn(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    record = m6_operation("operation_staged_incomplete")
    section = fetched_html(operator_client, fetch_engine, record)
    assert f"Staged turn</span> {BADGE}</p>" in section
    cells = stage_cells(section)
    assert [c[1] for c in cells] == ["Done", "Done", "Running", "Pending", "Pending"]
    assert cells[3][2:] == ["—", "—"]  # pending: no time, no revision
    assert cells[4][2:] == ["—", "—"]
    assert cells[2][3] == "—"  # running, not yet committed
    assert cells[1][3] == "revision 4"


def test_engine_view_shows_a_rating_checks_stages_without_revision(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    record = engine_fixture("get_operation")
    record.update(
        label="rating_check",
        stages=[
            {"name": "queued", "status": "done", "at": "2026-10-24T12:00:00Z"},
            {"name": "checking", "status": "running", "at": "2026-10-24T12:00:01Z"},
        ],
    )
    section = fetched_html(operator_client, fetch_engine, record)
    assert 'Label: <span title="rating_check">Rating check</span>' in section
    cells = stage_cells(section)
    assert [c[1] for c in cells] == ["Done", "Running"]
    # **Complement:** no Revision column, and no badge (not a staged turn).
    assert "<th>Revision</th>" not in section
    assert all(len(c) == 3 for c in cells)
    assert "badge incomplete" not in section


def test_engine_view_without_label_reads_dash(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    section = fetched_html(operator_client, fetch_engine, engine_fixture("get_operation"))
    assert '<p class="operation-label">Label: —</p>' in section
    assert '<table class="stages">' not in section
    assert "<h3>Stages</h3>" not in section


@pytest.fixture
def engine_page(fetch_engine: FakeEngine) -> FakeEngine:
    fetch_engine.respond(
        "GET", "/operations/{operation_id}", 200, json=m6_operation("operation_staged")
    )
    return fetch_engine


def test_engine_page_reads_once_and_records_nothing(
    operator_client: Client, operator: Any, engine_page: FakeEngine
) -> None:
    before = EngineOperation.objects.count()
    response = operator_client.get(f"/operations/engine/{STAGED_OP}/")
    assert response.status_code == 200
    (sent,) = engine_page.requests
    assert sent.method == "GET"
    assert sent.url.path == f"/operations/{STAGED_OP}"
    assert "viewpoint" not in sent.url.params
    # **Complement:** a read, never recorded.
    assert EngineOperation.objects.count() == before
    html = response.content.decode()
    assert f"<title>Engine operation {STAGED_OP} — Epistrel Console</title>" in html
    assert f"Engine operation <code>{STAGED_OP}</code>" in html
    assert ENGINE_SCOPE in html
    section = engine_view(html)
    assert '<dt>Status</dt><dd><span title="committed">Committed</span></dd>' in section
    assert "<dt>Kind</dt><dd><code>play_turn</code></dd>" in section
    assert f'href="/stories/{M6_STORY}/"><code>{M6_STORY}</code></a>' in section
    turn_url = history_turn_url(M6_STORY, 12, 1)
    assert f'<a class="turn-link" href="{escape(turn_url)}">Turn 12 · variant 1</a>' in section
    assert "<dt>Timeline revision</dt><dd>6</dd>" in section
    assert '<dt>Origin</dt><dd><span title="generated">Generated</span></dd>' in section
    assert "<dt>Started</dt><dd><time" in section
    assert "<dt>Committed</dt><dd><time" in section
    assert [re.sub(r"<[^>]+>", "", c[0]) for c in stage_cells(section)] == list(STAGE_NAMES)
    # Only the listed fields: no result on this page.
    assert "<dt>Result</dt>" not in section
    assert "<dt>Config revisions</dt>" not in section


def test_engine_page_turn_without_variant_or_story(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    record = m6_operation("operation_staged")
    record.update(turn_variant=None, story_id=None, origin="")
    fetch_engine.respond("GET", "/operations/{operation_id}", 200, json=record)
    section = engine_view(operator_client.get(f"/operations/engine/{STAGED_OP}/").content.decode())
    assert "<dt>Turn</dt><dd>Turn 12 · variant —</dd>" in section  # no story: unlinked
    assert "<dt>Story</dt><dd>—</dd>" in section
    assert "<dt>Origin</dt><dd>—</dd>" in section


def test_engine_page_lists_this_consoles_rows(
    operator_client: Client, operator: Any, engine_page: FakeEngine
) -> None:
    older = make_row(engine_operation_id=STAGED_OP, kind="respond")
    newer = make_row(engine_operation_id=STAGED_OP, kind="regenerate")
    make_row(engine_operation_id=INCOMPLETE_OP)  # another operation's row: not listed
    html = operator_client.get(f"/operations/engine/{STAGED_OP}/").content.decode()
    log = html.split("<h2>This Console's log</h2>", 1)[1]
    links = re.findall(r'<a class="log-link" href="/operations/(\d+)/"><code>(\w+)</code> · ', log)
    assert links == [(str(newer.pk), "regenerate"), (str(older.pk), "respond")]
    assert "No entry in this Console" not in log
    # With none: the "No entry" line.
    engine_page.respond(
        "GET", "/operations/{operation_id}", 200, json=m6_operation("operation_staged_incomplete")
    )
    html = operator_client.get(f"/operations/engine/{INCOMPLETE_OP[:-1]}3/").content.decode()
    assert (
        "No entry in this Console&#x27;s log — another client made it, or it was one of a "
        "batch&#x27;s later events."
    ) in html
    assert 'class="log-link"' not in html


def test_engine_page_lowercase_id_is_normalised(
    operator_client: Client, operator: Any, engine_page: FakeEngine
) -> None:
    make_row(engine_operation_id=STAGED_OP)
    response = operator_client.get(f"/operations/engine/{STAGED_OP.lower()}/")
    assert response.status_code == 200
    (sent,) = engine_page.requests
    assert sent.url.path == f"/operations/{STAGED_OP}"
    html = response.content.decode()
    assert f"Engine operation <code>{STAGED_OP}</code>" in html
    assert 'class="log-link"' in html


@pytest.mark.parametrize("bad", ["not-an-id", "01JM6QP000000000000000000O", "01JM6QP00001"])
def test_engine_page_malformed_id_is_404_with_no_call(
    operator_client: Client, operator: Any, engine_page: FakeEngine, bad: str
) -> None:
    response = operator_client.get(f"/operations/engine/{bad}/")
    assert response.status_code == 404
    html = response.content.decode()
    assert "Not found (that isn&#x27;t an operation id)" in html
    assert ENGINE_SCOPE in html
    assert "Engine operation <code>" in html
    # **Complement:** the fake's call log is empty.
    assert engine_page.requests == []


def test_engine_page_malformed_id_is_escaped(
    operator_client: Client, operator: Any, engine_page: FakeEngine
) -> None:
    response = operator_client.get("/operations/engine/%3Cb%3Ex/")
    assert response.status_code == 404
    html = response.content.decode()
    assert "<code>&lt;b&gt;x</code>" in html
    assert "<b>x" not in html
    assert engine_page.requests == []


def test_engine_page_404_and_503(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    row = make_row(engine_operation_id=STAGED_OP)
    url = f"/operations/engine/{STAGED_OP}/"

    def error(status: int, code: str, **headers: str) -> None:
        body = {"error": {"code": code, "message": "nope", "details": {}}}
        fetch_engine.reset()
        fetch_engine.respond(
            "GET", "/operations/{operation_id}", status, json=body, headers=headers
        )

    error(404, "not_found")
    response = operator_client.get(url)
    assert response.status_code == 404
    html = response.content.decode()
    assert f"The Engine has no operation {STAGED_OP}" in html
    assert ENGINE_SCOPE in html  # the frame keeps rendering
    assert f'href="/operations/{row.pk}/"' in html

    error(503, "database_unavailable")
    response = operator_client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="engine-error"' in html
    assert "Engine error 503 database_unavailable" in html
    assert f'href="/operations/{row.pk}/"' in html

    error(503, "erasure_pending", **{"Retry-After": "30"})
    response = operator_client.get(url)
    assert response.status_code == 503
    assert response["Retry-After"] == "30"
    html = response.content.decode()
    assert "Held until the erasure ledger is re-applied — retry in 30 s" in html
    assert f'<a class="retry" href="{url}">Retry</a>' in html
    assert f"/stories/{STORY_A}/delete/" in html  # the newest log row's story
    assert f'href="/operations/{row.pk}/"' in html
    assert 'class="engine-error"' not in html


def test_engine_page_held_without_a_known_story(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    body = {"error": {"code": "erasure_pending", "message": "held", "details": {}}}
    fetch_engine.respond("GET", "/operations/{operation_id}", 503, json=body)
    response = operator_client.get(f"/operations/engine/{STAGED_OP}/")
    assert response.status_code == 503
    assert not response.has_header("Retry-After")
    html = response.content.decode()
    assert "Held until the erasure ledger is re-applied</p>" in html
    assert "Retry</a>" in html
    assert "Delete" not in html
    assert "/delete/" not in html


def test_unknown_stage_values_are_humanised() -> None:
    record = engine_fixture("get_operation")
    record["stages"] = [{"name": "later_stage", "status": "partial", "at": None}]
    parsed = OperationRecord.from_json(record)
    assert parsed is not None
    rows = views.stage_rows(parsed)
    assert rows[0].name_text == "Later stage"
    assert rows[0].status_text == "Partial"
    assert rows[0].revision_text == "—"
    # A stage status outside the Engine's enum (C15) makes the record fail to parse.
    record["stages"] = [{"name": "later_stage", "status": "exploded", "at": None}]
    assert OperationRecord.from_json(record) is None


# --- the Engine label (#257) --------------------------------------------------------------------

LABEL_SELECT = re.compile(r'<select name="label">(.*?)</select>', re.S)


def label_options(html: str) -> list[str]:
    match = LABEL_SELECT.search(html)
    assert match is not None
    return re.findall(r"<option [^>]*>[^<]*</option>", match.group(1))


def test_fetch_labels_the_row(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    record = m6_operation("operation_staged")
    fetch_engine.respond("GET", "/operations/{operation_id}", 200, json=record)
    row = make_row(engine_operation_id=record["id"])
    html = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    row.refresh_from_db()
    assert row.engine_label == "staged_turn"
    # The Fetch response itself shows it (set in memory after recording).
    assert '<dt>Engine label</dt><dd><span title="staged_turn">Staged turn</span></dd>' in html
    assert EngineOperation.objects.count() == 1  # a read adds no row


def test_a_failed_fetch_leaves_the_label_unread(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    fetch_engine.respond(
        "GET",
        "/operations/{operation_id}",
        404,
        json={"error": {"code": "not_found", "message": "gone", "details": {}}},
    )
    row = make_row(engine_operation_id=STAGED_OP)
    html = operator_client.get(f"/operations/{row.pk}/?fetch=1").content.decode()
    row.refresh_from_db()
    assert row.engine_label is None
    assert "<dt>Engine label</dt><dd>—</dd>" in html


def test_the_detail_page_shows_the_label_after_the_operation_id(
    operator_client: Client, operator: Any
) -> None:
    row = make_row(engine_operation_id=STAGED_OP, engine_label="rating_check")
    html = operator_client.get(f"/operations/{row.pk}/").content.decode()
    label = '<dt>Engine label</dt><dd><span title="rating_check">Rating check</span></dd>'
    assert label in html
    assert html.index("<dt>Engine operation id</dt>") < html.index(label)
    for unread in (None, ""):
        other = make_row(engine_label=unread)
        page = operator_client.get(f"/operations/{other.pk}/").content.decode()
        assert "<dt>Engine label</dt><dd>—</dd>" in page


def test_label_filter_offers_only_recorded_labels(operator_client: Client, operator: Any) -> None:
    make_row(engine_label=None)
    make_row(engine_label="")
    assert label_options(operator_client.get("/operations/").content.decode()) == [
        '<option value="">all</option>'
    ]
    make_row(engine_label="staged_turn")
    make_row(engine_label="rating_check")
    assert label_options(operator_client.get("/operations/").content.decode()) == [
        '<option value="">all</option>',
        '<option value="rating_check" title="rating_check">Rating check</option>',
        '<option value="staged_turn" title="staged_turn">Staged turn</option>',
    ]


def test_label_filter_narrows(operator_client: Client, operator: Any) -> None:
    staged = make_row(engine_label="staged_turn")
    make_row(engine_label="rating_check")
    make_row(engine_label=None)  # **Complement:** never read
    make_row(engine_label="")  # read, no label
    html = operator_client.get("/operations/?label=staged_turn").content.decode()
    assert listed_ids(html) == [staged.pk]
    assert '<option value="staged_turn" title="staged_turn" selected>Staged turn</option>' in html
    # A label the log doesn't hold is applied as given.
    assert listed_ids(operator_client.get("/operations/?label=genesis").content.decode()) == []
    # Clear drops it.
    assert 'href="/operations/">Clear</a>' in html


def test_label_note_is_shown(operator_client: Client, operator: Any) -> None:
    html = operator_client.get("/operations/").content.decode()
    note = f'<p class="filter-note">{escape(views.LABEL_NOTE)}</p>'
    assert note in html
    assert html.index('<select name="label">') < html.index(note)


def test_label_note_points_to_story_operations(operator_client: Client, operator: Any) -> None:
    """#324: the note says what the log covers and points to the story's Operations page."""
    assert views.LABEL_NOTE == (
        "Known only for operations whose Engine record this Console has read (Fetch from "
        "Engine, the Engine record page, a queued turn's poll, an opened History page or "
        "eval-hooks block). A story's Operations page lists every operation of that story with "
        "the Engine's own label."
    )
    html = operator_client.get("/operations/").content.decode()
    assert escape(views.LABEL_NOTE) in html
    # **Complement:** the old sentence is gone.
    assert "The Engine has no list of operations to filter by" not in html
    assert "The Engine has no list of operations to filter by" not in views.LABEL_NOTE


def test_story_filter_links_engine_operations_with_label(
    operator_client: Client, operator: Any
) -> None:
    """#324: a log filtered to one story links that story's Operations page, the label carried."""
    html = operator_client.get(f"/operations/?story={STORY_A}&label=staged_turn").content.decode()
    link = (
        f'<a class="story-operations-link" href="/stories/{STORY_A}/operations/'
        f'?label=staged_turn">{views.STORY_OPERATIONS_TEXT}</a>'
    )
    assert f'<p class="filter-note">{escape(views.LABEL_NOTE)} {link}</p>' in html
    # No label (or one the Engine doesn't list): the bare page.
    for query in (f"story={STORY_A}", f"story={STORY_A}&label=made_up"):
        page = operator_client.get(f"/operations/?{query}").content.decode()
        assert f'href="/stories/{STORY_A}/operations/">' in page
        assert "/operations/?label=" not in page
    # **Complement:** no link without a story filter, or with one that isn't an id.
    for query in ("", "label=staged_turn", "story=not-an-id"):
        page = operator_client.get(f"/operations/?{query}").content.decode()
        assert "story-operations-link" not in page
        assert views.STORY_OPERATIONS_TEXT not in page


@override_settings(ROOT_URLCONF="tests.urls_without_story_operations")
def test_no_story_operations_link_while_unregistered(
    operator_client: Client, operator: Any
) -> None:
    """**Complement (D19, #341):** without ``stories:operations-list`` a log filtered to one
    story still renders, with the note and no link to that story's Operations page."""
    assert views.story_operations_url(STORY_A, "staged_turn") is None
    response = operator_client.get(f"/operations/?story={STORY_A}&label=staged_turn")
    assert response.status_code == 200
    html = response.content.decode()
    assert f'<p class="filter-note">{escape(views.LABEL_NOTE)}</p>' in html
    assert "story-operations-link" not in html
    assert views.STORY_OPERATIONS_TEXT not in html


def test_kind_cell_shows_the_label(operator_client: Client, operator: Any) -> None:
    make_row(engine_label="staged_turn", replayed=True)
    make_row(kind="set_story_content_rating", engine_label="rating_check")
    make_row(kind="truncate", engine_label=None)
    make_row(kind="fork", engine_label="")
    html = operator_client.get("/operations/").content.decode()
    assert (
        '<td><code>respond</code> · <span title="staged_turn">Staged turn</span> (replayed)</td>'
        in html
    )
    assert (
        '<td><code>set_story_content_rating</code> · <span title="rating_check">Rating check'
        "</span></td>" in html
    )
    # **Complement:** NULL and "" show the bare kind.
    assert "<td><code>truncate</code></td>" in html
    assert "<td><code>fork</code></td>" in html


def test_paging_keeps_the_label(operator_client: Client, operator: Any) -> None:
    for _ in range(51):
        make_row(engine_label="staged_turn")
    first = operator_client.get("/operations/?label=staged_turn").content.decode()
    assert len(listed_ids(first)) == 50
    assert "?label=staged_turn&amp;page=2" in first
    second = operator_client.get("/operations/?label=staged_turn&page=2").content.decode()
    assert len(listed_ids(second)) == 1


def test_engine_page_labels_its_rows_before_listing_them(
    operator_client: Client, operator: Any, engine_page: FakeEngine
) -> None:
    row = make_row(engine_operation_id=STAGED_OP)
    other = make_row(engine_operation_id=INCOMPLETE_OP)
    operator_client.get(f"/operations/engine/{STAGED_OP}/")
    row.refresh_from_db()
    other.refresh_from_db()
    assert row.engine_label == "staged_turn"
    assert other.engine_label is None
    assert EngineOperation.objects.count() == 2


def test_a_failed_engine_page_read_labels_nothing(
    operator_client: Client, operator: Any, fetch_engine: FakeEngine
) -> None:
    fetch_engine.respond(
        "GET",
        "/operations/{operation_id}",
        404,
        json={"error": {"code": "not_found", "message": "gone", "details": {}}},
    )
    row = make_row(engine_operation_id=STAGED_OP)
    assert operator_client.get(f"/operations/engine/{STAGED_OP}/").status_code == 404
    row.refresh_from_db()
    assert row.engine_label is None
