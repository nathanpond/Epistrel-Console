"""The shared confirm-page helpers (#43's convention, built in #45)."""

from typing import Any

import pytest
from django.http import HttpRequest, HttpResponse
from django.template import engines
from django.template.loader import get_template
from django.test import RequestFactory

from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)

PAGE = ConfirmPage(title="T", question="Do {name}?", emphasis="it", confirm_label="Go",
                   cancel_url="/back/")  # fmt: skip


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("/stories/?status=active", "/stories/?status=active"),
        ("https://evil.example/", "/fallback/"),
        ("//evil.example/x", "/fallback/"),
        ("", "/fallback/"),
    ],
)
def test_safe_next(rf: RequestFactory, given: str, expected: str) -> None:
    assert safe_next(rf.get("/x/", {"next": given}), "/fallback/") == expected


def test_posted_next_wins(rf: RequestFactory) -> None:
    request = rf.post("/x/?next=/get/", {"next": "/post/"})
    assert safe_next(request, "/f/") == "/post/"


def test_posted_key_or_a_fresh_one(rf: RequestFactory) -> None:
    assert posted_idempotency_key(rf.post("/x/", {"idempotency_key": "a.b:c-1"})) == "a.b:c-1"
    fresh = posted_idempotency_key(rf.post("/x/", {"idempotency_key": "no spaces"}))
    assert len(fresh) == 26


@pytest.mark.django_db
def test_get_renders_and_post_performs(rf: RequestFactory, admin_user: Any) -> None:
    calls: list[str] = []

    def perform(key: str) -> HttpResponse:
        calls.append(key)
        return HttpResponse("done")

    get: HttpRequest = rf.get("/x/")
    get.user = admin_user
    page = confirm_action(get, PAGE, perform).content.decode()
    assert "Do <em>it</em>?" in page
    assert '<a href="/back/">Cancel</a>' in page
    assert calls == []
    post = rf.post("/x/", {"idempotency_key": "k1"})
    assert confirm_action(post, PAGE, perform).content == b"done"
    assert calls == ["k1"]
    assert confirm_action(rf.put("/x/"), PAGE, perform).status_code == 405


def _req(rf: RequestFactory, user: Any, method: str, data: dict[str, str] | None = None) -> Any:
    request = rf.post("/x/", data or {}) if method == "POST" else rf.get("/x/")
    request.user = user
    return request


class Recorder:
    """A ``perform`` that records the keys it was called with."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, key: str) -> HttpResponse:
        self.calls.append(key)
        return HttpResponse("done")


@pytest.mark.django_db
def test_confirmed_mode_get_carries_confirmed_flag(rf: RequestFactory, admin_user: Any) -> None:
    perform = Recorder()
    get = _req(rf, admin_user, "GET")
    html = confirm_action(get, PAGE, perform, require_confirmed=True).content.decode()
    assert '<input type="hidden" name="confirmed" value="1">' in html
    assert perform.calls == []


@pytest.mark.django_db
def test_confirmed_mode_unconfirmed_post_rerenders_with_the_posted_key(
    rf: RequestFactory, admin_user: Any
) -> None:
    perform = Recorder()
    post = _req(rf, admin_user, "POST", {"idempotency_key": "first-render-key"})
    response = confirm_action(post, PAGE, perform, require_confirmed=True)
    html = response.content.decode()
    assert response.status_code == 200
    assert '<input type="hidden" name="idempotency_key" value="first-render-key">' in html
    assert '<input type="hidden" name="confirmed" value="1">' in html
    assert "incomplete" not in html
    assert perform.calls == []  # complement: an unconfirmed POST never performs


@pytest.mark.django_db
def test_confirmed_mode_confirmed_post_performs_once_with_the_carried_key(
    rf: RequestFactory, admin_user: Any
) -> None:
    perform = Recorder()
    post = _req(rf, admin_user, "POST", {"idempotency_key": "first-render-key", "confirmed": "1"})
    assert confirm_action(post, PAGE, perform, require_confirmed=True).content == b"done"
    assert perform.calls == ["first-render-key"]


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["", "no spaces"])
def test_confirmed_mode_bad_key_rerenders_fresh_with_an_error(
    rf: RequestFactory, admin_user: Any, key: str
) -> None:
    perform = Recorder()
    post = _req(rf, admin_user, "POST", {"idempotency_key": key, "confirmed": "1"})
    response = confirm_action(post, PAGE, perform, require_confirmed=True)
    html = response.content.decode()
    assert response.status_code == 200
    assert "This confirmation was incomplete — confirm again" in html
    assert 'name="idempotency_key" value="no spaces"' not in html
    assert perform.calls == []


@pytest.mark.django_db
def test_default_mode_post_without_confirmed_still_performs(
    rf: RequestFactory, admin_user: Any
) -> None:
    perform = Recorder()
    post = _req(rf, admin_user, "POST", {"idempotency_key": "k2"})
    assert confirm_action(post, PAGE, perform).content == b"done"
    assert perform.calls == ["k2"]
    html = confirm_action(_req(rf, admin_user, "GET"), PAGE, perform).content.decode()
    assert 'name="confirmed"' not in html


@pytest.mark.django_db
def test_render_confirm_uses_a_given_key(rf: RequestFactory, admin_user: Any) -> None:
    get = _req(rf, admin_user, "GET")
    html = render_confirm(get, PAGE, idempotency_key="given").content.decode()
    assert '<input type="hidden" name="idempotency_key" value="given">' in html


# ``_confirm.html`` as it stood before #134 (67cee2a): a default-mode page renders it byte for byte.
PRE_134_CONFIRM = """<div class="confirm">
  <p class="question">{% if has_emphasis %}{{ question_before }}<em>{{ page.emphasis }}</em>{{ question_after }}{% else %}{{ page.question }}{% endif %}</p>
  {% for line in page.lines %}<p>{{ line }}</p>{% endfor %}
  {% if page.facts %}
  <dl>
    {% for label, value in page.facts %}<dt>{{ label }}</dt><dd><code>{{ value }}</code></dd>{% endfor %}
  </dl>
  {% endif %}
  <form method="post">
    {% csrf_token %}
    <input type="hidden" name="idempotency_key" value="{{ idempotency_key }}">
    <input type="hidden" name="next" value="{{ next_url }}">
    {% for name, value in page.hidden.items %}<input type="hidden" name="{{ name }}" value="{{ value }}">
    {% endfor %}
    {% if page.fields_template %}{% include page.fields_template %}{% endif %}
    <button type="submit"{% if page.danger %} class="danger"{% endif %}>{{ page.confirm_label }}</button>
    <a href="{{ next_url }}">Cancel</a>
  </form>
</div>
"""  # noqa: E501


@pytest.mark.parametrize(
    "page",
    [
        PAGE,
        ConfirmPage(title="T", question="Drop it?", confirm_label="Drop", cancel_url="/b/",
                    lines=("One", "Two"), facts=(("Id", "x1"),), hidden={"a": "1"},
                    danger=True),
    ],
)  # fmt: skip
def test_a_default_mode_page_renders_byte_for_byte_as_before_134(page: ConfirmPage) -> None:
    """#179: #134's AC2. The added error and buttons lines leave nothing in default mode. A
    fixed ``csrf_token`` stands in for the request's (masked afresh on every render)."""
    context = {
        "page": page,
        "has_emphasis": False,
        "idempotency_key": "k1",
        "next_url": "/back/",
        "csrf_token": "fixed-token",
    }
    before = engines["django"].from_string(PRE_134_CONFIRM).render(context)
    assert get_template("console/_confirm.html").render(context) == before
