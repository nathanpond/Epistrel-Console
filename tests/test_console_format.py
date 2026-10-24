from typing import Any

import pytest

from console.templatetags.console_format import check_value


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("ok", "ok"),
        (None, "—"),
        (0, "0"),
        (1.5, "1.5"),
        (True, "yes"),
        (False, "no"),
        ({}, "—"),
        ([], "—"),
        ({"alive": 2, "last_heartbeat_s": 2.2}, "alive 2, last heartbeat 2.2 s"),
        ({"took_ms": 12, "worker_count": 3}, "took 12 ms, worker count 3"),
        ({"last_heartbeat_s": None}, "last heartbeat s —"),
        ({"inner": {"state_name": "ok"}}, "inner state name ok"),
        (["a", None, 3], "a, —, 3"),
    ],
)
def test_check_value(value: Any, text: str) -> None:
    assert check_value(value) == text
