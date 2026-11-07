"""The shared confirm-page helpers (#43's convention, built in #45)."""

import dataclasses
from typing import Any

import pytest
from django.http import HttpRequest, HttpResponse
from django.template import engines
from django.template.loader import get_template
from django.test import RequestFactory

from console.confirm import (
    TYPED_NAME_MISMATCH,
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
    typed_name_for,
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
    # The incomplete-confirmation notice, not the bare word (the CSS has `.badge.incomplete`).
    assert "This confirmation was incomplete" not in html
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


# ``_confirm.html`` as it stood before #235's D8 extensions: a page that sets none of them renders
# it byte for byte.
PRE_235_CONFIRM = """<div class="confirm">
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
    {% if page.fields_template %}{% include page.fields_template %}{% endif %}{% if page.error %}
    <p class="errorlist confirm-error">{{ page.error }}</p>{% endif %}
    <button type="submit"{% if page.danger %} class="danger"{% endif %}>{{ page.confirm_label }}</button>{% if page.buttons_template %}
    {% include page.buttons_template %}{% endif %}
    <a href="{{ next_url }}">Cancel</a>
  </form>
</div>
"""  # noqa: E501


@pytest.mark.parametrize(
    "page",
    [
        PAGE,
        ConfirmPage(title="T", question="Drop it?", confirm_label="Drop", cancel_url="/b/",
                    lines=("One",), facts=(("Id", "x1"), ("Name", "<b>")), hidden={"a": "1"},
                    danger=True, error="Refused", buttons_template="console/_confirm.html"),
    ],
)  # fmt: skip
def test_a_page_without_the_extensions_renders_as_before_235(page: ConfirmPage) -> None:
    """#235's complement: the defaults of ``blocked``, ``fact_urls`` and ``error_url``/
    ``error_link`` leave main's markup unchanged."""
    if page.buttons_template:
        page = dataclasses.replace(page, buttons_template="")
    context = {
        "page": page,
        "has_emphasis": False,
        "idempotency_key": "k1",
        "next_url": "/back/",
        "csrf_token": "fixed-token",
    }
    before = engines["django"].from_string(PRE_235_CONFIRM).render(context)
    assert get_template("console/_confirm.html").render(context) == before


def _render(page: ConfirmPage) -> str:
    context = {"page": page, "has_emphasis": False, "idempotency_key": "k1", "next_url": "/b/",
               "csrf_token": "t"}  # fmt: skip
    return get_template("console/_confirm.html").render(context)


def test_blocked_renders_a_warning_and_a_disabled_submit() -> None:
    page = ConfirmPage(title="T", question="Fix?", confirm_label="Fix", cancel_url="/b/",
                       danger=True, error="Refused", blocked="Only the latest turn can be fixed",
                       hidden={"a": "1"})  # fmt: skip
    html = _render(page)
    assert '<p class="warning">Only the latest turn can be fixed</p>' in html
    assert '<button type="submit" disabled class="danger">Fix</button>' in html
    assert '<button type="submit" class="danger">' not in html  # no live submit
    assert html.index("confirm-error") < html.index('class="warning"') < html.index("<button")
    assert '<input type="hidden" name="a" value="1">' in html
    assert '<a href="/b/">Cancel</a>' in html


@pytest.mark.django_db
@pytest.mark.parametrize("confirmed", [False, True])
def test_a_blocked_page_never_performs(
    rf: RequestFactory, admin_user: Any, confirmed: bool
) -> None:
    perform = Recorder()
    page = dataclasses.replace(PAGE, blocked="Archived")
    data = {"idempotency_key": "k9", "confirmed": "1"}
    response = confirm_action(
        _req(rf, admin_user, "POST", data), page, perform, require_confirmed=confirmed
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert perform.calls == []
    assert '<p class="warning">Archived</p>' in html
    assert 'name="idempotency_key" value="k9"' in html


def test_fact_urls_link_the_named_fact_only() -> None:
    page = ConfirmPage(title="T", question="Q?", confirm_label="Go", cancel_url="/b/",
                       facts=(("Provider", "bank"), ("Outbox id", "01JM6BX")),
                       fact_urls={"Provider": "/engine/providers/bank/"})  # fmt: skip
    html = _render(page)
    assert '<dd><a href="/engine/providers/bank/"><code>bank</code></a></dd>' in html
    assert "<dd><code>01JM6BX</code></dd>" in html


def test_error_link_after_the_error() -> None:
    page = ConfirmPage(title="T", question="Q?", confirm_label="Go", cancel_url="/b/",
                       error="Needs `states`", error_url="/stories/S/config/",
                       error_link="Config")  # fmt: skip
    html = _render(page)
    assert (
        '<p class="errorlist confirm-error">Needs <code>states</code> \u2014 '
        '<a href="/stories/S/config/">Config</a></p>'
    ) in html
    # #256: backtick spans become <code>; any other markup in the error stays escaped.
    escaped = _render(dataclasses.replace(page, error="<b>x</b> `y`"))
    assert "&lt;b&gt;x&lt;/b&gt; <code>y</code>" in escaped
    # Complement: without an error, or without a label, main's markup.
    assert "Config</a>" not in _render(dataclasses.replace(page, error=""))
    assert '<a href="/stories/S/config/">' not in _render(dataclasses.replace(page, error_link=""))


# --- E9's typed-name confirm (#295) ---------------------------------------------------------------

# ``_confirm.html`` as main rendered it before #295 (captured from main with a fixed key and CSRF
# token): a page with ``typed_name`` "" renders it byte for byte.
MAIN_PLAIN = "\n".join(
    [
        '<div class="confirm">',
        '  <p class="question">Do <em>it</em>?</p>',
        "  ",
        "  ",
        '  <form method="post">',
        '    <input type="hidden" name="csrfmiddlewaretoken" value="CSRF">',
        '    <input type="hidden" name="idempotency_key" value="KEY1">',
        '    <input type="hidden" name="next" value="/back/">',
        "    ",
        "    ",
        '    <button type="submit">Go</button>',
        '    <a href="/back/">Cancel</a>',
        "  </form>",
        "</div>",
        "",
    ]
)
MAIN_FULL = "\n".join(
    [
        '<div class="confirm">',
        '  <p class="question">Delete?</p>',
        "  <p>a line</p>",
        "  ",
        "  <dl>",
        '    <dt>Story id</dt><dd><a href="/s/"><code>X1</code></a></dd>',
        "  </dl>",
        "  ",
        '  <form method="post">',
        '    <input type="hidden" name="csrfmiddlewaretoken" value="CSRF">',
        '    <input type="hidden" name="idempotency_key" value="KEY1">',
        '    <input type="hidden" name="next" value="/b/">',
        '    <input type="hidden" name="h" value="v">',
        "    ",
        "    ",
        '    <p class="errorlist confirm-error">Bad <code>x</code> — <a href="/c/">Config</a></p>',
        '    <p class="warning">Blocked</p>',
        '    <button type="submit" disabled class="danger">Delete</button>',
        '    <a href="/b/">Cancel</a>',
        "  </form>",
        "</div>",
        "",
    ]
)


def _main_context(page: ConfirmPage) -> dict[str, Any]:
    before, _, after = page.question.partition("{name}")
    return {"page": page, "question_before": before, "question_after": after,
            "has_emphasis": "{name}" in page.question, "idempotency_key": "KEY1",
            "next_url": page.cancel_url, "csrf_token": "CSRF"}  # fmt: skip


def test_a_page_without_a_typed_name_renders_as_on_main() -> None:
    """**Complement:** ``typed_name`` defaults to "" and leaves main's HTML unchanged."""
    full = ConfirmPage(title="T", question="Delete?", confirm_label="Delete", cancel_url="/b/",
                       lines=("a line",), facts=(("Story id", "X1"),), hidden={"h": "v"},
                       danger=True, error="Bad `x`", blocked="Blocked",
                       fact_urls={"Story id": "/s/"}, error_url="/c/",
                       error_link="Config")  # fmt: skip
    template = get_template("console/_confirm.html")
    assert template.render(_main_context(PAGE)) == MAIN_PLAIN
    assert template.render(_main_context(full)) == MAIN_FULL


TYPED = dataclasses.replace(PAGE, typed_name="Harbour", typed_label="Harbour")


def test_the_typed_input_names_the_story_and_has_no_value() -> None:
    html = get_template("console/_confirm.html").render(_main_context(TYPED))
    assert (
        '<label>Type <strong>Harbour</strong> to confirm <input name="typed_name" '
        'autocomplete="off"></label>'
    ) in html
    assert 'name="typed_name" value' not in html
    assert html.index('name="typed_name"') < html.index("<button")


@pytest.mark.parametrize("name", [None, "", "  "])
def test_a_story_without_a_name_types_its_id(name: str | None) -> None:
    page = typed_name_for(PAGE, name, "01JM7ST0000000000000000001")
    assert (page.typed_name, page.typed_label) == ("01JM7ST0000000000000000001", "")
    html = get_template("console/_confirm.html").render(_main_context(page))
    assert "Type the story's id <strong>01JM7ST0000000000000000001</strong> to confirm" in html
    named = typed_name_for(PAGE, " Harbour ", "S")
    assert (named.typed_name, named.typed_label) == ("Harbour", "Harbour")


@pytest.mark.django_db
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("typed", ["Harbour", " Harbour "])
def test_the_exact_name_performs_once(
    rf: RequestFactory, admin_user: Any, confirmed: bool, typed: str
) -> None:
    perform = Recorder()
    data = {"idempotency_key": "k7", "confirmed": "1", "typed_name": typed}
    response = confirm_action(
        _req(rf, admin_user, "POST", data), TYPED, perform, require_confirmed=confirmed
    )
    assert response.content == b"done"
    assert perform.calls == ["k7"]


@pytest.mark.django_db
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("typed", ["harbour", "", None])
def test_a_mismatch_rerenders_and_never_performs(
    rf: RequestFactory, admin_user: Any, confirmed: bool, typed: str | None
) -> None:
    perform = Recorder()
    data = {"idempotency_key": "k8", "confirmed": "1"}
    if typed is not None:
        data["typed_name"] = typed
    response = confirm_action(
        _req(rf, admin_user, "POST", data), TYPED, perform, require_confirmed=confirmed
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert perform.calls == []
    assert TYPED_NAME_MISMATCH == "That isn't the story's name — type it exactly as shown"
    assert "That isn&#x27;t the story&#x27;s name — type it exactly as shown" in html
    assert 'name="idempotency_key" value="k8"' in html
    assert 'value="harbour"' not in html  # never echoed back


@pytest.mark.django_db
def test_the_first_confirmed_mode_render_is_clean(rf: RequestFactory, admin_user: Any) -> None:
    """The name is checked only on a POST that would otherwise perform: the originating form's
    POST (no ``confirmed``) re-renders the page without the mismatch error."""
    perform = Recorder()
    response = confirm_action(
        _req(rf, admin_user, "POST", {"idempotency_key": "k6"}), TYPED, perform,
        require_confirmed=True,
    )  # fmt: skip
    assert perform.calls == []
    assert "type it exactly" not in response.content.decode()
