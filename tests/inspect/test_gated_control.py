"""Convention 22's greyed control (#188): ``console/_gated_control.html`` (the visible reason) and
the ``gate_attrs`` filter (``disabled``, ``aria-describedby`` and a backtick-free ``title``)."""

from django.template import Context, Template
from django.template.loader import render_to_string

from console.inspect import PERCEPTION, UNREAD, Unavailable, unavailable_for
from console.templatetags.inspect_tags import gate_attrs

PERCEPTION_FULL = Unavailable(
    PERCEPTION,
    "perception.level",
    "Needs `perception.level: full` (this story runs `co_location`)",
)


def partial(gate: Unavailable | None, gate_id: str = "gate-x") -> str:
    return render_to_string(
        "console/_gated_control.html", {"gate": gate, "gate_id": gate_id}
    ).strip()


def test_the_partial_renders_nothing_for_an_open_gate() -> None:
    assert partial(None) == ""


def test_the_partial_renders_the_reason_with_code_spans_and_the_id() -> None:
    assert partial(PERCEPTION_FULL) == (
        '<small class="gate-reason" id="gate-x">Needs <code>perception.level: full</code> '
        "(this story runs <code>co_location</code>)</small>"
    )


def test_the_partial_without_an_id_and_escapes_the_reason() -> None:
    gate = Unavailable("", None, "Needs `<b>` & more")
    assert partial(gate, "") == (
        '<small class="gate-reason">Needs <code>&lt;b&gt;</code> &amp; more</small>'
    )
    assert partial(UNREAD) == (
        '<small class="gate-reason" id="gate-x">'
        "Couldn&#x27;t read this story&#x27;s configuration — reload</small>"
    )


def test_gate_attrs() -> None:
    assert gate_attrs(PERCEPTION_FULL, "gate-x") == (
        ' disabled aria-describedby="gate-x" '
        'title="Needs perception.level: full (this story runs co_location)"'
    )
    assert gate_attrs(None, "gate-x") == ""
    assert gate_attrs(PERCEPTION_FULL, "") == (
        ' disabled title="Needs perception.level: full (this story runs co_location)"'
    )
    assert gate_attrs(Unavailable("", None, 'a "quote" <b>'), "g") == (
        ' disabled aria-describedby="g" title="a &quot;quote&quot; &lt;b&gt;"'
    )


def test_a_greyed_control_in_a_template() -> None:
    template = Template(
        '{% load inspect_tags %}<select name="x"{{ gate|gate_attrs:"gate-x" }}></select>'
        '{% include "console/_gated_control.html" with gate=gate gate_id="gate-x" %}'
    )
    gate = unavailable_for("multi_character")
    shown = template.render(Context({"gate": gate}))
    assert shown.startswith('<select name="x" disabled aria-describedby="gate-x" title="')
    assert '<small class="gate-reason" id="gate-x">' in shown
    assert "<code>multi_character.renders: per_character</code>" in shown
    open_gate = template.render(Context({"gate": None}))
    assert open_gate == '<select name="x"></select>'
