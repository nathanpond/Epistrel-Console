"""A purpose's API key (#46): sent once, in the api-key PUT, and never shown or stored again."""

import json
import re
from urllib.parse import urlencode

import pytest
from django.forms.models import model_to_dict
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.models.helpers import IDEMPOTENCY_KEY, ITEM, KEY, answer_list, body, calls, error, item

pytestmark = pytest.mark.django_db

ENTERED = "sk-test-0123456789-do-not-echo"
URL = "/models/generation/api-key/"
KEY_MISSING = "The Engine has no <code>EPISTREL_CONFIG_KEY</code> — it cannot store API keys"


def key_form(**fields: str) -> dict[str, str]:
    return {"api_key": ENTERED, "idempotency_key": IDEMPOTENCY_KEY, **fields}


def test_edit_page_has_a_password_input_with_autocomplete_off(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    html = operator_client.get("/models/generation/edit/").content.decode()
    tag = re.search(r'<input[^>]*name="api_key"[^>]*>', html)
    assert tag
    assert 'type="password"' in tag.group(0)
    assert 'autocomplete="off"' in tag.group(0)
    assert "set (fingerprint <code>082f20f3</code>)" in html
    assert "/models/generation/api-key/clear/?next=/models/generation/edit/" in html


def test_clear_key_shows_only_with_a_fingerprint(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("quality_judge"))
    html = operator_client.get("/models/quality_judge/edit/").content.decode()
    assert "not set" in html
    assert "/api-key/clear/" not in html


def test_the_key_goes_only_in_the_api_key_put(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond("PUT", KEY, 204)
    answer_list(engine)
    response = operator_client.post(URL, key_form(), follow=True)
    puts = calls(engine, "PUT")
    assert len(puts) == 1
    assert puts[0].url.path == "/admin/model-config/generation/api-key"
    assert body(puts[0]) == {"api_key": ENTERED}
    assert puts[0].headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    others = [r for r in engine.requests if r is not puts[0]]
    assert others
    assert all(ENTERED not in (r.content.decode() + str(r.url)) for r in others)
    assert "Key set for generation" in response.content.decode()


def test_the_key_is_in_no_page_redirect_or_log_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond("PUT", KEY, 204)
    answer_list(engine)
    response = operator_client.post(URL, key_form())
    assert response.status_code == 302
    assert ENTERED not in response["Location"]
    assert ENTERED not in response.content.decode()
    page = operator_client.get(response["Location"])
    assert ENTERED not in page.content.decode()
    row = EngineOperation.objects.get()
    assert row.kind == "put_model_api_key"
    assert row.request["body"] == {"api_key": "***"}
    assert ENTERED not in json.dumps(model_to_dict(row), default=str)
    detail = operator_client.get(f"/operations/{row.pk}/").content.decode()
    assert ENTERED not in detail


def test_a_refused_key_is_not_rendered_back(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond(
        "PUT",
        KEY,
        422,
        json=error(
            "validation_error",
            "too long",
            field="body.api_key",
            errors=[{"field": "body.api_key", "message": "Key refused", "type": "x"}],
        ),
    )
    response = operator_client.post(URL, key_form())
    html = response.content.decode()
    assert response.status_code == 200
    assert "Key refused" in html
    assert ENTERED not in html
    assert ENTERED not in json.dumps(
        [model_to_dict(r) for r in EngineOperation.objects.all()], default=str
    )


def test_a_blank_key_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    page = operator_client.get("/models/generation/edit/").content.decode()
    snapshot = re.search(r'name="snapshot" value="([^"]+)"', page)
    assert snapshot
    engine.reset()
    response = operator_client.post(URL, key_form(api_key="   ", snapshot=snapshot.group(1)))
    assert response.status_code == 200
    assert engine.requests == []
    assert "This field is required." in response.content.decode()


def test_config_key_missing_shows_the_message(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond("PUT", KEY, 503, json=error("config_key_missing", "no config key"))
    response = operator_client.post(URL, key_form())
    html = response.content.decode()
    assert response.status_code == 200
    assert KEY_MISSING in html
    assert html.index(KEY_MISSING) < html.index('class="api-key-form"')  # above the key form
    assert ENTERED not in html


def test_another_key_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond("PUT", KEY, 409, json=error("idempotency_conflict", "key reused"))
    html = operator_client.post(URL, key_form()).content.decode()
    assert "idempotency_conflict" in html
    assert KEY_MISSING not in html


def test_get_on_the_key_url_is_not_allowed(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.get(URL).status_code == 405
    assert engine.requests == []


# --- Clear key ---------------------------------------------------------------------------------


def clear_url(purpose: str = "generation") -> str:
    return f"/models/{purpose}/api-key/clear/?{urlencode({'next': f'/models/{purpose}/edit/'})}"


def test_clear_confirm_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(clear_url()).content.decode()
    assert engine.requests == []
    assert (
        "Clear the API key for <em>generation</em>? Calls will use default&#x27;s key, or fail "
        "if it has none." in html
    )
    assert '<a href="/models/generation/edit/">Cancel</a>' in html


def test_clear_confirm_names_the_parent(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(clear_url("regeneration")).content.decode()
    assert "Calls will use generation&#x27;s key" in html


def test_clear_post_sends_delete(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", KEY, 204)
    answer_list(engine)
    response = operator_client.post(
        "/models/generation/api-key/clear/", {"idempotency_key": IDEMPOTENCY_KEY}, follow=True
    )
    (delete,) = calls(engine, "DELETE")
    assert delete.url.path == "/admin/model-config/generation/api-key"
    assert response.redirect_chain == [("/models/", 302)]
    assert "Key cleared for generation" in response.content.decode()
    assert EngineOperation.objects.get().kind == "delete_model_api_key"


def test_clear_config_key_missing_shows_the_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    schema_codes = engine.schema["paths"]["/admin/model-config/{purpose}/api-key"]["delete"]
    assert "503" in schema_codes["responses"]
    engine.respond("DELETE", KEY, 503, json=error("config_key_missing", "no config key"))
    engine.respond("GET", ITEM, 200, json=item("generation"))
    response = operator_client.post(
        "/models/generation/api-key/clear/", {"idempotency_key": IDEMPOTENCY_KEY}
    )
    assert response.status_code == 200
    assert KEY_MISSING in response.content.decode()
