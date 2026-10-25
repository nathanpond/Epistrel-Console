"""Acting as any principal (#64): the nav selector, ``POST /acting-as/``, the banner, and every
Engine call made through ``engine_client_for`` carrying the acting principal."""

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django import forms
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings

from console.acting import acting_principal
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.principal import PrincipalField, remember_principals
from console.models import ActingPrincipal, EngineOperation, KnownPrincipal
from console.views import stories as story_views
from tests.conftest import OPERATOR_PASSWORD
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.stories.helpers import summary

pytestmark = pytest.mark.django_db

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"
QUIET = "/operations/"  # a page that makes no Engine call
BANNER = "everything you do in the Engine is recorded as them."


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def act_as(client: Client, principal: str = "", *, other: str = "", **extra: str) -> Any:
    data = {"principal": principal, "other": other, "next": QUIET, **extra}
    return client.post("/acting-as/", data)


def flash(response: Any) -> str:
    """The latest message flashed (earlier redirects in a test are not followed, so their
    messages are still queued)."""
    return [str(m) for m in get_messages(response.wsgi_request)][-1]


def principal_header(request: httpx.Request) -> bytes:
    return next(v for k, v in request.headers.raw if k.lower() == b"x-epistrel-principal")


def request_for(user: Any) -> HttpRequest:
    request = RequestFactory().get("/")
    request.user = user
    return request


def stored(user: Any) -> str | None:
    row = ActingPrincipal.objects.filter(user=user).first()
    return row.principal if row else None


# --- the default and the selection ----------------------------------------------------------------


def test_the_default_is_the_operators_own_principal(operator: Any) -> None:
    assert acting_principal(request_for(operator)) == "console:nate"
    assert not ActingPrincipal.objects.exists()


def test_acting_principal_is_read_once_per_request(
    operator: Any, django_assert_num_queries: Any
) -> None:
    request = request_for(operator)
    with django_assert_num_queries(1):
        acting_principal(request)
        acting_principal(request)


def test_selecting_alice_saves_remembers_and_returns_to_next(operator_client: Client) -> None:
    response = act_as(operator_client, other="alice")
    assert response.status_code == 302
    assert response["Location"] == QUIET
    assert stored(User.objects.get(username="nate")) == "alice"
    assert list(KnownPrincipal.objects.values_list("principal", flat=True)) == ["alice"]
    assert flash(response) == "Now acting as alice."


def test_a_non_blank_other_wins_over_the_select(operator_client: Client, operator: Any) -> None:
    act_as(operator_client, "console:nate", other="alice")
    assert stored(operator) == "alice"


def test_the_select_is_used_when_other_is_blank(operator_client: Client, operator: Any) -> None:
    act_as(operator_client, "bob", other="  ")
    assert stored(operator) == "bob"


def test_a_mutation_and_a_principal_requiring_read_carry_the_acting_principal(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    act_as(operator_client, other="alice")
    engine.respond("POST", "/stories/{story_id}/archive", 200, json=engine_fixture("archive_story"))
    operator_client.post(
        f"/stories/{STORY}/archive/", {"idempotency_key": KEY, "name": "Harbour", "next": QUIET}
    )
    (mutation,) = engine.requests
    assert principal_header(mutation) == b"alice"
    (row,) = EngineOperation.objects.all()
    assert (row.operator_username, row.operator, row.principal) == ("nate", operator, "alice")

    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    request = request_for(operator)
    client = engine_client_for(request)
    client._transport = engine.transport
    client.get_story(STORY)
    assert principal_header(engine.requests[-1]) == b"alice"


def test_a_get_read_carries_the_acting_principal(
    operator_client: Client, engine: FakeEngine
) -> None:
    act_as(operator_client, other="alice")
    engine.respond("GET", "/stories", 200, json={"items": [summary(STORY, "Harbour", "active")]})
    engine.respond("GET", "/stories/{story_id}", 200, json=engine_fixture("get_story"))
    operator_client.get("/stories/")
    assert engine.requests
    assert {principal_header(r) for r in engine.requests} == {b"alice"}


def test_back_to_me_restores_the_default(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    act_as(operator_client, other="alice")
    response = act_as(operator_client, action="reset")
    assert flash(response) == "Back to acting as console:nate."
    assert not ActingPrincipal.objects.exists()
    engine.respond("GET", "/stories", 200, json={"items": []})
    operator_client.get("/stories/")
    assert principal_header(engine.requests[0]) == b"console:nate"


def test_selecting_yourself_leaves_no_row_and_no_known_principal(
    operator_client: Client, operator: Any
) -> None:
    act_as(operator_client, other="alice")
    response = act_as(operator_client, "console:nate")
    assert not ActingPrincipal.objects.exists()
    assert not KnownPrincipal.objects.filter(principal="console:nate").exists()
    assert flash(response) == "Back to acting as console:nate."


def test_any_principal_may_be_selected_another_console_user_included(
    operator_client: Client, operator: Any
) -> None:
    act_as(operator_client, other="console:someone")
    assert stored(operator) == "console:someone"


def test_resubmitting_the_current_principal_bumps_last_used_at(
    operator_client: Client,
) -> None:
    act_as(operator_client, other="alice")
    first = KnownPrincipal.objects.get(principal="alice").last_used_at
    response = act_as(operator_client, "alice")
    assert KnownPrincipal.objects.get(principal="alice").last_used_at > first
    assert flash(response) == "Now acting as alice."


def test_a_second_operators_selection_does_not_affect_the_first(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    bob = User.objects.create_superuser("bob", "bob@example.invalid", OPERATOR_PASSWORD)
    other = Client()
    other.force_login(bob)
    act_as(other, other="carol")
    assert stored(bob) == "carol"
    assert acting_principal(request_for(operator)) == "console:nate"
    engine.respond("GET", "/stories", 200, json={"items": []})
    operator_client.get("/stories/")
    assert principal_header(engine.requests[0]) == b"console:nate"


def test_the_selection_persists_across_logout_and_login(
    operator_client: Client, operator: Any
) -> None:
    act_as(operator_client, other="alice")
    operator_client.post("/accounts/logout/")
    operator_client.force_login(operator)
    assert BANNER in operator_client.get(QUIET).content.decode()


def test_changes_are_logged_at_info(
    operator_client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="console.acting"):
        act_as(operator_client, other="alice")
    (record,) = [r for r in caplog.records if r.name == "console.acting"]
    assert record.getMessage() == (
        "acting principal changed: operator=nate old=console:nate new=alice"
    )
    assert not EngineOperation.objects.exists()  # never in the operation log


# --- refusals ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "Principal can't be blank."),
        ("system", "`system` is reserved for the Engine."),
        ("x" * 257, "Principal is longer than 256 characters."),
        ("a\nb", "Principal contains a control character."),
        ("a\tb", "Principal contains a control character."),
        ("a\x85b", "Principal contains a control character."),
        ("ŝ", "Principal can only use Latin-1 characters (it is sent in an HTTP header)."),
        ("🙂", "Principal can only use Latin-1 characters (it is sent in an HTTP header)."),
    ],
)
def test_invalid_values_are_refused_with_zero_engine_calls(
    operator_client: Client, engine: FakeEngine, operator: Any, value: str, reason: str
) -> None:
    act_as(operator_client, other="alice")
    response = act_as(operator_client, value)
    assert response.status_code == 302
    assert flash(response) == reason
    assert stored(operator) == "alice"  # unchanged
    assert not KnownPrincipal.objects.exclude(principal="alice").exists()
    assert engine.requests == []


def test_only_the_first_failing_rule_is_shown(operator_client: Client) -> None:
    # A control character and over-long: the control character is reported (precedence).
    response = act_as(operator_client, other="\n" + "x" * 300)
    assert flash(response) == "Principal contains a control character."


def test_surrounding_spaces_and_tabs_are_trimmed(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    act_as(operator_client, "  alice\t")
    assert stored(operator) == "alice"
    engine.respond("GET", "/stories", 200, json={"items": []})
    operator_client.get("/stories/")
    assert principal_header(engine.requests[0]) == b"alice"


def test_cafe_is_accepted_and_sent_as_latin1(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    act_as(operator_client, other="café")
    assert stored(operator) == "café"
    engine.respond("GET", "/stories", 200, json={"items": []})
    operator_client.get("/stories/")
    assert principal_header(engine.requests[0]) == b"caf\xe9"


@pytest.mark.parametrize("value", ["ŝ", "🙂", "café"])
def test_a_write_form_principal_field_accepts_non_latin1(value: str) -> None:
    # Complement: write-form values travel in JSON bodies, so only the acting selector refuses.
    class Form(forms.Form):
        owner = PrincipalField()

    form = Form({"owner": value})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["owner"] == value


def test_get_is_405(operator_client: Client) -> None:
    assert operator_client.get("/acting-as/").status_code == 405


@pytest.mark.parametrize("unsafe", ["https://evil.example/", "//evil.example/x", ""])
def test_an_unsafe_next_redirects_home(operator_client: Client, unsafe: str) -> None:
    response = act_as(operator_client, other="alice", next=unsafe)
    assert response["Location"] == "/"


def test_a_safe_referer_is_the_fallback(operator_client: Client) -> None:
    response = operator_client.post(
        "/acting-as/",
        {"other": "alice", "next": "https://evil.example/"},
        headers={"referer": "http://testserver/models/"},
    )
    assert response["Location"] == "http://testserver/models/"


def test_a_non_operator_cannot_pick(client: Client) -> None:
    staffer = User.objects.create_user("staffer", password=OPERATOR_PASSWORD, is_staff=True)
    client.force_login(staffer)
    assert client.post("/acting-as/", {"other": "alice"}).status_code == 403
    assert not ActingPrincipal.objects.exists()


# --- nav selector and banner ------------------------------------------------------------------


def test_no_banner_while_acting_as_yourself(operator_client: Client) -> None:
    html = operator_client.get(QUIET).content.decode()
    assert "Acting as: <code>console:nate</code>" in html
    assert BANNER not in html
    assert 'value="reset"' not in html  # "Back to me" hidden while acting as self


def test_banner_while_acting_as_someone_else(operator_client: Client) -> None:
    act_as(operator_client, other="alice")
    html = operator_client.get(QUIET).content.decode()
    assert '<div class="banner banner-warning" role="status">' in html
    assert f"Acting as <strong><code>alice</code></strong> — {BANNER}" in html
    assert "Acting as: <code>alice</code>" in html
    assert html.count('name="action" value="reset"') == 2  # nav and banner


def test_the_selector_offers_me_first_then_known_principals(
    operator_client: Client,
) -> None:
    act_as(operator_client, other="alice")
    act_as(operator_client, other="bob")
    KnownPrincipal.objects.filter(principal="bob").delete()  # current, though no longer known
    html = operator_client.get(QUIET).content.decode()
    options = html[html.index('<select name="principal">') :]
    options = options[: options.index("</select>")]
    assert options.index('value="console:nate">console:nate (me)') < options.index(
        'value="bob" selected>bob'
    )
    assert options.index('value="bob" selected') < options.index('value="alice">alice')
    assert 'value=""' not in options  # no blank option
    assert f'name="next" value="{QUIET}"' in html


def test_the_selector_carries_the_full_path_as_next(operator_client: Client) -> None:
    html = operator_client.get(f"{QUIET}?kind=respond").content.decode()
    assert 'name="next" value="/operations/?kind=respond"' in html


def test_no_selector_on_the_login_page(client: Client) -> None:
    html = client.get("/accounts/login/").content.decode()
    assert "Acting as" not in html


# --- PrincipalField ---------------------------------------------------------------------------


class OwnerForm(forms.Form):
    owner = PrincipalField()
    relation = PrincipalField(required=False)
    owner_filter = PrincipalField(required=False, allow_system=True, remember=False)


def test_the_datalist_lists_known_principals() -> None:
    KnownPrincipal.objects.create(principal="alice")
    KnownPrincipal.objects.create(principal="bob")
    html = str(OwnerForm()["owner"])
    assert 'list="dl-id_owner"' in html
    assert '<datalist id="dl-id_owner">' in html
    assert '<option value="alice"></option>' in html
    assert '<option value="bob"></option>' in html
    # Each field has its own datalist.
    assert '<datalist id="dl-id_relation">' in str(OwnerForm()["relation"])


def test_typing_a_new_principal_adds_it_and_filters_do_not() -> None:
    form = OwnerForm({"owner": " dave ", "relation": "", "owner_filter": "erin"})
    assert form.is_valid(), form.errors
    assert remember_principals(form) == ["dave"]
    assert list(KnownPrincipal.objects.values_list("principal", flat=True)) == ["dave"]


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("", "Principal can't be blank."),
        ("system", "`system` is reserved for the Engine."),
        ("a\tb", "Principal contains a control character."),
        ("y" * 257, "Principal is longer than 256 characters."),
    ],
)
def test_principal_field_errors_use_the_refusal_texts(value: str, error: str) -> None:
    form = OwnerForm({"owner": value})
    assert not form.is_valid()
    assert form.errors["owner"] == [error]


def test_filter_fields_allow_system() -> None:
    form = OwnerForm({"owner": "alice", "owner_filter": "system"})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["owner_filter"] == "system"
