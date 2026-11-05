"""#247: a story's Providers page and bind form, against the schema-validating fake Engine."""

import copy
import html as html_lib
import json
import re
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client

from console.engine.models import ProviderBinding
from console.forms.bindings import ID_TOO_LONG, BindingForm, clean_bound_id
from tests.engine_panel.helpers import form_data
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import error_json

STORY = "01JM6ST0000000000000000001"
STORY_PATH = "/stories/{story_id}"
BINDINGS = "/stories/{story_id}/providers"
REGISTRY = "/admin/external-providers"
ONE = "/admin/external-providers/{name}"
PUT = "/stories/{story_id}/providers/{name}/binding"
PAGE = f"/stories/{STORY}/providers/"
BANK_FORM = f"/stories/{STORY}/providers/bank/bind/"
SHOP_FORM = f"/stories/{STORY}/providers/shop/bind/"
BANK_PUT = f"/stories/{STORY}/providers/bank/binding"
SHOP_PUT = f"/stories/{STORY}/providers/shop/binding"
NEEDS = "The Engine doesn't report which providers this story needs."


# --- helpers ------------------------------------------------------------------------------------


def text(fragment: str) -> str:
    return html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment))).strip()


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def row(page: str, name: str) -> str:
    found = re.search(rf'<tr id="binding-{re.escape(name)}">(.*?)</tr>', page, re.S)
    assert found is not None, name
    return found.group(1)


def cell(fragment: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}"[^>]*>(.*?)</td>', fragment, re.S)
    assert found is not None, cls
    return found.group(1)


def key_in(page: str) -> str:
    found = re.search(r'name="idempotency_key" value="([^"]+)"', page)
    assert found is not None
    return found.group(1)


def requests(engine: FakeEngine, method: str | None = None) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in engine.requests if method is None or r.method == method]


def puts(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "PUT"]


def body(request: Any) -> Any:
    return json.loads(request.content)


def bank_provider(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    answer: dict[str, Any] = copy.deepcopy(engine_fixture("m6/provider"))
    if manifest is not None:
        answer["manifest"] = manifest
    return answer


def shop_provider() -> dict[str, Any]:
    (shop,) = [
        copy.deepcopy(p) for p in engine_fixture("m6/providers")["providers"] if p["name"] == "shop"
    ]
    shop["config_revision"] = 4
    return dict(shop)


def registry(*, region: bool = False) -> dict[str, Any]:
    answer: dict[str, Any] = copy.deepcopy(engine_fixture("m6/providers"))
    if region:
        read = answer["providers"][0]["manifest"]["values"]["balance"]["read"]
        read["headers"]["X-Region"] = "{binding.region}"
    return answer


def answer_page(
    engine: FakeEngine,
    *,
    bindings: dict[str, Any] | None = None,
    providers: dict[str, Any] | None = None,
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("m6/story"))
    engine.respond("GET", BINDINGS, 200, json=bindings or engine_fixture("m6/bindings"))
    engine.respond("GET", REGISTRY, 200, json=providers or registry())


def answer_form(
    engine: FakeEngine,
    *,
    bindings: dict[str, Any] | None = None,
    provider: dict[str, Any] | None = None,
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("m6/story"))
    engine.respond("GET", BINDINGS, 200, json=bindings or engine_fixture("m6/bindings"))
    engine.respond("GET", ONE, 200, json=provider or bank_provider())


def written(**changes: Any) -> dict[str, Any]:
    answer: dict[str, Any] = copy.deepcopy(engine_fixture("m6/binding_written"))
    answer.update(changes)
    return answer


def no_bindings() -> dict[str, Any]:
    return {"bindings": []}


def bind_form(page: str) -> str:
    found = re.search(
        r'<form method="post" action="[^"]*/bind/" class="binding-form">.*?</form>', page, re.S
    )
    return found.group(0) if found else ""


def get_form(client: Client, url: str = BANK_FORM) -> tuple[str, dict[str, str]]:
    page = client.get(url).content.decode()
    return page, form_data(page, url)


# --- the Providers page -------------------------------------------------------------------------


def test_page_reads_in_order(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    response = operator_client.get(PAGE)
    page = response.content.decode()

    assert response.status_code == 200
    assert requests(engine) == [
        ("GET", f"/stories/{STORY}"),
        ("GET", f"/stories/{STORY}/providers"),
        ("GET", REGISTRY),
    ]
    assert requests(engine, "PUT") == []  # complement: no write
    assert "<title>Harbour Ledger — Providers" in page
    assert "Harbour Ledger — Providers</h1>" in page
    line = text(page.split("Harbour Ledger — Providers</h1>")[1].split("</p>")[0])
    assert line == f"Play · History · Settings · Config · All stories · {STORY}"
    assert "1 binding" in page


def test_rows_for_registered_and_orphan_bindings(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, bindings=engine_fixture("m6/bindings_orphan"))
    page = operator_client.get(PAGE).content.decode()

    order = re.findall(r'<tr id="binding-([^"]+)">', page)
    registered = [p["name"] for p in engine_fixture("m6/providers")["providers"]]
    assert order == [*registered, "ledger"]  # registry order, then the orphan

    bank = row(page, "bank")
    assert '<a href="/engine/providers/bank/"><code>bank</code></a>' in bank
    assert text(cell(bank, "binding-ids")) == "account_id = acct-7731"
    assert '<code class="bound-id">acct-7731</code>' in bank
    assert text(cell(bank, "binding-status")) == "Bound"
    assert text(cell(bank, "binding-needs")) == "account_id"
    assert "Change ids" in cell(bank, "binding-action")
    assert f'href="{BANK_FORM}"' in bank
    assert text(cell(bank, "binding-bound")) != "—"

    shop = row(page, "shop")
    assert text(cell(shop, "binding-ids")) == "Not bound"
    assert text(cell(shop, "binding-bound")) == "—"
    assert text(cell(shop, "binding-needs")) == "no ids"
    assert text(cell(shop, "binding-status")) == "Not bound"
    assert text(cell(shop, "binding-action")) == "Bind"

    ledger = row(page, "ledger")
    assert "<code>ledger</code>" in ledger
    assert "<a " not in cell(ledger, "binding-provider")  # its engine:provider page would 404
    assert text(cell(ledger, "binding-ids")) == "ledger_id = l-1"
    assert text(cell(ledger, "binding-status")) == "Provider not registered"
    assert text(cell(ledger, "binding-action")) == ""
    assert "2 bindings" in page


def test_incomplete_binding_names_the_missing_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, providers=registry(region=True))
    page = operator_client.get(PAGE).content.decode()

    bank = row(page, "bank")
    assert cell(bank, "binding-status") == "Incomplete — missing <code>region</code>"
    assert 'title="Incomplete — missing region"' in bank
    assert text(cell(bank, "binding-needs")) == "account_id, region"


def test_unparsed_binding_raw(operator_client: Client, engine: FakeEngine) -> None:
    bad = {"provider": "bank", "external_ids": 5}
    raw = {"bindings": [engine_fixture("m6/bindings_orphan")["bindings"][1], bad]}
    assert ProviderBinding.from_json(bad) is None
    answer_page(engine)
    engine.side_effect("GET", BINDINGS, lambda request: httpx.Response(200, json=raw))
    page = operator_client.get(PAGE).content.decode()

    assert page.count("This binding couldn&#x27;t be read") == 1
    unread = re.search(r'<tr class="unread unread-binding">(.*?)</tr>', page, re.S)
    assert unread is not None
    assert '"external_ids": 5' in html_lib.unescape(unread.group(1))
    assert "acct-7731" not in page  # only the rejected entry is shown raw
    assert "1 binding" in page  # parsed only
    assert text(cell(row(page, "bank"), "binding-status")) == "Not bound"
    # Rows: registered, then the orphan, then the unreadable binding.
    assert page.index('id="binding-ledger"') < page.index("unread-binding")


def test_registry_failure_keeps_bindings_hides_bind(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("GET", REGISTRY, 503, json=error_json("database_unavailable"))
    page = operator_client.get(PAGE).content.decode()

    assert "The provider registry couldn&#x27;t be read — binding is unavailable" in page
    assert 'class="engine-error' in page
    bank = row(page, "bank")
    assert "<a " not in cell(bank, "binding-provider")
    assert text(cell(bank, "binding-ids")) == "account_id = acct-7731"
    assert text(cell(bank, "binding-needs")) == "—"
    assert text(cell(bank, "binding-status")) == "Bound"
    assert text(cell(bank, "binding-action")) == ""
    assert "/bind/" not in page
    assert 'id="binding-shop"' not in page


def test_registry_not_a_list_is_a_failed_registry_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#281: the model keeps a non-list ``providers`` for the Engine panel's unread row; this
    page reads it as a failed registry read (bad_response), never as "Provider not
    registered"."""
    answer_page(engine)
    raw = {"config_revision": 4, "providers": {"bank": {}}}
    engine.side_effect("GET", REGISTRY, lambda request: httpx.Response(200, json=raw))
    page = operator_client.get(PAGE).content.decode()

    assert "The provider registry couldn&#x27;t be read — binding is unavailable" in page
    assert 'class="engine-error' in page
    assert "bad_response" in page
    bank = row(page, "bank")
    assert text(cell(bank, "binding-status")) == "Bound"
    assert "Provider not registered" not in page
    assert "This provider couldn&#x27;t be read" not in page
    assert "/bind/" not in page


def test_bindings_failure_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    engine.respond("GET", BINDINGS, 503, json=error_json("database_unavailable"))
    response = operator_client.get(PAGE)
    page = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in page
    assert "<table" not in page
    assert ("GET", REGISTRY) not in requests(engine)

    engine.respond("GET", BINDINGS, 404, json=error_json("not_found"))
    response = operator_client.get(PAGE)
    assert response.status_code == 404
    assert "Story not found" in response.content.decode()


def test_story_read_failure_makes_no_further_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond("GET", STORY_PATH, 404, json=error_json("not_found"))
    response = operator_client.get(PAGE)
    assert response.status_code == 404
    assert "Story not found" in response.content.decode()
    assert requests(engine) == [("GET", f"/stories/{STORY}")]

    engine.reset()
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    page = operator_client.get(PAGE).content.decode()
    assert 'class="engine-error' in page
    assert requests(engine) == [("GET", f"/stories/{STORY}")]


def test_held_story_renders_the_held_page(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    engine.respond("GET", BINDINGS, 503, json=error_json("erasure_pending"))
    response = operator_client.get(PAGE)
    assert response.status_code == 503
    assert "Held until the erasure ledger is re-applied" in response.content.decode()


def test_needs_notice_always_shown(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    page = operator_client.get(PAGE).content.decode()
    assert NEEDS in html_lib.unescape(page)
    assert "refused with <code>provider_unbound</code>, naming it." in page
    assert "No providers are registered" not in page

    answer_page(engine, providers={"config_revision": 4, "providers": []})
    page = operator_client.get(PAGE).content.decode()
    assert NEEDS in html_lib.unescape(page)
    assert (
        '<a href="/engine/providers/">No providers are registered — register one in the '
        "Engine panel</a>"
    ) in page


def test_archived_story_notice_keeps_bind_links(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    story = dict(engine_fixture("m6/story"), status="archived")
    engine.respond("GET", STORY_PATH, 200, json=story)
    page = operator_client.get(PAGE).content.decode()
    assert "This story is archived — unarchive it to bind a provider." in page
    assert f'href="{BANK_FORM}"' in page


# --- the bind form --------------------------------------------------------------------------------


def test_bind_form_one_input_per_key(operator_client: Client, engine: FakeEngine) -> None:
    manifest = {
        "values": {
            "balance": {
                "type": "number",
                "read": {
                    "url": "https://bank.example.test/{binding.account_id}/{story.id}",
                    "headers": {
                        "Authorization": "Bearer {secret.BANK_TOKEN}",
                        "X-Region": "{binding.region}",
                        "X-Account": "{binding.account_id}",
                    },
                },
            }
        }
    }
    answer_form(engine, provider=bank_provider(manifest))
    response = operator_client.get(BANK_FORM)
    page = response.content.decode()

    assert response.status_code == 200
    assert re.findall(r'<input type="text" name="([^"]+)"', bind_form(page)) == [
        "id__account_id",
        "id__region",
    ]  # complement: {secret.BANK_TOKEN} and {story.id} give no input
    assert 'value="acct-7731"' in page
    assert "maxlength" not in bind_form(page)
    assert '<label for="id_id__account_id"><code>account_id</code></label>' in page
    assert "Harbour Ledger — Bind" in page
    assert '<a href="/engine/providers/bank/"><code>bank</code></a>' in page
    assert (
        "This provider's manifest substitutes: <code>account_id</code>, <code>region</code>"
    ) in page
    assert ">Save ids</button>" in page
    assert "Back to Providers" in page
    assert requests(engine) == [
        ("GET", f"/stories/{STORY}"),
        ("GET", f"/stories/{STORY}/providers"),
        ("GET", "/admin/external-providers/bank"),
    ]


def test_unused_existing_key_is_kept_and_blank_drops_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    bindings = engine_fixture("m6/bindings")
    bindings["bindings"][0]["external_ids"]["old_branch"] = "b-9"
    answer_form(engine, bindings=bindings)
    page, data = get_form(operator_client)
    assert "<code>old_branch</code> (not used by the manifest — blank drops it)" in page
    assert data["id__old_branch"] == "b-9"

    engine.respond("PUT", PUT, 200, json=written())
    data.update(id__old_branch="  ", id__account_id="acct-1")
    response = operator_client.post(BANK_FORM, data)
    assert response.status_code == 302
    (sent,) = puts(engine)
    assert body(sent) == {"external_ids": {"account_id": "acct-1"}}

    engine.reset()
    data.update(id__old_branch="b-10")
    operator_client.post(BANK_FORM, data)
    (sent,) = puts(engine)
    assert body(sent) == {"external_ids": {"account_id": "acct-1", "old_branch": "b-10"}}


def test_json_mode_without_keys(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine, provider=shop_provider())
    page, data = get_form(operator_client, SHOP_FORM)
    assert '<textarea name="external_ids"' in page
    assert '<input type="text"' not in bind_form(page)
    assert "This provider's manifest substitutes: no ids" in page
    assert "A JSON object of id names to ids" in page
    assert ">Bind</button>" in page

    engine.respond("PUT", PUT, 200, json=written(provider="shop", external_ids={"store": "s-9"}))
    response = operator_client.post(SHOP_FORM, {**data, "external_ids": '{"store": "s-9"}'})
    assert response.status_code == 302
    assert response["Location"] == f"{PAGE}#binding-shop"
    (sent,) = puts(engine)
    assert sent.url.path == SHOP_PUT
    assert body(sent) == {"external_ids": {"store": "s-9"}}


def test_json_mode_rejects_non_string_values(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine, provider=shop_provider())
    _, data = get_form(operator_client, SHOP_FORM)
    page = operator_client.post(SHOP_FORM, {**data, "external_ids": '{"store": 5}'})
    assert "Each id is a string" in page.content.decode()
    page = operator_client.post(SHOP_FORM, {**data, "external_ids": '{"bad-key": "x"}'})
    assert "Ids are named with letters, digits and _ (up to 64)" in page.content.decode()
    many = json.dumps({f"k{i}": "x" for i in range(33)})
    page = operator_client.post(SHOP_FORM, {**data, "external_ids": many})
    assert "Up to 32 ids" in page.content.decode()
    assert puts(engine) == []


def test_blank_required_id_makes_no_write(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    response = operator_client.post(BANK_FORM, {**data, "id__account_id": "   "})
    page = html_lib.unescape(response.content.decode())
    assert response.status_code == 200
    assert "Enter the id this provider's manifest substitutes for `{binding.account_id}`" in page
    assert [r for r in engine.requests if r.url.path == BANK_PUT] == []
    long = operator_client.post(BANK_FORM, {**data, "id__account_id": "x" * 257})
    assert ID_TOO_LONG in long.content.decode()
    assert puts(engine) == []


def test_unchanged_binding_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    response = operator_client.post(BANK_FORM, data)
    assert response.status_code == 302
    assert response["Location"] == PAGE
    assert ("info", "No changes — nothing was sent") in flashes(response)
    assert puts(engine) == []


def test_creating_a_binding_is_a_change(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: with no binding yet, ``{}`` (JSON mode) or filled inputs do send."""
    answer_form(engine, bindings=no_bindings(), provider=shop_provider())
    _, data = get_form(operator_client, SHOP_FORM)
    engine.respond("PUT", PUT, 200, json=written(provider="shop", external_ids={}))
    response = operator_client.post(SHOP_FORM, {**data, "external_ids": "{}"})
    assert response.status_code == 302
    (sent,) = puts(engine)
    assert body(sent) == {"external_ids": {}}
    assert ("success", f"Bound to shop — 0 ids, idempotency key {data['idempotency_key']}") in [
        f for f in flashes(response)
    ]

    engine.reset()
    answer_form(engine, bindings=no_bindings())
    page, data = get_form(operator_client)
    assert ">Bind</button>" in page
    engine.respond("PUT", PUT, 200, json=written())
    operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-7731"})
    assert len(puts(engine)) == 1


def test_bind_sends_full_map_and_page_key(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    page, data = get_form(operator_client)
    engine.respond("PUT", PUT, 200, json=written())
    operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-1138"})
    (sent,) = puts(engine)
    assert body(sent) == {"external_ids": {"account_id": "acct-1138"}}
    assert sent.headers["Idempotency-Key"] == key_in(page)
    assert "X-Epistrel-Expected-Revision" not in sent.headers  # complement (D8)


def test_values_sent_as_typed(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond("PUT", PUT, 200, json=written())
    operator_client.post(BANK_FORM, {**data, "id__account_id": " acct 1 "})
    (sent,) = puts(engine)
    assert body(sent)["external_ids"]["account_id"] == " acct 1 "


def test_warnings_and_activated_flashed(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond("PUT", PUT, 200, json=written())
    response = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-7732"})

    assert response.status_code == 302
    assert response["Location"] == f"{PAGE}#binding-bank"
    assert flashes(response) == [
        ("success", f"Bound to bank — 1 id, idempotency key {data['idempotency_key']}"),
        (
            "warning",
            "Binding stored, but: Below safety minimum: `model_calls` 6; a turn needs at least "
            "8 (reservation: guardrail_input=1, guardrail_output=1, generation=6)",
        ),
        ("info", "This binding made the story's required providers live: bank"),
    ]


def test_binding_incomplete_on_fields(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond(
        "PUT",
        PUT,
        422,
        json=error_json("binding_incomplete", provider="bank", missing=["account_id"]),
    )
    response = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-2"})
    page = response.content.decode()
    field = page.split('class="field-id__account_id"')[1].split("</p>")[0]
    assert "The provider&#x27;s manifest needs this id" in field
    assert 'value="acct-2"' in page
    assert key_in(page) != data["idempotency_key"]  # D8: an answered refusal is a new attempt


def test_binding_incomplete_unknown_key_above_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond(
        "PUT", PUT, 422, json=error_json("binding_incomplete", provider="bank", missing=["region"])
    )
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-2"}).content.decode()
    assert (
        "The provider&#x27;s manifest now also needs: <code>region</code> — reload to get its "
        "inputs"
    ) in page
    assert page.index("now also needs") < page.index('class="binding-form"')


def test_validation_error_on_key_field(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond(
        "PUT",
        PUT,
        422,
        json=error_json(
            "validation_error",
            "an external id holds no NUL",
            field="body.external_ids.account_id",
            reason="nul",
        ),
    )
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-3"}).content.decode()
    field = page.split('class="field-id__account_id"')[1].split("</p>")[0]
    assert "an external id holds no NUL" in field


def test_validation_error_in_json_mode_lands_on_the_box(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_form(engine, provider=shop_provider())
    _, data = get_form(operator_client, SHOP_FORM)
    engine.respond(
        "PUT",
        PUT,
        422,
        json=error_json("validation_error", "too long", field="body.external_ids.store.x"),
    )
    page = operator_client.post(SHOP_FORM, {**data, "external_ids": '{"store": "s"}'})
    field = page.content.decode().split('class="field-external_ids"')[1].split("</p>")[0]
    assert "too long" in field


def test_unregistered_provider_404_on_get(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    engine.respond("GET", ONE, 404, json=error_json("not_found", provider="ledger"))
    response = operator_client.get(f"/stories/{STORY}/providers/ledger/bind/")
    page = response.content.decode()
    assert response.status_code == 404
    assert "No provider named “ledger” is registered" in page
    assert 'class="binding-form"' not in page
    assert puts(engine) == []


def test_provider_read_failure_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    engine.respond("GET", ONE, 503, json=error_json("database_unavailable"))
    page = operator_client.get(BANK_FORM).content.decode()
    assert 'class="engine-error' in page
    assert 'class="binding-form"' not in page


def test_provider_deleted_before_put(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.reset()
    engine.respond("PUT", PUT, 404, json=error_json("not_found", provider="bank"))
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-4"}).content.decode()
    assert '<p class="refusal">bank is no longer registered</p>' in page
    assert 'value="acct-4"' in page
    assert requests(engine) == [("PUT", BANK_PUT)]  # re-rendered from the snapshot, no read


def test_archived_refusal_wording(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond("PUT", PUT, 409, json=error_json("story_archived"))
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-5"}).content.decode()
    assert "This story is archived — unarchive it to bind a provider." in page


def test_story_busy_wording(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond("PUT", PUT, 423, json=error_json("story_busy", reason="in_flight"))
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-6"}).content.decode()
    assert "Story busy — another turn is running" in page
    assert key_in(page) != data["idempotency_key"]


def test_other_errors_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.respond("PUT", PUT, 409, json=error_json("idempotency_conflict"))
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-7"}).content.decode()
    assert 'class="engine-error' in page
    assert key_in(page) != data["idempotency_key"]

    engine.fail("PUT", PUT, httpx.ConnectTimeout("slow"))
    page = operator_client.post(BANK_FORM, {**data, "id__account_id": "acct-7"}).content.decode()
    assert 'class="engine-error' in page
    assert key_in(page) == data["idempotency_key"]  # D8: the outcome is unknown, so replay


def test_invalid_provider_name_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"/stories/{STORY}/providers/Bank/bind/")
    assert response.status_code == 404
    assert engine.requests == []
    response = operator_client.post(f"/stories/{STORY}/providers/Bank/bind/", {})
    assert response.status_code == 404
    assert engine.requests == []


def test_signed_snapshot_rerender_makes_no_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_form(engine)
    _, data = get_form(operator_client)
    engine.reset()
    response = operator_client.post(BANK_FORM, {**data, "id__account_id": ""})
    assert response.status_code == 200
    assert engine.requests == []

    # A tampered snapshot falls back to the three reads.
    operator_client.post(BANK_FORM, {**data, "id__account_id": "", "snapshot": "x"})
    assert len([r for r in engine.requests if r.method == "GET"]) == 3


def test_archived_story_form_notice(operator_client: Client, engine: FakeEngine) -> None:
    answer_form(engine)
    engine.respond("GET", STORY_PATH, 200, json=dict(engine_fixture("m6/story"), status="archived"))
    page = operator_client.get(BANK_FORM).content.decode()
    assert "This story is archived — unarchive it to bind a provider." in page
    assert 'class="binding-form"' in page


def test_unreadable_existing_binding_counts_as_none(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_form(engine)
    raw = {"bindings": [{"provider": "bank", "external_ids": 5}]}
    engine.side_effect("GET", BINDINGS, lambda request: httpx.Response(200, json=raw))
    page = operator_client.get(BANK_FORM).content.decode()
    assert "The existing binding couldn&#x27;t be read — saving replaces it" in page
    assert ">Bind</button>" in page


def test_play_and_settings_link_providers(operator_client: Client, engine: FakeEngine) -> None:
    from tests.play.helpers import STORY as PLAY_STORY
    from tests.play.helpers import answer_page as answer_play
    from tests.settings.helpers import answer_page as answer_settings

    answer_play(engine)
    play = operator_client.get(f"/stories/{PLAY_STORY}/").content.decode()
    link = f'<a class="providers-link" href="/stories/{PLAY_STORY}/providers/">Providers</a>'
    assert link in play
    assert play.index('class="config-tab"') < play.index(link)

    answer_settings(engine)
    settings = operator_client.get(f"/stories/{PLAY_STORY}/settings/").content.decode()
    assert link in settings
    assert settings.index('class="config-tab"') < settings.index(link)


# --- the form alone -------------------------------------------------------------------------------


def test_clean_bound_id() -> None:
    assert clean_bound_id(None) is None
    assert clean_bound_id(" \t ") is None
    assert clean_bound_id(" a ") == " a "
    assert clean_bound_id("x" * 256) == "x" * 256
    with pytest.raises(ValidationError, match=ID_TOO_LONG):
        clean_bound_id("x" * 257)


def test_binding_form_external_ids_order() -> None:
    form = BindingForm(
        {"id__b": "2", "id__a": "1", "id__old": "o"},
        keys=["b", "a"],
        existing={"a": "1", "old": "o", "b": "2"},
    )
    assert form.is_valid()
    assert list(form.external_ids()) == ["b", "a", "old"]
    assert form.unchanged()
