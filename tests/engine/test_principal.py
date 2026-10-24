import pytest
from django.contrib.auth.models import AnonymousUser, User

from console.engine.client import principal_for


def test_user_principal() -> None:
    assert principal_for(User(username="nate")) == "console:nate"


def test_anonymous_has_no_principal() -> None:
    with pytest.raises(ValueError, match="anonymous"):
        principal_for(AnonymousUser())


def test_empty_username_has_no_principal() -> None:
    with pytest.raises(ValueError, match="empty"):
        principal_for(User(username=""))
