"""Provider bindings at blank story creation (#259): one row per registered provider, the
ticked rows sent as ``provider_bindings``, and the Engine's binding refusals on the rows."""

import json
import re
from collections.abc import Mapping
from typing import Any

import pytest
from django.core import signing
from django.test import Client

from console.engine.models import ProviderList
from console.forms import CharacterFormSet, StoryForm, story_spec
from console.forms.bindings import ProviderChoices
from console.models import EngineOperation
from console.views import stories as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import text
from tests.stories.helpers import calls

pytestmark = pytest.mark.django_db

URL = "/stories/new/"
REGISTRY = "/admin/external-providers"
TEEN = "00000000000000000000RAT002"
RATINGS = [("00000000000000000000RAT001", "Everyone"), (TEEN, "Teen")]
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"
NEW_ID = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
MARA = {
    "name": "Mara",
    "definition": "A harbour pilot.",
    "true_age": "34",
    "is_player": "on",
    "safe_fallback_text": "",
    "deflection_style": "",
}


def registry() -> dict[str, Any]:
    answer: dict[str, Any] = engine_fixture("m6/providers")
    return answer


def choices(answer: dict[str, Any] | None = None) -> ProviderChoices:
    listed = ProviderList.from_json(answer if answer is not None else registry())
    assert listed is not None
    return ProviderChoices.of(listed.providers)


def form_data(
    *updates: Mapping[str, str], signed: ProviderChoices | None = None, **extra: str
) -> dict[str, Any]:
    """The posted form with one character, the signed rows the page carried and ``extra``
    (``__`` for ``-``)."""
    data: dict[str, Any] = {
        "name": "Harbour",
        "content_rating_id": TEEN,
        "viewpoint": "",
        "idempotency_key": KEY,
        "rating_choices": views._signed_ratings(RATINGS),
        "provider_choices": views._signed_providers(signed or choices()),
        "characters-TOTAL_FORMS": "1",
        "characters-INITIAL_FORMS": "0",
        "characters-MIN_NUM_FORMS": "1",
        "characters-MAX_NUM_FORMS": "20",
    }
    for name, value in MARA.items():
        data[f"characters-0-{name}"] = value
    for update in [*updates, extra]:
        data.update({k.replace("__", "-"): v for k, v in update.items()})
    return data


GUILD = {"bind__guild": "on", "bindid__guild__player": "p-48", "bindid__guild__realm": "eu-1"}


def posts(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", "/stories")


def sent(engine: FakeEngine) -> Any:
    (request,) = posts(engine)
    return json.loads(request.content)


def row(html: str, name: str) -> str:
    start = html.index(f'id="binding-{name}"')
    return html[start : html.index("</div>", start)]


def field_block(html: str, name: str) -> str:
    start = html.index(f'id="id_{name}"')
    return html[html.rfind("<p", 0, start) : html.index("</p>", start)]


def error_422(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


@pytest.fixture
def ratings(engine: FakeEngine) -> FakeEngine:
    engine.respond(
        "GET", "/admin/content-ratings", 200, json=engine_fixture("list_content_ratings")
    )
    engine.respond("GET", "/admin/settings", 200, json=engine_fixture("get_deployment_settings"))
    return engine


def no_story_provider_calls(engine: FakeEngine) -> None:
    """Neither form reads or writes a story's providers (``/stories/{id}/providers…``)."""
    assert not [r for r in engine.requests if "/providers" in r.url.path.removeprefix(REGISTRY)]


# --- the rows ------------------------------------------------------------------------------------


def test_blank_form_lists_each_provider_with_its_keys(
    operator_client: Client, ratings: FakeEngine
) -> None:
    html = operator_client.get(URL).content.decode()
    order = re.findall(r'id="binding-([^"]+)"', html)
    assert order == [p["name"] for p in registry()["providers"]]  # registry order

    guild = row(html, "guild")
    assert 'name="bind-guild"' in guild
    assert "checked" not in guild  # nothing is needed on a blank story
    inputs = re.findall(r'name="(bindid-guild-[^"]+)"', guild)
    assert inputs == ["bindid-guild-player", "bindid-guild-realm"]  # first-use order
    assert "<code>player</code>" in guild
    assert "Fills {binding.player} in the manifest" in guild
    assert "maxlength" not in guild
    assert "advisory · read-only" in text(guild)
    assert '<a href="/engine/providers/guild/"><code>guild</code></a>' in guild

    shop = row(html, "shop")
    assert 'name="bind-shop"' in shop
    assert "bindid-shop" not in shop  # no keys: the checkbox only
    assert "Its manifest substitutes no ids: binding it records the link" in shop
    assert "authoritative · write-capable" in text(row(html, "bank"))

    reads = [(r.method, r.url.path) for r in ratings.requests if r.method == "GET"]
    assert reads == [
        ("GET", "/admin/content-ratings"),
        ("GET", "/admin/settings"),
        ("GET", REGISTRY),
    ]
    # The rows travel signed, so a POST needs no second read.
    signed = re.search(r'name="provider_choices" value="([^"]+)"', html)
    assert signed is not None
    assert signing.loads(signed.group(1), salt=views.PROVIDERS_SALT)["rows"][0][0] == "bank"


def test_untouched_bindings_send_no_provider_bindings(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    response = operator_client.post(URL, form_data())
    assert response.status_code == 302
    body = sent(engine)
    assert "provider_bindings" not in body
    # Complement: byte for byte the body the form sent before #259 for the same cast.
    data = form_data()
    story = StoryForm(data, ratings=RATINGS)
    characters = CharacterFormSet(data, prefix="characters")
    assert story.is_valid()
    assert characters.is_valid()
    before = story_spec(story, characters)
    assert body == before
    assert list(body) == list(before)
    no_story_provider_calls(engine)


def test_ticked_rows_are_sent(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    response = operator_client.post(URL, form_data(GUILD, bind__shop="on"))
    assert response.status_code == 302
    assert response["Location"] == f"/stories/{NEW_ID}/"
    assert sent(engine)["provider_bindings"] == {
        "guild": {"external_ids": {"player": "p-48", "realm": "eu-1"}},
        "shop": {"external_ids": {}},
    }
    # Sent exactly as typed (not trimmed).
    engine.reset()
    operator_client.post(URL, form_data(GUILD, bindid__guild__player=" p-48 "))
    assert sent(engine)["provider_bindings"]["guild"]["external_ids"]["player"] == " p-48 "
    flash = operator_client.get("/operations/").content.decode()
    assert "· bound to guild" in flash
    # The #44 row keeps the body, bindings included (ids, not secrets).
    recorded = EngineOperation.objects.order_by("-id").first()
    assert recorded is not None
    assert "p-48" in json.dumps(recorded.request)
    no_story_provider_calls(engine)


def test_local_binding_errors_make_no_call(operator_client: Client, engine: FakeEngine) -> None:
    blank = operator_client.post(
        URL, form_data(bind__guild="on", bindid__guild__player="p-48", bindid__guild__realm="  ")
    )
    html = blank.content.decode()
    assert "Enter this id: the manifest substitutes {binding.realm}" in field_block(
        html, "bindid-guild-realm"
    )
    assert "Enter this id" not in field_block(html, "bindid-guild-player")

    unticked = operator_client.post(URL, form_data(bindid__guild__player="p-48"))
    assert "Tick “Bind guild” to send these ids, or clear them" in field_block(
        unticked.content.decode(), "bind-guild"
    )

    too_long = operator_client.post(
        URL, form_data(bind__guild="on", bindid__guild__player="x" * 257, bindid__guild__realm="r")
    )
    assert "Up to 256 characters" in field_block(too_long.content.decode(), "bindid-guild-player")
    assert posts(engine) == []

    # Complement: the same half-filled rows on an Add-character re-render say nothing.
    added = operator_client.post(
        URL, form_data(add="1", bind__guild="on", bindid__guild__player="p-48")
    )
    page = added.content.decode()
    assert 'id="character-1"' in page
    assert "Enter this id" not in page
    assert "Tick “Bind" not in page
    assert 'value="p-48"' in page  # kept
    assert posts(engine) == []
    assert calls(engine, "GET", REGISTRY) == []  # the signed rows, every time


def test_binding_incomplete_lands_on_the_missing_keys(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "binding_incomplete",
            "the binding to 'guild' lacks the ids its manifest substitutes: realm",
            provider="guild",
            missing=["realm"],
        ),
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "The provider&#x27;s manifest needs this id" in field_block(html, "bindid-guild-realm")
    assert "needs this id" not in field_block(html, "bindid-guild-player")
    assert "The Engine rejected the story: the binding to" in html
    assert 'value="eu-1"' in html  # the input is kept


def test_binding_incomplete_for_a_provider_without_a_row_shows_at_the_top(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422("binding_incomplete", "lacks ids", provider="vault", missing=["k"]),
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "vault: lacks ids" in text(html)


def test_not_found_lands_on_the_provider_row(operator_client: Client, engine: FakeEngine) -> None:
    # D18: POST /stories doesn't declare this 404 (Engine bug nathanpond/Epistrel#682).
    engine.respond_undeclared(
        "POST",
        "/stories",
        404,
        json=error_422("not_found", "no provider 'guild'", provider="guild"),
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "guild isn&#x27;t registered (anymore) — untick it or register it" in field_block(
        html, "bind-guild"
    )
    assert "The Engine rejected the story: no provider" in html
    assert 'class="engine-error' not in html


def test_a_404_naming_no_sent_binding_is_the_error_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_undeclared(
        "POST", "/stories", 404, json=error_422("not_found", "gone", provider="vault")
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "The Engine rejected the story" not in html
    assert "isn&#x27;t registered (anymore)" not in html
    assert "not_found" in html  # the panel names the code


def test_external_id_validation_lands_on_its_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    # What the Engine's check_external_ids reports: no provider in the path.
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "validation_error",
            "an external id holds no NUL",
            field="body.external_ids.player",
            reason="nul",
        ),
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "an external id holds no NUL" in field_block(html, "bindid-guild-player")
    assert "The Engine rejected the story: an external id holds no NUL" in html

    # A path naming the provider lands on that input too.
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422(
            "validation_error",
            "too long",
            field="body.provider_bindings.guild.external_ids.realm",
        ),
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "too long" in field_block(html, "bindid-guild-realm")

    # A key no ticked row has goes to the top.
    engine.respond(
        "POST",
        "/stories",
        422,
        json=error_422("validation_error", "bad key", field="body.external_ids.zone"),
    )
    html = operator_client.post(URL, form_data(GUILD)).content.decode()
    assert "zone: bad key" in text(html)


def test_a_failed_registry_read_creates_without_bindings(
    operator_client: Client, ratings: FakeEngine
) -> None:
    ratings.respond("GET", REGISTRY, 503, json=error_422("unavailable", "database unavailable"))
    html = operator_client.get(URL).content.decode()
    assert (
        "Couldn't read the provider registry (unavailable — database unavailable) — the story "
        "is created without bindings; bind it later on its Providers page"
    ) in text(html).replace("&#x27;", "'")
    assert 'name="bind-' not in html
    signed = re.search(r'name="provider_choices" value="([^"]+)"', html)
    assert signed is not None

    # Complement: the failure doesn't block creation, and a POST doesn't retry the read.
    ratings.reset()
    ratings.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    data = form_data()
    data["provider_choices"] = signed.group(1)
    response = operator_client.post(URL, data)
    assert response.status_code == 302
    assert "provider_bindings" not in sent(ratings)
    assert calls(ratings, "GET", REGISTRY) == []


def test_tampered_provider_choices_are_read_again(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form_data(add="1")
    data["provider_choices"] = data["provider_choices"][:-2] + "xx"
    operator_client.post(URL, data)
    assert len(calls(engine, "GET", REGISTRY)) == 1
    engine.reset()
    missing = form_data(add="1")
    del missing["provider_choices"]
    operator_client.post(URL, missing)
    assert len(calls(engine, "GET", REGISTRY)) == 1
    # Complement: with a valid signed field, a POST makes no registry read.
    engine.reset()
    operator_client.post(URL, form_data(add="1"))
    assert calls(engine, "GET", REGISTRY) == []


def test_no_registered_provider_reads_none(operator_client: Client, ratings: FakeEngine) -> None:
    ratings.respond("GET", REGISTRY, 200, json={"config_revision": 1, "providers": []})
    html = operator_client.get(URL).content.decode()
    assert "No providers are registered." in html
    assert 'name="bind-' not in html
    ratings.respond("POST", "/stories", 201, json=engine_fixture("create_story"))
    operator_client.post(URL, form_data(signed=ProviderChoices([])))
    assert "provider_bindings" not in sent(ratings)
