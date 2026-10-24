from typing import Any

import pytest
from django.template import Context, Template

from console.templatetags.console_format import check_value, engine_time


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


# --- #43: engine_time and pretty_json ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("2026-10-20T08:15:30.123456Z", "2026-10-20 08:15:30 UTC"),
        ("2026-10-20T10:15:30+02:00", "2026-10-20 08:15:30 UTC"),
    ],
)
def test_engine_time(value: str, shown: str) -> None:
    html = str(engine_time(value))
    assert html == f'<time datetime="{value}" title="{value}">{shown}</time>'


def test_engine_time_none_and_garbage() -> None:
    assert engine_time(None) == "—"
    # Complement: an unparseable value is shown as given, escaped by the template engine.
    assert engine_time("<soon>") == "<soon>"
    assert "&lt;soon&gt;" in Template("{% load console_format %}{{ v|engine_time }}").render(
        Context({"v": "<soon>"})
    )


def test_pretty_json_in_a_template_is_escaped() -> None:
    out = Template("{% load console_format %}<pre>{{ v|pretty_json }}</pre>").render(
        Context({"v": {"name": "Zoë", "tag": "<b>"}})
    )
    assert out == (
        "<pre>{\n"
        "  &quot;name&quot;: &quot;Zoë&quot;,\n"
        "  &quot;tag&quot;: &quot;&lt;b&gt;&quot;\n"
        "}</pre>"
    )
