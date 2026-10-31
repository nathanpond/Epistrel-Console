"""C8 (#61): role and weight wording, the role select's options and the local errors."""

import pytest

from console.participation import (
    FIX_NEEDS_ROLE,
    PLAYER_NO_ROLE,
    ROLE_LABELS,
    WEIGHT_LABELS,
    WEIGHT_TITLES,
    participation_error,
    role_choices,
    role_text,
    weight_label,
)


def test_the_c8_constants() -> None:
    assert ROLE_LABELS == {"primary": "Primary", "reactive": "Reactive", "silent": "Silent"}
    assert WEIGHT_LABELS == {"major": "Major", "minor": "Minor"}
    assert FIX_NEEDS_ROLE == "Choose a role to fix it."
    assert PLAYER_NO_ROLE == "The player's character has no role."
    assert WEIGHT_TITLES["major"] == "Major: full partition and hot-memory budget"
    assert WEIGHT_TITLES["minor"] == (
        "Minor: sparse partition, scaled hot-memory budget, rendered only when named"
    )


@pytest.mark.parametrize(
    ("role", "fixed", "text"),
    [
        ("primary", False, "Primary"),
        ("reactive", True, "Reactive (fixed)"),
        ("silent", False, "Silent"),
        (None, True, "—"),
        (None, False, "—"),
        ("", True, "—"),
        ("background_only", False, "Background only"),
        ("background_only", True, "Background only (fixed)"),
    ],
)
def test_role_text(role: str | None, fixed: bool, text: str) -> None:
    assert role_text(role, fixed) == text


def test_weight_label() -> None:
    assert weight_label("major") == "Major"
    assert weight_label("minor") == "Minor"
    assert weight_label(None) == "—"


def test_role_choices() -> None:
    roles = [("primary", "Primary"), ("reactive", "Reactive"), ("silent", "Silent")]
    assert role_choices(engine_default=False) == roles
    assert role_choices(engine_default=True) == [("", "Engine default (reactive)"), *roles]


def test_the_local_errors() -> None:
    assert participation_error(None, True) == FIX_NEEDS_ROLE
    assert participation_error("primary", True) is None
    assert participation_error(None, False) is None
    assert participation_error("primary", False, is_player=True) == PLAYER_NO_ROLE
    assert participation_error(None, False, is_player=True) is None
