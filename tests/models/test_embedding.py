"""The ``embedding`` model port on /models/ (#59): its own row only, a confirm before a model
change (#134's confirmed mode), the probe's refusal, and no drop."""

import re
from typing import Any

import httpx
import pytest
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.models.helpers import (
    IDEMPOTENCY_KEY,
    ITEM,
    KEY,
    answer_list,
    body,
    calls,
    error,
    item,
)

pytestmark = pytest.mark.django_db

URL = "/models/embedding/edit/"
NO_CHANGE = "No change — embedding already uses these values"
REEMBED = (
    "Changing the embedding model re-embeds every story&#x27;s hot memories in the background. "
    "A model with a different vector dimension retypes the deployment&#x27;s vector column. "
    "While a story re-embeds, its searches are lexical-only."
)
CHAT_PARAMS = ("temperature", "top_p", "max_tokens", "stop", "seed")
OWN = {"base_url": "own", "model": "own", "api_key": "own", "params": {"timeout": "own"}}


def embedding(**fields: Any) -> dict[str, Any]:
    """The embedding row with values of its own (revision 5, as the list fixture)."""
    values: dict[str, Any] = {
        "base_url": "http://ollama:11434/v1",
        "model": "nomic-embed-text",
        "params": {"timeout": 30.0},
        "sources": OWN,
    }
    return item("embedding", **{**values, **fields})


def entry(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("purpose", "config_revision")}


def open_edit(client: Client, engine: FakeEngine, row: dict[str, Any] | None = None) -> str:
    """The edit page's snapshot (one GET), and the call log cleared."""
    engine.respond("GET", ITEM, 200, json=row or embedding())
    html = client.get(URL).content.decode()
    match = re.search(r'name="snapshot" value="([^"]+)"', html)
    assert match
    engine.reset()
    return match.group(1)


def form(snapshot: str, **fields: str) -> dict[str, str]:
    values = {"base_url": "http://ollama:11434/v1", "model": "nomic-embed-text", "timeout": "30"}
    return {**values, "idempotency_key": IDEMPOTENCY_KEY, "snapshot": snapshot, **fields}


def saved(revision: int = 6, **fields: Any) -> dict[str, Any]:
    return {**embedding(**fields), "config_revision": revision, "idempotency_key": "01SAVED"}


def answer_put(engine: FakeEngine, revision: int = 6, **fields: Any) -> None:
    engine.respond("PUT", ITEM, 200, json=saved(revision, **fields))


def hidden(html: str) -> dict[str, str]:
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, m.message) for m in response.context["messages"]]


def non_field(html: str) -> list[str]:
    block = re.search(r'<ul class="errorlist nonfield">(.*?)</ul>', html, re.S)
    return re.findall(r"<li>(.*?)</li>", block.group(1), re.S) if block else []


# --- List and edit page ----------------------------------------------------------------------


def test_the_list_row_has_edit_no_drop_and_no_markers(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine, embedding=entry(embedding()))
    html = operator_client.get("/models/").content.decode()
    row = re.search(r'<tr id="purpose-embedding">(.*?)</tr>', html, re.S)
    assert row
    assert '<a class="button" href="/models/embedding/edit/">Edit</a>' in row.group(1)
    assert "Drop override" not in row.group(1)
    assert 'class="source"' not in row.group(1)
    for name in CHAT_PARAMS:
        assert re.search(rf'<td class="param-{name}">n/a</td>', row.group(1)), name
    assert re.search(r'<td class="param-timeout">30.0\s*</td>', row.group(1))


def test_the_edit_page_has_only_the_port_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=embedding())
    html = operator_client.get(URL).content.decode()
    assert (
        "The embedding port uses only its own settings: a blank field is unset, never taken "
        "from default." in html
    )
    assert "Drop override" not in html
    assert "/drop/" not in html
    assert "placeholder=" not in html
    for name in CHAT_PARAMS:
        assert f'name="{name}"' not in html
    assert re.search(r'name="model"[^>]*value="nomic-embed-text"', html)
    assert re.search(r'name="base_url"[^>]*value="http://ollama:11434/v1"', html)
    assert re.search(r'name="timeout"[^>]*value="30.0"', html)
    assert "A model-change probe must answer within the Console&#x27;s 30 s wait." in html
    assert re.search(r'<input[^>]*name="api_key"', html)  # the key form stays


def test_the_drop_url_404s_with_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    for method in ("get", "post"):
        response = getattr(operator_client, method)("/models/embedding/drop/")
        assert response.status_code == 404
    assert engine.requests == []


# --- Validation and the no-change check ------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model", "", "This field is required."),
        ("model", "   ", "This field is required."),
        ("timeout", "0", "Ensure this value is greater than 0."),
        ("timeout", "601", "Ensure this value is less than or equal to 600.0."),
        ("base_url", "ftp://x", "Enter a URL"),
    ],
)
def test_invalid_input_renders_the_edit_page_with_zero_calls(
    operator_client: Client, engine: FakeEngine, field: str, value: str, message: str
) -> None:
    snapshot = open_edit(operator_client, engine)
    response = operator_client.post(URL, form(snapshot, **{"model": "other-model", field: value}))
    assert response.status_code == 200
    assert message in response.content.decode()
    assert engine.requests == []


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"base_url": "http://ollama:11434/v1/"},
        {"base_url": "  http://ollama:11434/v1  ", "timeout": "30.0"},
        {"model": "  nomic-embed-text  "},
    ],
)
def test_identical_values_are_no_change_with_zero_calls(
    operator_client: Client, engine: FakeEngine, fields: dict[str, str]
) -> None:
    snapshot = open_edit(operator_client, engine)
    answer_list(engine)
    response = operator_client.post(URL, form(snapshot, **fields), follow=True)
    assert response.redirect_chain == [("/models/", 302)]
    assert flashes(response) == [("info", NO_CHANGE)]
    assert calls(engine, "PUT") == []
    assert EngineOperation.objects.count() == 0


def test_an_unset_row_with_blank_values_is_no_change_except_the_model(
    operator_client: Client, engine: FakeEngine
) -> None:
    """The fixture's row sets nothing; a model is required, so naming one is a change."""
    snapshot = open_edit(operator_client, engine, item("embedding"))
    response = operator_client.post(
        URL, form(snapshot, base_url="", timeout="", model="nomic-embed-text")
    )
    assert "Change the embedding model" in response.content.decode()
    assert engine.requests == []


# --- Model change: the confirm page ----------------------------------------------------------


def test_a_model_change_renders_the_confirm_page_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    response = operator_client.post(
        URL, form(snapshot, model="mxbai-embed-large", base_url="", timeout="45")
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    assert REEMBED in html
    assert "<dt>Model</dt><dd><code>nomic-embed-text → mxbai-embed-large</code></dd>" in html
    assert "<dt>Base URL</dt><dd><code>http://ollama:11434/v1 → — (not set)</code></dd>" in html
    assert "<dt>timeout (s)</dt><dd><code>30.0 → 45.0</code></dd>" in html
    carried = hidden(html)
    assert carried["confirmed"] == "1"
    assert carried["idempotency_key"] == IDEMPOTENCY_KEY  # the edit page's key, carried
    assert carried["model"] == "mxbai-embed-large"
    assert carried["base_url"] == ""
    assert carried["timeout"] == "45"
    assert carried["snapshot"]
    assert '<a href="/models/embedding/edit/">Cancel</a>' in html
    assert '<form method="post">' in html  # posts back to the edit URL itself


def test_a_null_old_value_renders_not_set(operator_client: Client, engine: FakeEngine) -> None:
    snapshot = open_edit(operator_client, engine, item("embedding"))
    html = operator_client.post(URL, form(snapshot)).content.decode()
    assert "<dt>Model</dt><dd><code>— (not set) → nomic-embed-text</code></dd>" in html


def test_a_model_change_without_confirmed_always_re_renders_the_confirm(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(URL, form(snapshot, model="mxbai-embed-large"))
    again = operator_client.post(URL, {**hidden(first.content.decode()), "confirmed": "0"})
    assert "Change the embedding model" in again.content.decode()
    assert engine.requests == []


def test_the_confirmed_post_sends_the_carried_key_and_the_full_replacement(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(
        URL, form(snapshot, model="mxbai-embed-large", base_url="", timeout="")
    )
    answer_put(engine, model="mxbai-embed-large")
    answer_list(engine)
    response = operator_client.post(
        URL, {**hidden(first.content.decode()), "temperature": "0.5"}, follow=True
    )
    (put,) = calls(engine, "PUT")
    assert put.url.path == "/admin/model-config/embedding"
    assert put.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert body(put) == {"base_url": None, "model": "mxbai-embed-large", "params": None}
    assert put.extensions["timeout"]["read"] == 30.0  # the probe runs inside the PUT
    assert response.redirect_chain == [("/models/", 302)]
    assert flashes(response) == [
        ("success", "Embedding model changed — re-embedding started (config revision 6)")
    ]
    assert calls(engine, "GET", ITEM) == []  # no fresh read before the PUT


def test_the_confirm_post_re_validates_its_hidden_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(URL, form(snapshot, model="mxbai-embed-large"))
    response = operator_client.post(URL, {**hidden(first.content.decode()), "timeout": "-1"})
    assert "Ensure this value is greater than 0." in response.content.decode()
    assert engine.requests == []


def test_a_kept_model_puts_directly(operator_client: Client, engine: FakeEngine) -> None:
    snapshot = open_edit(operator_client, engine)
    answer_put(engine, base_url="http://embedder:8080/v1")
    answer_list(engine)
    response = operator_client.post(
        URL, form(snapshot, base_url="http://embedder:8080/v1", temperature="0.5"), follow=True
    )
    (put,) = calls(engine, "PUT")
    assert put.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert body(put) == {
        "base_url": "http://embedder:8080/v1",
        "model": "nomic-embed-text",
        "params": {"timeout": 30.0},
    }
    assert flashes(response) == [
        ("success", "Saved embedding — config revision 6, idempotency key 01SAVED")
    ]


def test_a_put_answering_the_snapshots_revision_is_no_change(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(URL, form(snapshot, model="mxbai-embed-large"))
    answer_put(engine, revision=5)
    answer_list(engine)
    response = operator_client.post(URL, hidden(first.content.decode()), follow=True)
    assert len(calls(engine, "PUT")) == 1
    assert flashes(response) == [("info", NO_CHANGE)]


# --- Engine refusals -------------------------------------------------------------------------


def unreachable(**details: Any) -> dict[str, Any]:
    return error(
        "embedding_unreachable",
        "the embedding endpoint did not answer the probe; the model was not changed",
        **details,
    )


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        ({"field": "body.model", "purpose": "embedding", "reason": "connection"}, "connection"),
        ({"field": "body.model", "purpose": "embedding"}, "unknown"),
    ],
)
def test_embedding_unreachable_is_only_a_form_error(
    operator_client: Client, engine: FakeEngine, details: dict[str, Any], reason: str
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(URL, form(snapshot, model="nope-embed", base_url="http://x:1"))
    engine.respond("PUT", ITEM, 422, json=unreachable(**details))
    response = operator_client.post(URL, hidden(first.content.decode()))
    html = response.content.decode()
    assert response.status_code == 200
    assert non_field(html) == [
        f"The Engine couldn&#x27;t reach that embedding model ({reason}). The model was not "
        "changed."
    ]
    assert html.count('class="errorlist') == 1  # no message on the model field
    assert "The Engine rejected the configuration" not in html
    assert re.search(r'name="model"[^>]*value="nope-embed"', html)
    assert re.search(r'name="base_url"[^>]*value="http://x:1"', html)
    assert not list(response.context["messages"])


def test_another_422_is_placed_on_its_field(operator_client: Client, engine: FakeEngine) -> None:
    snapshot = open_edit(operator_client, engine)
    engine.respond(
        "PUT",
        ITEM,
        422,
        json=error("validation_error", "timeout too long", field="body.params.timeout"),
    )
    html = operator_client.post(URL, form(snapshot, timeout="500")).content.decode()
    assert re.search(r'name="timeout".*?timeout too long', html, re.S)


def test_a_not_a_port_param_422_shows_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    engine.respond(
        "PUT",
        ITEM,
        422,
        json=error(
            "validation_error",
            "embedding takes no chat parameters: temperature",
            field="body.params.temperature",
            reason="not_a_port_param",
        ),
    )
    html = operator_client.post(URL, form(snapshot, timeout="20")).content.decode()
    assert any("embedding takes no chat parameters" in e for e in non_field(html))


@pytest.mark.parametrize(
    ("status", "code"), [(409, "config_conflict"), (503, "config_key_missing")]
)
def test_other_errors_show_the_panel_with_the_posted_values(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    snapshot = open_edit(operator_client, engine)
    engine.respond("PUT", ITEM, status, json=error(code, "refused"))
    html = operator_client.post(URL, form(snapshot, timeout="20")).content.decode()
    assert f"<code>{code}</code>" in html
    assert re.search(r'name="timeout"[^>]*value="20"', html)
    assert "may still apply" not in html
    key = re.search(r'name="idempotency_key" value="([^"]+)"', html)
    assert key
    assert key.group(1) != IDEMPOTENCY_KEY  # a fresh key for the next attempt
    assert calls(engine, "GET") == []


def test_a_console_timeout_warns_the_change_may_still_apply(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(URL, form(snapshot, model="mxbai-embed-large"))
    engine.fail("PUT", ITEM, httpx.ReadTimeout("slow"))
    html = operator_client.post(URL, hidden(first.content.decode())).content.decode()
    assert (
        "The Engine may still apply this change — check Models and the re-embed run before "
        "saving again." in html
    )
    assert re.search(r'name="model"[^>]*value="mxbai-embed-large"', html)


# --- API key and recording -------------------------------------------------------------------


def test_clearing_the_key_names_the_port_rule(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get("/models/embedding/api-key/clear/").content.decode()
    assert (
        "The embedding model will then be called without a key: the port never uses "
        "default&#x27;s key." in html
    )
    assert engine.requests == []


def test_every_write_is_recorded_with_no_entity(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    engine.respond("PUT", KEY, 204)
    engine.respond("DELETE", KEY, 204)
    answer_put(engine, base_url="http://embedder:8080/v1")
    operator_client.post(URL, form(snapshot, base_url="http://embedder:8080/v1"))
    operator_client.post(
        "/models/embedding/api-key/",
        {"api_key": "sk-embed", "idempotency_key": IDEMPOTENCY_KEY, "snapshot": snapshot},
    )
    operator_client.post("/models/embedding/api-key/clear/", {"idempotency_key": IDEMPOTENCY_KEY})
    rows = list(EngineOperation.objects.order_by("pk"))
    assert [r.kind for r in rows] == [
        "put_model_config",
        "put_model_api_key",
        "delete_model_api_key",
    ]
    assert all(r.entity_type is None and r.entity_id is None for r in rows)
