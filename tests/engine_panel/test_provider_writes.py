"""#246: register, replace and delete providers, against the schema-validating fake Engine."""

import copy
import html as html_lib
import json
import re
from typing import Any

import httpx
from django.contrib.messages import get_messages
from django.test import Client

from console.forms.providers import SPEC_FIELDS
from console.models import EngineOperation
from tests.engine_panel.helpers import body, calls, error, form_data
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

LIST = "/admin/external-providers"
ONE = "/admin/external-providers/{name}"
BANK = "/admin/external-providers/bank"
NEW = "/engine/provider-registration/"
EDIT = "/engine/providers/bank/replace/"
DELETE = "/engine/providers/bank/delete/"
DETAIL = "/engine/providers/bank/"
STORY_A = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
STORY_B = "01JB2Z8Y5Q3W6K9M1N4P7R0T2W"
MANIFEST_TEXT = (
    '{"values": {"balance": {"type": "number",\n'
    '  "read": {"url": "https://bank.example/accounts/{binding.account_id}/balance"}}}}'
)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def text(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def key_in(page: str) -> str:
    found = re.search(r'name="idempotency_key" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


def registration(**changes: str) -> dict[str, str]:
    posted = {
        "idempotency_key": "01JM6KEY0000000000000000AA",
        "name": "bank",
        "trust_tier": "authoritative",
        "transport": "http",
        "manifest": MANIFEST_TEXT,
        "write_capable": "false",
        "confirmation": "",
        "dedupes_dispatch": "",
        "returns_resulting_values": "",
        "unavailability": "last_known",
        "freshness_minutes": "",
        "confirm_wait_max_seconds": "10",
    }
    posted.update(changes)
    return posted


def mutation(**changes: Any) -> dict[str, Any]:
    answer: dict[str, Any] = copy.deepcopy(engine_fixture("m6/provider_mutation"))
    answer.update(changes)
    return answer


def provider(**changes: Any) -> dict[str, Any]:
    answer: dict[str, Any] = copy.deepcopy(engine_fixture("m6/provider"))
    answer.update(changes)
    return answer


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method != "GET"]


def manifest_lines(page: str) -> list[str]:
    block = re.search(r'<ul class="manifest-failures">(.*?)</ul>', page, re.S)
    assert block is not None
    return re.findall(r"<li[^>]*>(.*?)</li>", block.group(1), re.S)


# --- register -----------------------------------------------------------------------------------


def test_register_get_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(NEW)
    page = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    assert "Register a provider" in page
    assert key_in(page)
    assert 'action="/engine/provider-registration/"' in page
    assert '<details class="manifest-example"><summary>Example</summary>' in page
    assert "api.game.example" in page  # the recorded example's manifest
    assert "<code>in_process</code>" in page  # schema help through code_spans


def test_register_sends_every_field_and_redirects(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", LIST, 201, json=mutation())
    response = operator_client.post(NEW, registration(freshness_minutes="15"))

    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (sent,) = calls(engine, "POST", LIST)
    payload = body(sent)
    assert set(payload) == {*SPEC_FIELDS, "name"}
    assert len(payload) == 11
    assert payload["name"] == "bank"
    assert payload["freshness_minutes"] == 15
    assert payload["confirmation"] is None
    assert payload["manifest"] == json.loads(MANIFEST_TEXT)
    assert sent.headers["Idempotency-Key"] == "01JM6KEY0000000000000000AA"
    assert flashes(response) == [
        "Registered bank — config revision 5, idempotency key 01JM6KEY0000000000000000AA"
    ]


def test_invalid_form_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    cases = [
        {"name": "Bank"},
        {"manifest": "{not json"},
        {"write_capable": "true"},
        {"freshness_minutes": "0"},
    ]
    for case in cases:
        response = operator_client.post(NEW, registration(**case))
        page = response.content.decode()
        assert response.status_code == 200, case
        assert key_in(page) == "01JM6KEY0000000000000000AA", case  # D8: nothing sent
    assert calls(engine, "POST", LIST) == []
    assert engine.requests == []


def _refuse(engine: FakeEngine, status: int, code: str, message: str, **details: Any) -> None:
    engine.respond("POST", LIST, status, json=error(code, message, **details))


def test_invalid_manifest_host_not_allowed(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(
        engine,
        422,
        "invalid_manifest",
        "the URL's host is not in provider_allowed_hosts",
        field="body.manifest.values.balance.read.url",
        reason="host_not_allowed",
    )
    page = operator_client.post(NEW, registration()).content.decode()
    (line,) = manifest_lines(page)
    assert text(line) == (
        "values.balance.read.url: the URL's host is not in provider_allowed_hosts — add the host"
        " to provider_allowed_hosts in Deployment settings"
    )
    assert "<code>values.balance.read.url</code>" in line
    assert 'href="/engine/settings/#settings-external-providers"' in line
    assert 'title="host_not_allowed"' in page
    assert 'aria-invalid="true"' in re.search(r"<textarea[^>]*>", page).group(0)  # type: ignore[union-attr]


def test_invalid_manifest_at_member(operator_client: Client, engine: FakeEngine) -> None:
    cases = [
        ("body.manifest.values.balance.read.url", "unknown_placeholder", "unknown placeholder"),
        ("body.manifest.values.balance.read.value_path", "path_expression", "bad path"),
        ("body.manifest.values.balance.write", "not_write_capable", "no write template"),
        ("body.manifest", "shape", "not a manifest"),
    ]
    for field, reason, message in cases:
        _refuse(engine, 422, "invalid_manifest", message, field=field, reason=reason)
        page = operator_client.post(NEW, registration()).content.decode()
        (line,) = manifest_lines(page)
        member = field.removeprefix("body.manifest").removeprefix(".") or "manifest"
        assert text(line) == f"{member}: {message}", field
        assert f'title="{reason}"' in page
        assert "settings-external-providers" not in line


def test_invalid_manifest_schema_errors_in_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    _refuse(
        engine,
        422,
        "invalid_manifest",
        "the manifest is invalid",
        field="body.manifest",
        errors=[
            {"field": "body.manifest.values.b.type", "message": "missing type", "type": "missing"},
            {"field": "body.manifest.values.a", "message": "not an object", "type": "dict_type"},
        ],
    )
    page = operator_client.post(NEW, registration()).content.decode()
    lines = [text(li) for li in manifest_lines(page)]
    assert lines == ["values.b.type: missing type", "values.a: not an object"]
    assert 'title="missing"' in page
    assert 'title="dict_type"' in page


def test_invalid_manifest_without_field_is_one_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    _refuse(engine, 422, "invalid_manifest", "manifest refused")
    page = operator_client.post(NEW, registration()).content.decode()
    assert [text(li) for li in manifest_lines(page)] == ["manifest: manifest refused"]


def test_a_refused_registration_writes_nothing(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(
        engine,
        422,
        "invalid_manifest",
        "the URL's host is not in provider_allowed_hosts",
        field="body.manifest.values.balance.read.url",
        reason="host_not_allowed",
    )
    posted = registration()
    response = operator_client.post(NEW, posted)
    page = response.content.decode()

    assert len(engine.requests) == 1  # one POST, no retry, no follow-up read
    assert engine.requests[0].method == "POST"
    assert response.status_code == 200
    assert flashes(response) == []
    assert form_data(page, NEW)["manifest"] == posted["manifest"]  # byte for byte
    assert key_in(page) != posted["idempotency_key"]  # D8: an answered refusal, a fresh key


def test_transport_failure_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", LIST, httpx.ReadTimeout("slow"))
    posted = registration()
    page = operator_client.post(NEW, posted).content.decode()
    assert 'class="engine-error' in page
    assert key_in(page) == posted["idempotency_key"]


def test_provider_exists_on_name(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(engine, 409, "provider_exists", "already registered", provider="bank")
    page = operator_client.post(NEW, registration()).content.decode()
    error_block = re.search(r'<div class="field field-name">(.*?)</div>', page, re.S)
    assert error_block is not None
    assert text(error_block.group(1)).endswith(
        "A provider named “bank” is already registered — open it to replace its registration"
    )
    assert '<a href="/engine/providers/bank/">open it</a>' in error_block.group(1)
    assert 'class="engine-error' not in page


def test_validation_error_lands_on_its_field(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(
        engine,
        422,
        "validation_error",
        "invalid",
        errors=[{"field": "body.confirmation", "message": "required", "type": "required"}],
    )
    page = operator_client.post(NEW, registration()).content.decode()
    field = re.search(r'<div class="field field-confirmation">(.*?)</div>', page, re.S)
    assert field is not None
    assert '<ul class="errorlist"' in field.group(1)
    assert "required" in text(field.group(1))
    assert 'class="manifest-failures"' not in page


def test_unmirrored_path_goes_above_the_form(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(
        engine,
        422,
        "validation_error",
        "invalid",
        errors=[{"field": "body.extra", "message": "not permitted", "type": "extra_forbidden"}],
    )
    page = operator_client.post(NEW, registration()).content.decode()
    assert "extra: not permitted" in page


def test_config_conflict_panel(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(engine, 409, "config_conflict", "the configuration changed")
    response = operator_client.post(NEW, registration())
    page = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in page
    assert "config_conflict" in page
    assert key_in(page) != "01JM6KEY0000000000000000AA"


def test_writes_are_recorded(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", LIST, 201, json=mutation())
    operator_client.post(NEW, registration())
    row = EngineOperation.objects.get(kind="register_provider")
    assert row.entity_type == "external_provider"
    assert row.entity_id == "bank"


# --- replace ------------------------------------------------------------------------------------


def _replace_page(operator_client: Client, engine: FakeEngine, **changes: Any) -> dict[str, str]:
    engine.respond("GET", ONE, 200, json=provider(**changes))
    page = operator_client.get(EDIT).content.decode()
    return form_data(page, EDIT)


def test_replace_get_reads_once(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE, 200, json=provider())
    page = operator_client.get(EDIT).content.decode()
    assert [r.method for r in engine.requests] == ["GET"]
    assert "Replace registration — <code>bank</code>" in page
    assert 'name="name"' not in page
    assert 'class="manifest-example"' not in page
    posted = form_data(page, EDIT)
    assert posted["write_capable"] == "true"
    assert posted["confirmation"] == "synchronous"
    assert posted["freshness_minutes"] == "10"
    assert json.loads(posted["manifest"]) == provider()["manifest"]


def test_replace_untouched_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.reset()
    response = operator_client.post(EDIT, posted)
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    assert flashes(response) == ["No changes — nothing was sent"]
    assert engine.requests == []

    reindented = dict(posted, manifest=json.dumps(json.loads(posted["manifest"])))
    response = operator_client.post(EDIT, reindented)
    assert flashes(response)[-1] == "No changes — nothing was sent"
    assert calls(engine, "PUT") == []


def test_replace_manifest_canonical_change_is_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    posted = _replace_page(operator_client, engine)
    manifest = json.loads(posted["manifest"])
    manifest["values"]["balance"]["read"]["timeout_seconds"] = 5.0  # 5 → 5.0 is a change
    engine.respond("PUT", ONE, 200, json=mutation())
    operator_client.post(EDIT, dict(posted, manifest=json.dumps(manifest)))
    assert len(calls(engine, "PUT")) == 1


def test_replace_sends_all_fields(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.respond("PUT", ONE, 200, json=mutation(freshness_minutes=20))
    response = operator_client.post(EDIT, dict(posted, freshness_minutes="20"))

    (sent,) = calls(engine, "PUT", BANK)
    payload = body(sent)
    assert set(payload) == set(SPEC_FIELDS)
    assert "name" not in payload
    assert payload["freshness_minutes"] == 20
    original = provider()
    for member in SPEC_FIELDS:
        if member != "freshness_minutes":
            assert payload[member] == original[member], member
    assert sent.headers["Idempotency-Key"] == posted["idempotency_key"]
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (flash,) = flashes(response)
    assert flash == (
        f"Saved bank — config revision 5, idempotency key {posted['idempotency_key']}. It"
        " applies from the next operation that resolves the provider; a turn already under way"
        " keeps what it resolved."
    )


def test_replace_nothing_changed_at_the_engine(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.respond("PUT", ONE, 200, json=mutation(config_revision=4))  # the snapshot's
    response = operator_client.post(EDIT, dict(posted, freshness_minutes="20"))
    assert flashes(response) == ["The Engine found nothing to change"]
    assert response["Location"] == DETAIL


def test_replace_tampered_snapshot_rereads_once(
    operator_client: Client, engine: FakeEngine
) -> None:
    posted = _replace_page(operator_client, engine)
    engine.reset()
    tampered = dict(posted, snapshot=posted["snapshot"][:-2] + "xx")
    response = operator_client.post(EDIT, tampered)
    assert [r.method for r in engine.requests] == ["GET"]  # re-read, then equal: no PUT
    assert flashes(response) == ["No changes — nothing was sent"]

    engine.reset()
    engine.respond("PUT", ONE, 200, json=mutation())
    operator_client.post(EDIT, dict(posted, snapshot="", freshness_minutes="30"))
    assert [r.method for r in engine.requests] == ["GET", "PUT"]


def test_replace_reread_404_says_gone(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.reset()
    engine.respond("GET", ONE, 404, json=error("not_found", "no such provider"))
    response = operator_client.post(EDIT, dict(posted, snapshot=""))
    assert response.status_code == 404
    assert calls(engine, "PUT") == []
    assert "bank is no longer registered — register it again" in text(response.content.decode())


def test_replace_mentions_concurrent_change(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)  # read at config revision 4
    engine.respond("PUT", ONE, 200, json=mutation(config_revision=6))
    response = operator_client.post(EDIT, dict(posted, freshness_minutes="20"))
    (flash,) = flashes(response)
    assert flash.startswith("Saved bank — config revision 6")
    assert flash.endswith(
        " The deployment's configuration also changed elsewhere since you loaded this page;"
        " see Config changes"
    )


def test_replace_404_links_register(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.respond("PUT", ONE, 404, json=error("not_found", "no such provider"))
    response = operator_client.post(EDIT, dict(posted, freshness_minutes="20"))
    page = response.content.decode()
    assert "bank is no longer registered — register it again" in text(page)
    assert '<a href="/engine/provider-registration/">register it again</a>' in page
    assert key_in(page) != posted["idempotency_key"]


def test_replace_invalid_manifest_lines(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.respond(
        "PUT",
        ONE,
        422,
        json=error(
            "invalid_manifest",
            "unknown placeholder",
            field="body.manifest.values.balance.read.url",
            reason="unknown_placeholder",
        ),
    )
    page = operator_client.post(EDIT, dict(posted, freshness_minutes="20")).content.decode()
    assert [text(li) for li in manifest_lines(page)] == [
        "values.balance.read.url: unknown placeholder"
    ]
    assert form_data(page, EDIT)["snapshot"] == posted["snapshot"]


def test_replace_invalid_form_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    posted = _replace_page(operator_client, engine)
    engine.reset()
    page = operator_client.post(
        EDIT, dict(posted, confirmation="", freshness_minutes="20")
    ).content.decode()
    assert engine.requests == []
    assert "A write-capable provider declares this" in page
    assert key_in(page) == posted["idempotency_key"]


def test_replace_get_404_is_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE, 404, json=error("not_found", "no such provider"))
    response = operator_client.get(EDIT)
    assert response.status_code == 404
    assert "No provider named “bank” is registered" in response.content.decode()
    assert 'action="/engine/providers/bank/replace/"' not in response.content.decode()


# --- delete -------------------------------------------------------------------------------------


def test_delete_get_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(DELETE, {"name": "evil", "next": DETAIL})
    page = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    assert "Delete provider" in page
    assert (
        "Stories bound to it keep their binding, but nothing can read or write its values until"
        " it is registered again." in page
    )
    assert "<em>bank</em>" in page
    assert "<em>evil</em>" not in page  # the emphasis is the URL's name, never the query
    assert "<dt>Provider</dt><dd><code>bank</code></dd>" in page


def test_delete_204_flash_and_redirect(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ONE, 204)
    response = operator_client.post(DELETE, {"idempotency_key": "01JM6KEY0000000000000000DD"})
    assert response.status_code == 302
    assert response["Location"] == "/engine/providers/"
    assert flashes(response) == ["Deleted bank — idempotency key 01JM6KEY0000000000000000DD"]
    (sent,) = calls(engine, "DELETE", BANK)
    assert sent.headers["Idempotency-Key"] == "01JM6KEY0000000000000000DD"
    row = EngineOperation.objects.get(kind="delete_provider")
    assert (row.entity_type, row.entity_id) == ("external_provider", "bank")


def test_delete_in_use_lists_stories(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "DELETE",
        ONE,
        409,
        json=error("provider_in_use", "in use", stories=[STORY_A, STORY_B]),
    )
    response = operator_client.post(DELETE, {"idempotency_key": "01JM6KEY0000000000000000DD"})
    page = response.content.decode()
    assert response.status_code == 409
    assert "bank was not deleted: these stories' States name it" in text(page)
    for story in (STORY_A, STORY_B):
        assert f'<a href="/stories/{story}/"><code>{story}</code></a>' in page
    assert "at most 100" not in page
    assert '<a href="/engine/providers/bank/">Back to bank</a>' in page

    # Complement: no further write; the list still shows the provider.
    engine.respond("GET", LIST, 200, json=engine_fixture("m6/providers"))
    listing = operator_client.get("/engine/providers/").content.decode()
    assert 'id="provider-bank"' in listing
    assert len(calls(engine, "DELETE")) == 1
    assert len(writes(engine)) == 1


def test_delete_in_use_at_most_100(operator_client: Client, engine: FakeEngine) -> None:
    stories = [f"01JB2Z8Y5Q3W6K9M1N4P7R{n:04d}" for n in range(100)]
    engine.respond("DELETE", ONE, 409, json=error("provider_in_use", "in use", stories=stories))
    page = operator_client.post(DELETE, {}).content.decode()
    assert "(the Engine lists at most 100)" in page


def test_delete_404_nothing_changed(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ONE, 404, json=error("not_found", "no such provider"))
    response = operator_client.post(DELETE, {})
    assert response.status_code == 302
    assert response["Location"] == "/engine/providers/"
    assert flashes(response) == [
        "bank isn't registered (it may already have been deleted) — nothing changed"
    ]


def test_invalid_name_write_urls_make_no_call(operator_client: Client, engine: FakeEngine) -> None:
    for url in ("/engine/providers/Bank/replace/", "/engine/providers/Bank/delete/"):
        assert operator_client.get(url).status_code == 404
        assert operator_client.post(url, {}).status_code == 404
    assert engine.requests == []


# --- links ----------------------------------------------------------------------------------------


def test_detail_and_list_links(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE, 200, json=provider())
    page = operator_client.get(DETAIL).content.decode()
    assert '<a class="replace-provider" href="/engine/providers/bank/replace/">' in page
    assert (
        'href="/engine/providers/bank/delete/?name=bank&amp;next=%2Fengine%2Fproviders%2Fbank%2F"'
        in page
    )

    engine.respond("GET", LIST, 503, json=error("unavailable", "down"))
    listing = operator_client.get("/engine/providers/").content.decode()
    assert 'href="/engine/provider-registration/">Register a provider</a>' in listing
