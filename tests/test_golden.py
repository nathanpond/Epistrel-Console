"""E24's golden normaliser (#296, ``tests/golden.py``)."""

from tests.golden import CSRF_PLACEHOLDER, IDEMPOTENCY_PLACEHOLDER, normalise_html

FORM = (
    '<body hx-headers:inherited=\'{{"X-CSRFToken": "{csrf}"}}\'>\n'
    '  <form method="post">\n'
    '    <input type="hidden" name="csrfmiddlewaretoken" value="{csrf}">\n'
    '    <input type="hidden" name="idempotency_key" value="{key}">\n'
    '    <input type="text" name="title" value="{title}">\n'
    "    <button>{label}</button>\n"
    "  </form>\n"
    "</body>\n"
)
KEY_A = "01JM7RW0000000000000000001"
KEY_B = "01JM7RW0000000000000000002"


def form(
    *, csrf: str = "tok-a", key: str = KEY_A, title: str = "Harbour", label: str = "Save"
) -> str:
    return FORM.format(csrf=csrf, key=key, title=title, label=label)


def test_whitespace_between_tags_collapses() -> None:
    assert normalise_html("<p>\n  <a>x</a>  \n <b>y z</b>\n</p>\n") == "<p><a>x</a><b>y z</b></p>"


def test_csrf_and_idempotency_keys_normalise_equal() -> None:
    one = normalise_html(form())
    other = normalise_html(form(csrf="tok-b", key=KEY_B))
    assert one == other
    assert f'name="csrfmiddlewaretoken" value="{CSRF_PLACEHOLDER}"' in one
    assert f'"X-CSRFToken": "{CSRF_PLACEHOLDER}"' in one
    assert f'name="idempotency_key" value="{IDEMPOTENCY_PLACEHOLDER}"' in one
    assert "tok-a" not in one
    assert KEY_A not in one


def test_a_changed_attribute_or_text_still_differs() -> None:
    """**Complement:** only the token and the key are placeholders."""
    base = normalise_html(form())
    assert normalise_html(form(title="Ferry")) != base
    assert normalise_html(form(label="Apply")) != base
    assert normalise_html(form().replace('method="post"', 'method="get"')) != base
    # A ULID elsewhere (not an idempotency key) is kept.
    assert normalise_html(form(title=KEY_B)) != normalise_html(form(title=KEY_A))
