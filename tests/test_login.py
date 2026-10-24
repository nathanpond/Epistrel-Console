"""Everything behind the operator login (#43): who may sign in, what stays public, logout."""

from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from tests.conftest import OPERATOR_PASSWORD

LOGIN = "/accounts/login/"
SIGNED_IN_PAGES = ["/", "/stories/", "/operations/", "/models/", "/guardrails/ratings/", "/engine/"]


@pytest.mark.django_db
@pytest.mark.parametrize("path", SIGNED_IN_PAGES)
def test_anonymous_pages_redirect_to_login(client: Client, path: str) -> None:
    response = client.get(path)
    # Complement: none of them renders for an anonymous visitor.
    assert response.status_code == 302
    assert response["Location"] == f"{LOGIN}?next={path}"


@pytest.mark.django_db
def test_anonymous_admin_redirects_to_the_admin_login(client: Client) -> None:
    # Django's admin views are exempt from LoginRequiredMiddleware and send anonymous visitors to
    # their own login, which uses the operator form (test_staff_only_account_cannot_use_...).
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response["Location"] == "/admin/login/?next=/admin/"


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/health", LOGIN, "/admin/login/"])
def test_public_pages_answer_anonymously(client: Client, path: str) -> None:
    assert client.get(path).status_code == 200


@pytest.mark.django_db
def test_static_files_are_served_anonymously(client: Client) -> None:
    response = client.get("/static/console/htmx.min.js")
    assert response.status_code == 200
    body = response.getvalue()
    assert body.startswith(b"var htmx=")


@pytest.mark.django_db
def test_login_page_has_no_shell(client: Client, badge_probe_calls: list[int]) -> None:
    html = client.get(LOGIN).content.decode()
    assert 'name="username"' in html
    assert "Log out" not in html
    assert "Engine:" not in html
    assert badge_probe_calls == []  # the badge never probes for an anonymous page
    assert "console/htmx.min.js" in html  # htmx loads on every page, the login page included


def sign_in(client: Client, username: str, password: str, next_: str = "") -> Any:
    data = {"username": username, "password": password}
    url = f"{LOGIN}?next={next_}" if next_ else LOGIN
    if next_:
        data["next"] = next_
    return client.post(url, data)


def test_operator_signs_in_and_sees_the_shell(client: Client, operator: User) -> None:
    response = sign_in(client, "nate", OPERATOR_PASSWORD)
    assert response.status_code == 302
    assert response["Location"] == "/"
    html = client.get("/stories/").content.decode()
    assert '<span class="operator">nate</span>' in html
    assert "Log out" in html
    for label in ("Stories", "Operations", "Models", "Guardrails", "Engine"):
        assert f">{label}</a>" in html


def test_sign_in_returns_to_next(client: Client, operator: User) -> None:
    response = sign_in(client, "nate", OPERATOR_PASSWORD, next_="/models/")
    assert response["Location"] == "/models/"


@pytest.mark.parametrize(
    "flags",
    [
        {"is_staff": True, "is_superuser": False},
        {"is_staff": False, "is_superuser": False},
    ],
)
def test_non_operator_is_refused_and_stays_anonymous(
    client: Client, db: None, flags: dict[str, bool]
) -> None:
    User.objects.create_user(
        "staffer",
        password=OPERATOR_PASSWORD,
        is_staff=flags["is_staff"],
        is_superuser=flags["is_superuser"],
    )
    response = sign_in(client, "staffer", OPERATOR_PASSWORD)
    assert response.status_code == 200
    assert "This account is not an operator." in response.content.decode()
    # Complement: not logged in.
    assert "_auth_user_id" not in client.session
    assert client.get("/").status_code == 302


def test_inactive_superuser_cannot_sign_in(client: Client, operator: User) -> None:
    operator.is_active = False
    operator.save()
    response = sign_in(client, "nate", OPERATOR_PASSWORD)
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_wrong_password_is_refused(client: Client, operator: User) -> None:
    response = sign_in(client, "nate", "wrong")
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_staff_only_account_cannot_use_the_admin_login(client: Client, db: None) -> None:
    User.objects.create_user("staffer", password=OPERATOR_PASSWORD, is_staff=True)
    response = client.post(
        "/admin/login/?next=/admin/",
        {"username": "staffer", "password": OPERATOR_PASSWORD, "next": "/admin/"},
    )
    assert response.status_code == 200
    assert "This account is not an operator." in response.content.decode()
    assert "_auth_user_id" not in client.session


def test_staff_only_session_is_refused_by_the_admin(client: Client, db: None) -> None:
    staffer = User.objects.create_user("staffer", password=OPERATOR_PASSWORD, is_staff=True)
    client.force_login(staffer)
    assert client.get("/admin/").status_code == 302  # to the admin login, not the index


def test_operator_reaches_the_admin(operator_client: Client) -> None:
    assert operator_client.get("/admin/").status_code == 200


def test_logout_by_post_returns_to_login(operator_client: Client) -> None:
    response = operator_client.post("/accounts/logout/")
    assert response.status_code == 302
    assert response["Location"] == LOGIN
    assert operator_client.get("/").status_code == 302


def test_logout_by_get_is_refused(operator_client: Client) -> None:
    assert operator_client.get("/accounts/logout/").status_code == 405
    # Complement: still signed in.
    assert operator_client.get("/stories/").status_code == 200


def test_no_password_reset_or_signup_routes(operator_client: Client) -> None:
    for path in ("/accounts/password_change/", "/accounts/password_reset/", "/accounts/signup/"):
        assert operator_client.get(path).status_code == 404
