"""The shared M3 building blocks #64 ships (each has no page of its own): merge-patch diffs,
two-stage confirmation tokens, ``JsonBoxField``, the entity registry, ``principal_link``,
``@operation(recorded=…, entity=…)`` and ``complete_job``, the report models and partials, the
timeouts, ``rating_choices``, ``story_labels`` and the M3 nav sections."""

import copy
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django import forms
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings

from console import merge_patch, twostage
from console.engine import timeouts
from console.engine.choices import rating_choices, story_labels
from console.engine.client import (
    OPERATIONS,
    Call,
    EngineClient,
    Operation,
    calling,
    operation,
)
from console.engine.models import EntryLocation, ReportEntry, ValidationReport
from console.engine.pool import fan_out
from console.engine_access import RecordingEngineClient, complete_job, engine_client_for
from console.entities import ENTITY_TYPES, REGISTRY, entity_link, entity_url, unnamed_label
from console.forms.fields import JsonBoxField
from console.models import EngineOperation
from console.reports import field_anchor, location_anchor, report_from_json, report_from_row
from console.templatetags.principals import principal_link
from tests.fakes.engine import FakeEngine, recorded_schema, with_test_thing
from tests.fixtures import engine_fixture

URL = "http://engine.test"
STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
CHAR = "01JB2Z8Y5Q3W6K9M1N4P7RCHAR"
JOB = "01JB2Z8Y5Q3W6K9M1N4P7R0JOB"


# --- merge_patch ----------------------------------------------------------------------------------


def test_diff_nested_change_sends_only_the_changed_leaf() -> None:
    original = {"name": "Ada", "settings": {"tone": "dry", "length": 3}}
    new = {"name": "Ada", "settings": {"tone": "warm", "length": 3}}
    assert merge_patch.diff(original, new) == {"settings": {"tone": "warm"}}


def test_diff_removed_key_is_null() -> None:
    assert merge_patch.diff({"a": 1, "b": {"c": 2, "d": 3}}, {"b": {"c": 2}}) == {
        "a": None,
        "b": {"d": None},
    }


def test_diff_replaces_a_list_whole() -> None:
    assert merge_patch.diff({"tags": ["a", "b"]}, {"tags": ["a", "c"]}) == {"tags": ["a", "c"]}


def test_diff_no_change_is_empty() -> None:
    value = {"a": [1, {"b": None}], "c": {"d": True}}
    assert merge_patch.diff(value, copy.deepcopy(value)) == {}


def test_diff_new_key_and_type_change() -> None:
    assert merge_patch.diff({"a": 1, "b": {"x": 1}}, {"a": True, "b": 5, "c": {"y": 2}}) == {
        "a": True,  # a JSON boolean is not the number 1
        "b": 5,
        "c": {"y": 2},
    }


def test_apply_round_trips_a_diff() -> None:
    original = {"a": 1, "b": {"c": 2, "d": [1]}, "e": "x"}
    new = {"a": 2, "b": {"d": [2]}, "f": {"g": 1}}
    assert merge_patch.apply(original, merge_patch.diff(original, new)) == new


# --- twostage -------------------------------------------------------------------------------------


def test_twostage_round_trip() -> None:
    token = twostage.sign("profile-edit", "P1", {"name": "x"}, base={"revision": 7})
    payload = twostage.unsign("profile-edit", token)
    assert payload == twostage.Payload("profile-edit", "P1", {"name": "x"}, {"revision": 7})


def test_twostage_tampered_token_is_refused() -> None:
    token = twostage.sign("erasure", "S1", {"scope": "story"})
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(twostage.ConfirmationExpired, match="expired — submit the form again"):
        twostage.unsign("erasure", tampered)


def test_twostage_other_kind_is_refused() -> None:
    token = twostage.sign("erasure", "S1", {})
    with pytest.raises(twostage.ConfirmationExpired):
        twostage.unsign("offboarding", token)


def test_twostage_expired_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    token = twostage.sign("erasure", "S1", {"scope": "story"})
    later = time.time() + twostage.MAX_AGE_S + 5
    monkeypatch.setattr(time, "time", lambda: later)
    with pytest.raises(twostage.ConfirmationExpired):
        twostage.unsign("erasure", token)


def test_twostage_partial_renders_token_key_and_cancel() -> None:
    html = render_to_string(
        "console/_two_stage.html",
        {
            "token": "TOKEN",
            "confirm_url": "/erasure/confirm/",
            "confirm_label": "Erase now",
            "cancel_url": "/erasure/",
            "idempotency_key": "01JB2Z8Y5Q3W6K9M1N4P7R0KEY",
            "danger": True,
        },
    )
    assert f'name="{twostage.CONFIRM_FIELD}" value="TOKEN"' in html
    assert 'name="idempotency_key" value="01JB2Z8Y5Q3W6K9M1N4P7R0KEY"' in html
    assert '<button type="submit" class="danger">Erase now</button>' in html
    assert '<a class="button" href="/erasure/">Cancel</a>' in html
    assert 'action="/erasure/confirm/"' in html


# --- JsonBoxField ---------------------------------------------------------------------------------


class JsonForm(forms.Form):
    schema = JsonBoxField(dict, empty=None, required=False)
    rules = JsonBoxField(list, empty=[], required=False)


@pytest.mark.parametrize(
    ("field", "text", "error"),
    [
        ("schema", "[1, 2]", "Enter a JSON object ({…})."),
        ("rules", '{"a": 1}', "Enter a JSON array ([…])."),
        ("schema", '{"a": NaN}', "NaN is not valid JSON"),
        ("schema", '{"a": Infinity}', "Infinity is not valid JSON"),
        ("rules", "[-Infinity]", "-Infinity is not valid JSON"),
        ("schema", "{oops", "Enter valid JSON"),
        ("schema", '{"a": 1, "a": 2}', "duplicate key 'a'"),  # #68
        ("rules", '[{"b": {"c": 1, "c": 1}}]', "duplicate key 'c'"),
    ],
)
def test_json_box_refuses_before_any_call(field: str, text: str, error: str) -> None:
    form = JsonForm({field: text})
    assert not form.is_valid()
    assert error in form.errors[field][0]


def test_json_box_parses_and_blank_is_empty() -> None:
    form = JsonForm({"schema": ' {"a": [1, 2.5]} ', "rules": ""})
    assert form.is_valid(), form.errors
    assert form.cleaned_data == {"schema": {"a": [1, 2.5]}, "rules": []}


def test_json_box_renders_an_initial_value_as_json() -> None:
    html = str(JsonForm(initial={"schema": {"a": "é"}})["schema"])
    assert "&quot;a&quot;: &quot;é&quot;" in html


def test_json_box_required() -> None:
    class Required(forms.Form):
        body = JsonBoxField(dict)

    assert not Required({"body": ""}).is_valid()


# --- entity registry and principal_link -----------------------------------------------------------


def test_the_registry_covers_the_engines_entity_types() -> None:
    enum = recorded_schema()["components"]["schemas"]["EntityType"]["enum"]
    assert set(ENTITY_TYPES) == set(enum) == set(REGISTRY)
    assert REGISTRY["engine_profile"].lookup_key == "engine_profiles"
    assert REGISTRY["character"].engine_root == "/catalog/characters"


def test_a_known_type_resolves_to_its_page() -> None:
    assert entity_url("story", STORY) == f"/stories/{STORY}/"
    assert entity_link("story", STORY) == f'<a href="/stories/{STORY}/"><code>{STORY}</code></a>'


def test_an_unknown_type_renders_as_code() -> None:
    assert entity_link("gadget", "G1") == "<code>G1</code>"
    assert entity_link(None, "<b>") == "<code>&lt;b&gt;</code>"


@override_settings(ROOT_URLCONF="tests.urls_bare")
def test_a_type_without_its_page_yet_renders_as_code() -> None:
    # Every registered type has its page since #78; an empty URLconf stands in for "not yet".
    assert entity_link("engine_profile", CHAR) == f"<code>{CHAR}</code>"


@override_settings(ROOT_URLCONF="tests.urls_m3")
def test_a_type_links_once_its_page_exists() -> None:
    assert entity_link("scenario", CHAR) == (
        f'<a href="/catalog/scenarios/{CHAR}/"><code>{CHAR}</code></a>'
    )
    # #65 built the character pages and #68 the scenario pages: they link in the real URLconf.
    assert entity_link("character", CHAR) == (
        f'<a href="/catalog/characters/{CHAR}/"><code>{CHAR}</code></a>'
    )


def test_unnamed_label() -> None:
    assert unnamed_label(STORY) == "(unnamed) …7R0T2V"


@override_settings(ROOT_URLCONF="tests.urls_bare")
def test_principal_link_falls_back_to_code() -> None:
    assert principal_link("alice") == "<code>alice</code>"
    assert principal_link("<x>") == "<code>&lt;x&gt;</code>"
    assert principal_link("") == "—"


def test_principal_link_links_once_principals_detail_resolves() -> None:
    assert principal_link("alice") == '<a href="/principals/alice/"><code>alice</code></a>'
    # quote(p, safe="") first; reverse encodes the "%" once more (#76's view unquotes).
    assert principal_link("a/b") == '<a href="/principals/a%252Fb/"><code>a/b</code></a>'


# --- @operation(recorded=…, entity=…), complete_job, last_operation_id ----------------------------


def thing_doc() -> dict[str, Any]:
    """The schema plus ``/_test/thing`` (GET/POST), whose POST may also answer 202 with a job."""
    doc = with_test_thing(recorded_schema())
    job = {
        "description": "queued",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}, "id": {"type": "string"}},
                }
            }
        },
    }
    doc["paths"]["/_test/thing"]["post"]["responses"]["202"] = job
    return doc


@pytest.fixture
def thing() -> Iterator[FakeEngine]:
    with override_settings(ENGINE_URL=URL, ENGINE_SERVICE_KEY="k" * 32):
        yield FakeEngine(thing_doc())


def recording(operator: Any, fake: FakeEngine) -> RecordingEngineClient:
    request = RequestFactory().post("/")
    request.user = operator
    client = engine_client_for(request)
    client._transport = fake.transport
    return client


def call(name: str, method: str, **flags: Any) -> Call:
    arguments = flags.pop("arguments", {})
    return Call(name, Operation(method, "/_test/thing", None, False, (), **flags), arguments)


@pytest.mark.django_db
def test_a_post_flagged_recorded_false_writes_no_row(operator: Any, thing: FakeEngine) -> None:
    thing.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client = recording(operator, thing)
    with calling(call("quiet_post", "POST", recorded=False)):
        client._request("POST", "/_test/thing")
    assert len(thing.requests) == 1
    assert not EngineOperation.objects.exists()
    assert client.last_operation_id is None


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["POST", "GET"])
def test_a_non_writing_call_flagged_recorded_true_writes_one_row(
    operator: Any, thing: FakeEngine, method: str
) -> None:
    thing.respond(method, "/_test/thing", 200, json={"name": "x"})
    client = recording(operator, thing)
    with calling(call("validate_thing", method, recorded=True)):
        client._request(method, "/_test/thing")
    (row,) = EngineOperation.objects.all()
    assert row.kind == "validate_thing"
    assert row.outcome == "ok"
    assert row.idempotency_key == ""  # no Idempotency-Key sent (convention 19)
    assert client.last_operation_id == row.pk


@pytest.mark.django_db
def test_a_plain_read_still_writes_nothing(operator: Any, thing: FakeEngine) -> None:
    thing.respond("GET", "/_test/thing", 200, json={"name": "x"})
    client = recording(operator, thing)
    with calling(call("read_thing", "GET")):
        client._request("GET", "/_test/thing")
    assert not EngineOperation.objects.exists()


@pytest.mark.django_db
def test_the_entity_comes_from_the_named_argument(operator: Any, thing: FakeEngine) -> None:
    thing.respond("POST", "/_test/thing", 200, json={"name": "x"})
    client = recording(operator, thing)
    flags = {"entity": "character", "entity_id": "character_id"}
    with calling(call("publish_thing", "POST", arguments={"character_id": CHAR}, **flags)):
        client._request("POST", "/_test/thing")
    row = EngineOperation.objects.get()
    assert (row.entity_type, row.entity_id) == ("character", CHAR)


@pytest.mark.django_db
def test_a_create_reads_its_entity_id_from_the_response(operator: Any, thing: FakeEngine) -> None:
    thing.respond("POST", "/_test/thing", 202, json={"id": CHAR, "job_id": JOB})
    client = recording(operator, thing)
    with calling(call("create_thing", "POST", entity="persona", entity_id="response:id")):
        client._request("POST", "/_test/thing")
    row = EngineOperation.objects.get()
    assert (row.entity_type, row.entity_id) == ("persona", CHAR)
    assert (row.outcome, row.job_id) == ("queued", JOB)


@pytest.mark.django_db
def test_a_story_call_is_about_its_story(operator: Any, fake_engine: FakeEngine) -> None:
    fake_engine.respond(
        "POST", "/stories/{story_id}/archive", 200, json=engine_fixture("archive_story")
    )
    with override_settings(ENGINE_URL=URL):
        client = recording(operator, fake_engine)
        client.archive_story(STORY, idempotency_key="01JB2Z8Y5Q3W6K9M1N4P7R0KEY")
    row = EngineOperation.objects.get()
    assert (row.entity_type, row.entity_id, row.story_id) == ("story", STORY, STORY)


def test_the_decorator_binds_the_entity_argument() -> None:
    seen: list[Call | None] = []

    class Client(EngineClient):
        @operation(
            "POST", "/_test/decorated/{character_id}", entity="character", entity_id="character_id"
        )
        def publish(self, character_id: str, *, note: str = "") -> None:
            from console.engine.client import current_call

            seen.append(current_call())

    try:
        Client(URL, None).publish(CHAR, note="x")
        (current,) = seen
        assert current is not None
        assert current.kind == "publish"
        assert current.operation.is_recorded
        assert current.entity_ref() == ("character", CHAR)
    finally:
        OPERATIONS.pop("POST /_test/decorated/{character_id}", None)


def test_the_decorator_refuses_an_unknown_entity_argument() -> None:
    with pytest.raises(ValueError, match="no parameter 'thing_id'"):

        @operation("POST", "/_test/bad", entity="media", entity_id="thing_id")
        def bad(media_id: str) -> None: ...

    OPERATIONS.pop("POST /_test/bad", None)


def test_recorded_defaults_by_method() -> None:
    assert Operation("DELETE", "/x", None, False).is_recorded
    assert not Operation("GET", "/x", None, False).is_recorded
    assert Operation("GET", "/x", None, False, recorded=True).is_recorded
    assert not Operation("POST", "/x", None, False, recorded=False).is_recorded


def queued_row(**fields: Any) -> EngineOperation:
    defaults: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "alice",
        "kind": "put_story_content_rating",
        "http_method": "PUT",
        "path": f"/stories/{STORY}/content-rating",
        "outcome": "queued",
        "http_status": 202,
        "job_id": JOB,
        "response": {"job_id": JOB},
    }
    return EngineOperation.objects.create(**{**defaults, **fields})


@pytest.mark.django_db
def test_complete_job_is_idempotent() -> None:
    row = queued_row()
    done = {"status": "refused", "secret": "secret-value"}
    assert complete_job(JOB, "error", "rating_refused", done) == 1
    row.refresh_from_db()
    assert (row.outcome, row.error_code) == ("error", "rating_refused")
    assert row.response == {"status": "refused", "secret": "***"}  # stored redacted
    assert complete_job(JOB, "ok", "", {"status": "succeeded"}) == 0  # the second call
    row.refresh_from_db()
    assert (row.outcome, row.error_code) == ("error", "rating_refused")


@pytest.mark.django_db
def test_complete_job_overrides_and_only_its_job() -> None:
    other = queued_row(job_id="01JB2Z8Y5Q3W6K9M1N4P7R0OTH")
    row = queued_row()
    overrides = {"timeline_revision": 9, "story_id": STORY, "engine_operation_id": "OP"}
    assert complete_job(JOB, "ok", overrides=overrides) == 1
    row.refresh_from_db()
    other.refresh_from_db()
    assert (row.outcome, row.timeline_revision, row.story_id) == ("ok", 9, STORY)
    assert row.response == {"job_id": JOB}  # no response given: the 202's is kept
    assert other.outcome == "queued"


@pytest.mark.django_db
def test_complete_job_refuses_bad_arguments() -> None:
    with pytest.raises(ValueError, match="terminal"):
        complete_job(JOB, "queued")
    with pytest.raises(ValueError, match="override"):
        complete_job(JOB, "ok", overrides={"outcome": "ok"})


# --- report models and partials -------------------------------------------------------------------


REPORT = {
    "errors": [
        {
            "code": "missing_role",
            "location": {"entry": "E1", "version": "draft", "field": "cast.roles[1].name"},
            "outcome": "rejected",
            "rule": "roles need names",
            "text": "<blank>",
        }
    ],
    "warnings": [
        {
            "code": "personality_in_role",
            "location": {"entry": "E1", "version": 3, "role": "mother", "seed_id": "S9"},
            "outcome": "ignored",
            "source": "scenario",
        }
    ],
    "unavailable": ["fact_consistency"],
}


def test_the_report_parses_both_location_shapes() -> None:
    report = ValidationReport.from_json(REPORT)
    assert report is not None
    (error,) = report.errors
    assert error.location == EntryLocation("E1", "draft", field="cast.roles[1].name")
    assert report.warnings[0].location.version == 3
    element = EntryLocation.from_json({"entry": "E1", "version": 2, "media_id": "M1"})
    assert element is not None
    assert element.media_id == "M1"
    assert EntryLocation.from_json({"entry": "E1", "version": "v2"}) is None


def test_field_anchor_and_ancestor_fallback() -> None:
    assert field_anchor("cast.roles[1].name") == "f-cast-roles-1--name"
    location = EntryLocation("E1", "draft", field="cast.roles[1].name", role="r1")
    assert location_anchor(location) == "f-cast-roles-1--name"
    assert location_anchor(location, {"f-cast-roles-1-", "f-cast"}) == "f-cast-roles-1-"
    assert location_anchor(location, {"role-r1"}) == "role-r1"
    assert location_anchor(EntryLocation("E1", 1, seed_id="S9", role="r1")) == "seed-S9"
    assert location_anchor(EntryLocation("E1", 1)) is None


def test_validation_report_renders_an_error_and_a_warning_with_anchors() -> None:
    report = ValidationReport.from_json(REPORT)
    html = render_to_string(
        "console/_validation_report.html",
        {"report": report, "label": "Companion", "anchor_base": "/catalog/characters/C1/edit/"},
    )
    assert "Companion: validation report" in html
    assert "<code>missing_role</code> — rejected · rule: roles need names" in html
    assert (
        '<a href="/catalog/characters/C1/edit/#f-cast-roles-1--name">'
        "<code>cast.roles[1].name</code></a>"
    ) in html
    assert "<blockquote>&lt;blank&gt;</blockquote>" in html
    assert "<code>personality_in_role</code> — ignored · source: scenario" in html
    assert 'seed <a href="/catalog/characters/C1/edit/#seed-S9"><code>S9</code></a>' in html
    assert "entry <code>E1</code> v3 · role <code>mother</code>" in html
    assert "<code>fact_consistency</code>" in html


def test_report_from_json_finds_the_report() -> None:
    assert report_from_json(REPORT) == ValidationReport.from_json(REPORT)
    assert report_from_json({"valid": False, "report": REPORT}) == ValidationReport.from_json(
        REPORT
    )
    invalid = {"error": {"code": "publish_invalid", "message": "", "details": {"report": REPORT}}}
    assert report_from_json(invalid) is not None
    assert report_from_json({"name": "x"}) is None


@pytest.mark.django_db
def test_a_report_after_a_redirect_is_read_from_its_row() -> None:
    row = queued_row(outcome="error", response={"valid": False, "report": REPORT})
    assert report_from_row(str(row.pk)) == ValidationReport.from_json(REPORT)
    assert report_from_row("nope") is None
    assert report_from_row(row.pk + 1) is None


def test_report_entry_mirrors_the_engine_entry() -> None:
    errors: Any = REPORT["errors"]
    entry = ReportEntry.from_json(errors[0])
    assert entry is not None
    assert entry.source is None
    assert entry.rule == "roles need names"


# --- timeouts -------------------------------------------------------------------------------------


def test_timeouts() -> None:
    assert timeouts.CATALOG_PUBLISH == 30
    assert timeouts.SCENARIO_CREATE == 30
    assert timeouts.ERASURE == 60
    assert timeouts.UPLOAD == 60
    assert timeouts.rating_suggestion(3) == 70
    assert timeouts.rating_suggestion(0) == 10


# --- rating_choices and story_labels --------------------------------------------------------------


def ratings_body() -> dict[str, Any]:
    body = engine_fixture("list_content_ratings_all")
    items = body["items"]
    items[3]["retired_at"] = "2026-10-24T21:36:26Z"  # Explicit, retired
    body["items"] = [items[2], items[0], items[3], items[1]]  # out of display order
    return dict(body)


def plain_client(fake: FakeEngine) -> EngineClient:
    return EngineClient(URL, None, transport=fake.transport, principal="alice")


def test_rating_choices_in_display_order_without_retired(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/admin/content-ratings", 200, json=ratings_body())
    result = rating_choices(plain_client(fake_engine))
    assert result.ok
    assert [label for _, label in result.choices] == ["Everyone", "Teen", "Mature"]
    assert fake_engine.requests[0].url.params["include_retired"] == "1"
    assert len(fake_engine.requests) == 1


def test_rating_choices_keeps_a_retired_or_missing_current(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", "/admin/content-ratings", 200, json=ratings_body())
    client = plain_client(fake_engine)
    retired = rating_choices(client, current="00000000000000000000RAT004")
    assert retired.choices[-1] == ("00000000000000000000RAT004", "Explicit (retired)")
    missing = rating_choices(client, current="00000000000000000000RAT999")
    assert missing.choices[-1] == (
        "00000000000000000000RAT999",
        "00000000000000000000RAT999 (missing)",
    )
    everything = rating_choices(client, include_retired=True)
    assert [label for _, label in everything.choices] == [
        "Everyone",
        "Teen",
        "Mature",
        "Explicit (retired)",
    ]


def test_rating_choices_on_a_failed_read(fake_engine: FakeEngine) -> None:
    error = {"error": {"code": "unavailable", "message": "down", "details": {}}}
    fake_engine.respond("GET", "/admin/content-ratings", 503, json=error)
    result = rating_choices(plain_client(fake_engine))
    assert not result.ok
    assert result.choices == []
    assert result.unavailable_text == "Ratings unavailable: 503 unavailable — reload to retry"


def story_body(story_id: str, name: str | None) -> dict[str, Any]:
    body = dict(engine_fixture("get_story"))
    body["id"] = story_id
    body["name"] = name
    return body


def test_story_labels(fake_engine: FakeEngine) -> None:
    named, unnamed, held, gone = (
        f"01JB2Z8Y5Q3W6K9M1N4P7R0{s}" for s in ("AAA", "BBB", "CCC", "DDD")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        story_id = request.url.path.rsplit("/", 1)[-1]
        if story_id == named:
            return httpx.Response(200, json=story_body(named, "Harbour"))
        if story_id == unnamed:
            return httpx.Response(200, json=story_body(unnamed, None))
        code, status = ("erasure_pending", 503) if story_id == held else ("not_found", 404)
        return httpx.Response(status, json={"error": {"code": code, "message": "", "details": {}}})

    client = EngineClient(URL, None, transport=httpx.MockTransport(handler), principal="alice")
    labels = story_labels(client, [named, unnamed, held, gone, named])
    assert list(labels) == [named, unnamed, held, gone]
    assert labels[named].text == "Harbour"
    assert labels[unnamed].text == "(unnamed) …7R0BBB"
    assert labels[held].text == f"held …{held[-6:]}"
    assert labels[held].held
    assert labels[held].ok
    assert (labels[gone].text, labels[gone].error) == (gone, "404 not_found")


def test_story_labels_reads_at_most_the_cap() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        story_id = request.url.path.rsplit("/", 1)[-1]
        seen.append(story_id)
        return httpx.Response(200, json=story_body(story_id, "S"))

    client = EngineClient(URL, None, transport=httpx.MockTransport(handler), principal="alice")
    ids = [f"01JB2Z8Y5Q3W6K9M1N4P7R0{i:03d}" for i in range(4)]
    labels = story_labels(client, ids, cap=2)
    assert sorted(seen) == ids[:2]
    assert labels[ids[3]].error == "not looked up"


def test_fan_out_reports_a_timeout_and_errors() -> None:
    from console.engine.client import EngineUnreachable

    def read(key: int) -> int:
        if key == 2:
            raise EngineUnreachable("connection failed")
        if key == 3:
            time.sleep(0.5)
        return key * 10

    results = fan_out(read, [1, 2, 3, 1], deadline_s=0.2)
    assert results[1] == 10
    assert isinstance(results[2], EngineUnreachable)
    timed_out = results[3]
    assert isinstance(timed_out, EngineUnreachable)
    assert timed_out.reason == "timeout"


# --- nav ------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_m3_sections_are_hidden_until_their_urls_exist(operator_client: Client) -> None:
    html = operator_client.get("/operations/").content.decode()
    nav = html[html.index('<nav class="shell">') : html.index("</nav>")]
    # #76 built the principal pages, #78 the profile pages, #86 the erasure pages.
    assert '<a href="/erasure/">Erasure</a>' in nav
    assert '<a href="/principals/">Principals</a>' in nav
    assert '<a href="/profiles/">Profiles</a>' in nav
    # #65 built the character list, #67 the personas, #68 the scenarios and #70 the media
    # library: the Catalog group shows with them.
    assert '<span class="nav-group-label">Catalog:</span> <a href="/catalog/characters/">' in nav
    assert '<a href="/catalog/scenarios/">Scenarios</a>' in nav
    assert '<a href="/personas/">Personas</a>' in nav
    assert '<a href="/media/">Media</a>' in nav
    assert nav.index(">Engine<") < nav.index(">Operations<") < nav.index(">Admin<")


def _nav(html: str) -> str:
    return html[html.index('<nav class="shell">') : html.index("</nav>")]


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_no_offboarding")
def test_a_section_is_absent_while_its_url_is_missing(operator_client: Client) -> None:
    nav = _nav(operator_client.get("/operations/").content.decode())
    assert ">Offboarding<" not in nav
    assert "/offboarding/" not in nav
    # The sections whose URLs do resolve still show.
    assert '<a href="/principals/">Principals</a>' in nav
    assert '<a href="/erasure/">Erasure</a>' in nav


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_no_catalog")
def test_the_catalog_group_is_absent_while_none_of_its_urls_exist(
    operator_client: Client,
) -> None:
    nav = _nav(operator_client.get("/operations/").content.decode())
    assert "Catalog:" not in nav
    assert "nav-group" not in nav
    for label in (">Characters<", ">Scenarios<", ">Personas<", ">Media<"):
        assert label not in nav
    assert '<a href="/operations/" class="active">Operations</a>' in nav


@pytest.mark.django_db
@override_settings(ROOT_URLCONF="tests.urls_m3")
def test_m3_sections_show_in_order_once_their_urls_exist(operator_client: Client) -> None:
    html = operator_client.get("/erasure/").content.decode()
    assert "New erasure" in html  # #86's real page replaced the placeholder (no Engine call)
    html = operator_client.get("/operations/").content.decode()
    nav = html[html.index('<nav class="shell">') : html.index("</nav>")]
    assert '<span class="nav-group-label">Catalog:</span> <a href="/catalog/characters/">' in nav
    order = [
        ">Engine<",
        "Catalog:",
        ">Characters<",
        ">Scenarios<",
        ">Personas<",
        ">Media<",
        ">Principals<",
        ">Erasure<",
        ">Operations<",
    ]
    positions = [nav.index(label) for label in order]
    assert positions == sorted(positions)
    assert '<a href="/operations/" class="active">Operations</a>' in nav


@override_settings(ROOT_URLCONF="tests.urls_m3")
def test_catalog_is_active_under_its_prefixes(rf: RequestFactory) -> None:
    from console.context_processors import shell

    for path, active in (("/media/x/", True), ("/personas/", True), ("/catalog/a/", True)):
        request: HttpRequest = rf.get(path)
        assert shell(request)["nav_catalog"] == {
            "items": [
                {
                    "label": "Characters",
                    "url": "/catalog/characters/",
                    "active": path.startswith("/catalog/characters/"),
                },
                {
                    "label": "Scenarios",
                    "url": "/catalog/scenarios/",
                    "active": path.startswith("/catalog/scenarios/"),
                },
                {
                    "label": "Personas",
                    "url": "/personas/",
                    "active": path.startswith("/personas/"),
                },
                {
                    "label": "Media",
                    "url": "/media/",
                    "active": path.startswith("/media/"),
                },
            ],
            "active": active,
        }
    catalog: Any = shell(rf.get("/stories/"))["nav_catalog"]
    assert not catalog["active"]


@pytest.mark.django_db
def test_the_admin_lists_the_new_models(operator_client: Client) -> None:
    html = operator_client.get("/admin/console/").content.decode()
    assert "Acting principals" in html
    assert "Known principals" in html
    assert operator_client.get("/admin/console/knownprincipal/").status_code == 200
    assert operator_client.get("/admin/console/actingprincipal/").status_code == 200
