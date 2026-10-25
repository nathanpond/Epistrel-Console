"""The shared confirm-page helpers (#43's convention, built in #45)."""

from typing import Any

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from console.confirm import ConfirmPage, confirm_action, posted_idempotency_key, safe_next

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
