import pytest

from console.redaction import MASK, is_redacted, redact


@pytest.mark.parametrize(
    "name", ["api_key", "API_KEY", "key", "secret", "password", "credential", "service_key"]
)
def test_redacted_names(name: str) -> None:
    assert is_redacted(name)


@pytest.mark.parametrize("name", ["idempotency_key", "api_key_fingerprint", "keys", 3])
def test_kept_names(name: object) -> None:
    # Complement: exact names only.
    assert not is_redacted(name)


def test_redact_recurses_and_copies() -> None:
    value = {"a": [{"Secret": "s", "ok": 1}], "authorization": "Bearer x", "n": None}
    assert redact(value) == {"a": [{"Secret": MASK, "ok": 1}], "authorization": MASK, "n": None}
    assert value["authorization"] == "Bearer x"
