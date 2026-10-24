"""/models/ (#46): every purpose with its sources, editing one (PUT), dropping its override."""

import re
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.models.helpers import (
    IDEMPOTENCY_KEY,
    ITEM,
    LIST,
    answer_list,
    body,
    calls,
    error,
    item,
)

pytestmark = pytest.mark.django_db


def row(html: str, purpose: str) -> str:
    match = re.search(rf'<tr id="purpose-{purpose}">(.*?)</tr>', html, re.S)
    assert match, f"no row for {purpose}"
    return match.group(1)


def cells(html: str, purpose: str) -> list[str]:
    found = re.findall(r"<td[^>]*>(.*?)</td>", row(html, purpose), re.S)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip() for cell in found]


# Column order: purpose, base URL, model, the six params, other params, API key, actions.
BASE_URL, MODEL, TEMPERATURE, TOP_P, MAX_TOKENS, TIMEOUT, STOP, SEED, OTHER, API_KEY = range(1, 11)


# --- List -----------------------------------------------------------------------------------


def test_list_shows_every_purpose_default_first(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed = answer_list(engine)
    html = operator_client.get("/models/").content.decode()
    order = re.findall(r'<tr id="purpose-([a-z_]+)">', html)
    assert order[0] == "default"
    assert order == list(listed["purposes"])  # the fixture lists default first already
    assert "Config revision <strong>5</strong>" in html
    assert (
        "A story's engine profile may route a purpose to a different model; see the story's "
        "Config (#85)." in html
    )


def test_list_renders_own_inherited_and_default_markers(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    html = operator_client.get("/models/").content.decode()
    consolidation = cells(html, "consolidation")
    assert consolidation[MODEL] == "m-cons own"
    assert consolidation[MAX_TOKENS] == "100 own"
    assert consolidation[TEMPERATURE] == "0.7 default"
    assert consolidation[BASE_URL] == "http://host.docker.internal:11434/v1 default"
    lite = cells(html, "lite_summary")
    assert lite[MODEL] == "m-cons inherited from consolidation"
    assert lite[SEED] == "5 inherited from consolidation"
    assert lite[STOP] == "a default"
    # A param no row sets: no value, from default.
    assert lite[TOP_P] == "— (not set) default"
    assert cells(html, "regeneration")[API_KEY] == "set (fingerprint 082f20f3)"
    assert cells(html, "quality_judge")[API_KEY] == "not set"


def test_inherited_marker_names_the_parent_from_the_engines_sources(
    operator_client: Client, engine: FakeEngine
) -> None:
    rating = item("rating_suggestion")
    rating["model"] = "guard-1"
    rating["sources"] = {**rating["sources"], "model": "inherited"}
    entry = {k: v for k, v in rating.items() if k not in ("purpose", "config_revision")}
    answer_list(engine, rating_suggestion=entry)
    html = operator_client.get("/models/").content.decode()
    assert cells(html, "rating_suggestion")[MODEL] == "guard-1 inherited from guardrail_check"


def test_default_row_has_no_markers_and_no_drop_button(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    html = operator_client.get("/models/").content.decode()
    default = cells(html, "default")
    assert default[MODEL] == "llama3.1"
    assert default[TEMPERATURE] == "0.7"
    assert default[TOP_P] == "— (not set)"
    assert 'class="source"' not in row(html, "default")
    assert "/models/default/edit/" in row(html, "default")
    assert "/models/default/drop/" not in html
    assert "/models/generation/drop/?next=/models/" in row(html, "generation")


def test_seed_renders_on_the_list(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine)
    html = operator_client.get("/models/").content.decode()
    assert cells(html, "consolidation")[SEED] == "5 own"


def test_other_params_are_listed_read_only(operator_client: Client, engine: FakeEngine) -> None:
    entry = item("generation")
    entry["params"] = {**entry["params"], "presence_penalty": 0.5}
    entry["sources"]["params"]["presence_penalty"] = "default"
    del entry["purpose"], entry["config_revision"]
    answer_list(engine, generation=entry)
    html = operator_client.get("/models/").content.decode()
    assert cells(html, "generation")[OTHER] == "presence_penalty: 0.5"
    assert cells(html, "quality_judge")[OTHER] == "—"


# --- embedding: read-only --------------------------------------------------------------------


def test_embedding_row_is_read_only(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine)
    html = operator_client.get("/models/").content.decode()
    embedding = row(html, "embedding")
    assert "Configured in M4 (#59)" in embedding
    assert "/models/embedding/" not in html  # no edit, drop or key link anywhere
    assert "<a" not in embedding


EMBEDDING_URLS = [
    "/models/embedding/edit/",
    "/models/embedding/drop/",
    "/models/embedding/api-key/",
    "/models/embedding/api-key/clear/",
]


@pytest.mark.parametrize("url", EMBEDDING_URLS)
@pytest.mark.parametrize("method", ["get", "post"])
def test_embedding_action_urls_answer_404_without_calling_the_engine(
    operator_client: Client, engine: FakeEngine, url: str, method: str
) -> None:
    data = {"model": "forced", "api_key": "sk-forced", "idempotency_key": IDEMPOTENCY_KEY}
    response = getattr(operator_client, method)(url, data if method == "post" else None)
    assert response.status_code == 404
    assert engine.requests == []
    assert EngineOperation.objects.count() == 0


def test_other_purposes_edit_url_works(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: the same URL for an editable purpose reads it from the Engine."""
    engine.respond("GET", ITEM, 200, json=item("generation"))
    response = operator_client.get("/models/generation/edit/")
    assert response.status_code == 200
    assert [r.url.path for r in engine.requests] == ["/admin/model-config/generation"]


# --- Edit ------------------------------------------------------------------------------------


def test_edit_prefills_own_values_and_shows_inherited_as_placeholders(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=engine_fixture("get_model_config"))  # consolidation
    html = operator_client.get("/models/consolidation/edit/").content.decode()
    assert re.search(r'name="model"[^>]*value="m-cons"', html)
    assert re.search(r'name="max_tokens"[^>]*value="100"', html)
    assert re.search(r'name="seed"[^>]*value="5"', html)
    base_url = re.search(r"<input[^>]*name=\"base_url\"[^>]*>", html)
    assert base_url is not None
    assert "value=" not in base_url.group(0)
    assert 'placeholder="default: http://host.docker.internal:11434/v1"' in base_url.group(0)
    assert 'placeholder="default: 0.7"' in html  # temperature
    assert 'placeholder="default: — (not set)"' in html  # top_p, set nowhere
    assert "config revision <strong>5</strong>" in html
    assert "/models/consolidation/drop/" in html


def test_edit_of_a_child_purpose_names_its_parent(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("lite_summary"))
    html = operator_client.get("/models/lite_summary/edit/").content.decode()
    assert 'placeholder="consolidation: m-cons"' in html
    assert (
        "A blank field inherits from <code>consolidation</code>, then <code>default</code>" in html
    )


def test_default_edit_prefills_its_values_and_has_no_drop_button(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("default"))
    html = operator_client.get("/models/default/edit/").content.decode()
    assert re.search(r'name="model"[^>]*value="llama3.1"', html)
    assert re.search(r'name="base_url"[^>]*value="http://host.docker.internal:11434/v1"', html)
    assert re.search(r'name="stop"[^>]*>\s*a</textarea>', html)
    assert "placeholder=" not in html
    assert "/drop/" not in html


def edit_form(**fields: str) -> dict[str, str]:
    blank = dict.fromkeys(
        ("base_url", "model", "temperature", "top_p", "max_tokens", "timeout", "stop", "seed"), ""
    )
    return {**blank, "idempotency_key": IDEMPOTENCY_KEY, **fields}


def put_answer(engine: FakeEngine) -> dict[str, Any]:
    saved: dict[str, Any] = engine_fixture("put_model_config")
    engine.respond("PUT", ITEM, 200, json=saved)
    return saved


def test_put_sends_the_full_replacement_with_blank_fields_absent(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("guardrail_check"))
    page = operator_client.get("/models/guardrail_check/edit/").content.decode()
    snapshot = re.search(r'name="snapshot" value="([^"]+)"', page)
    assert snapshot
    engine.reset()
    put_answer(engine)
    response = operator_client.post(
        "/models/guardrail_check/edit/",
        edit_form(model=" guard-1 ", seed="42", timeout="30", snapshot=snapshot.group(1)),
    )
    assert response.status_code == 302
    assert response["Location"] == "/models/"
    (put,) = engine.requests  # the snapshot spares a re-read
    assert put.method == "PUT"
    assert put.url.path == "/admin/model-config/guardrail_check"
    assert put.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    sent = body(put)
    assert sent == {"model": "guard-1", "params": {"timeout": 30.0, "seed": 42}}
    assert type(sent["params"]["seed"]) is int  # an integer, not 42.0
    row = EngineOperation.objects.get()
    assert row.kind == "put_model_config"
    assert row.config_revision == 6


def test_put_with_every_field_blank_sends_an_empty_body(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("quality_judge"))
    put_answer(engine)
    operator_client.post("/models/quality_judge/edit/", edit_form())
    (put,) = calls(engine, "PUT")
    assert body(put) == {}


def test_default_put_sends_blank_fields_as_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("default"))
    put_answer(engine)
    operator_client.post(
        "/models/default/edit/",
        edit_form(
            base_url="http://host.docker.internal:11434/v1",
            model="llama3.1",
            stop=" END \n\n  STOP  \n",
            temperature="0.8",
        ),
    )
    (put,) = calls(engine, "PUT")
    assert body(put) == {
        "base_url": "http://host.docker.internal:11434/v1",
        "model": "llama3.1",
        "params": {
            "temperature": 0.8,
            "top_p": None,
            "max_tokens": None,
            "timeout": None,
            "stop": ["END", "STOP"],
            "seed": None,
        },
    }


def test_put_flashes_the_config_revision(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("guardrail_check"))
    saved = put_answer(engine)
    answer_list(engine)
    response = operator_client.post(
        "/models/guardrail_check/edit/", edit_form(model="guard-1"), follow=True
    )
    html = response.content.decode()
    assert (
        f"Saved guardrail_check — config revision 6, idempotency key {saved['idempotency_key']}"
        in html
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", "2.5"),
        ("top_p", "-0.1"),
        ("max_tokens", "0"),
        ("max_tokens", "4001"),
        ("timeout", "0"),
        ("timeout", "601"),
        ("seed", "-1"),
        ("seed", "2147483648"),
        ("seed", "1.5"),
        ("stop", "a\nb\nc\nd\ne"),
        ("base_url", "ftp://models.example"),
        ("base_url", "not a url"),
        ("model", "m" * 201),
    ],
)
def test_input_the_schema_refuses_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, field: str, value: str
) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    page = operator_client.get("/models/generation/edit/").content.decode()
    snapshot = re.search(r'name="snapshot" value="([^"]+)"', page)
    assert snapshot
    engine.reset()
    response = operator_client.post(
        "/models/generation/edit/", edit_form(**{field: value, "snapshot": snapshot.group(1)})
    )
    assert response.status_code == 200
    assert engine.requests == []
    assert 'class="errorlist' in response.content.decode()


def test_single_label_host_is_accepted(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    put_answer(engine)
    operator_client.post("/models/generation/edit/", edit_form(base_url="http://ollama:11434/v1"))
    (put,) = calls(engine, "PUT")
    assert body(put) == {"base_url": "http://ollama:11434/v1"}


def test_a_422_places_field_errors(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond(
        "PUT",
        ITEM,
        422,
        json=error(
            "validation_error",
            "Input should be less than or equal to 2",
            field="body.params.temperature",
            errors=[
                {"field": "body.params.temperature", "message": "Too hot", "type": "x"},
                {"field": "body.params.stop[1]", "message": "Bad stop", "type": "x"},
            ],
        ),
    )
    response = operator_client.post(
        "/models/generation/edit/", edit_form(temperature="1.5", stop="a\nb")
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert re.search(r'name="temperature".*?Too hot', html, re.S)
    assert re.search(r'name="stop".*?Bad stop', html, re.S)
    assert "The Engine rejected the configuration: Input should be less than or equal to 2" in html
    # The re-render keeps the operator's input.
    assert re.search(r'name="temperature"[^>]*value="1.5"', html)


def test_another_engine_error_shows_the_panel_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    engine.respond("PUT", ITEM, 409, json=error("config_conflict", "changed meanwhile"))
    response = operator_client.post("/models/generation/edit/", edit_form(model="x"))
    html = response.content.decode()
    assert response.status_code == 200
    assert "config_conflict" in html
    assert "changed meanwhile" in html
    assert re.search(r'name="model"[^>]*value="x"', html)


def test_a_tampered_snapshot_is_re_read(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=item("generation"))
    operator_client.post(
        "/models/generation/edit/", edit_form(temperature="9", snapshot="tampered")
    )
    assert [r.method for r in engine.requests] == ["GET"]


# --- Drop override ---------------------------------------------------------------------------


def drop_url(purpose: str = "guardrail_check") -> str:
    return f"/models/{purpose}/drop/?{urlencode({'next': '/models/'})}"


def test_drop_confirm_get_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(drop_url()).content.decode()
    assert engine.requests == []
    assert (
        "Drop <em>guardrail_check</em>&#x27;s override? Every field falls back to default." in html
    )
    assert "Its own API key is also removed if the Engine stores it per purpose." in html
    assert '<a href="/models/">Cancel</a>' in html
    assert 'name="idempotency_key" value="' in html


def test_drop_confirm_names_the_parent(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(drop_url("lite_summary")).content.decode()
    assert (
        "Drop <em>lite_summary</em>&#x27;s override? Every field falls back to its parent "
        "purpose (consolidation) or default." in html
    )


def test_drop_post_sends_delete_and_flashes_the_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    dropped = engine_fixture("delete_model_config")
    engine.respond("DELETE", ITEM, 200, json=dropped)
    answer_list(engine)
    response = operator_client.post(
        "/models/guardrail_check/drop/", {"idempotency_key": IDEMPOTENCY_KEY}, follow=True
    )
    html = response.content.decode()
    (delete,) = calls(engine, "DELETE")
    assert delete.url.path == "/admin/model-config/guardrail_check"
    assert delete.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert response.redirect_chain == [("/models/", 302)]
    assert (
        f"guardrail_check now uses default — config revision 7, idempotency key "
        f"{dropped['idempotency_key']}" in html
    )
    # The page shows the re-read state.
    assert calls(engine, "GET", LIST)
    assert EngineOperation.objects.get().kind == "delete_model_config"


def test_drop_with_nothing_to_drop_flashes_so(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ITEM, 204)
    answer_list(engine)
    response = operator_client.post(
        "/models/quality_judge/drop/", {"idempotency_key": IDEMPOTENCY_KEY}, follow=True
    )
    assert "Nothing to drop — quality_judge has no override" in response.content.decode()


def test_forced_drop_of_default_shows_the_engines_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "DELETE",
        ITEM,
        409,
        json=error("validation_error", "the default model configuration cannot be removed"),
    )
    response = operator_client.post("/models/default/drop/", {"idempotency_key": IDEMPOTENCY_KEY})
    html = response.content.decode()
    assert response.status_code == 200
    assert "the default model configuration cannot be removed" in html
