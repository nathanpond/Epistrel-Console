from ulid import ULID

from console.ids import new_ulid


def test_new_ulid_is_a_fresh_valid_ulid_each_call() -> None:
    first, second = new_ulid(), new_ulid()
    assert len(first) == 26
    assert str(ULID.from_str(first)) == first
    # Complement: one per render, never reused.
    assert first != second
