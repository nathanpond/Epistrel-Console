"""How a multi-character turn was composed (#203): ``console.composition`` and its panel, from
hand-built responses."""

import json
import re
from typing import Any

from django.template.loader import render_to_string

from console.composition import Composition, composition
from console.inspect import short_id
from console.scene import entity_url
from tests.play.helpers import MARA, STORY, TOBIN
from tests.play.test_cast_participation import JENNI

LABELS = {MARA: "Mara (player)", TOBIN: "Tobin", JENNI: "Jenni"}
KNIFE = "01JB2Z8Y5Q3W6K9M1N4P7R0KN1"
OUTSIDER = "01JB2Z8Y5Q3W6K9M1N4P7R0ZZ9"
OTHER = "01JB2Z8Y5Q3W6K9M1N4P7R0ZZ8"


def answer(**fields: Any) -> dict[str, Any]:
    return {"turn": 3, **fields}


def build(response: dict[str, Any], **options: Any) -> Composition | None:
    kwargs: dict[str, Any] = {"structured": True, "turn": 3, "anchors": frozenset()}
    kwargs.update(options)
    return composition(response, LABELS, STORY, **kwargs)


def panel(response: dict[str, Any], **options: Any) -> str:
    found = build(response, **options)
    assert found is not None
    return render_to_string("console/play/_composition.html", {"c": found}).strip()


def headings(html: str) -> list[str]:
    return re.findall(r"<h5>(.*?)</h5>", html)


def more_of(html: str) -> Any:
    match = re.search(r'<details class="more"><summary>More</summary><pre>(.*?)</pre>', html, re.S)
    assert match, "no More"
    return json.loads(match.group(1).replace("&quot;", '"').replace("&#x27;", "'"))


ROLES = {
    JENNI: {"role": "primary", "source": "request"},
    TOBIN: {"role": "reactive", "source": "fixed"},
}
FULL = answer(
    roles=ROLES,
    deferred_characters=[{"character_id": TOBIN, "reason": "budget", "fixed": True}],
    reduced_characters=[JENNI],
    escalation_deferred=[{"character_id": TOBIN, "reason": "fixed_role", "needed": "speech"}],
    adjudications=[{"object": KNIFE, "winner": JENNI, "loser": TOBIN, "rule": "request_order"}],
    reconcile=[
        {"proposition": "holds:knife", "winner_beat": 2, "loser_beat": 4, "rule": "earlier_beat"}
    ],
)


def test_each_subsection_renders_only_when_present() -> None:
    html = panel(FULL)
    assert html.startswith('<section class="composition">\n<h4>Composition</h4>')
    assert headings(html) == [
        "Roles",
        "Deferred to a later turn",
        "Narrated instead of rendered",
        "Reactions that needed a render",
        "Adjudications (simultaneous)",
        "Reconcile",
    ]
    assert "More" not in html
    for name in (
        "roles",
        "deferred_characters",
        "reduced_characters",
        "escalation_deferred",
        "adjudications",
        "reconcile",
    ):
        alone = panel(answer(**{name: FULL[name]}))
        assert len(headings(alone)) == 1, name
    # None of the six (interaction_mode and order don't count): no panel.
    assert build(answer(interaction_mode="sequential", order=[JENNI, TOBIN])) is None
    # Not a turn answer: no panel.
    assert build({"roles": ROLES}) is None


def test_role_sources_wording_and_raw_title() -> None:
    roles = {
        TOBIN: {"role": "reactive", "source": "default"},
        MARA: {"role": "silent", "source": "fixed"},
        JENNI: {"role": "primary", "source": "request"},
        OUTSIDER: {"role": "primary", "source": "escalated"},
        OTHER: {"role": "reactive", "source": "reduced"},
    }
    html = panel(answer(roles=roles))
    assert "<th>Character</th><th>Role</th><th>Source</th>" in html
    rows = re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", html)
    # Cast order (the labels' order), then ids outside the cast in the response's key order.
    assert rows == [
        (
            "Mara (player)",
            '<span title="silent">Silent</span>',
            '<span title="fixed">fixed role</span>',
        ),
        (
            "Tobin",
            '<span title="reactive">Reactive</span>',
            '<span title="default">stored role</span>',
        ),
        (
            "Jenni",
            '<span title="primary">Primary</span>',
            '<span title="request">this request</span>',
        ),
        (
            f'<span title="{OUTSIDER}">{short_id(OUTSIDER)}</span>',
            '<span title="primary">Primary</span>',
            '<span title="escalated">escalated (its reaction needed a render)</span>',
        ),
        (
            f'<span title="{OTHER}">{short_id(OTHER)}</span>',
            '<span title="reactive">Reactive</span>',
            '<span title="reduced">reduced (budget planning)</span>',
        ),
    ]


def test_escalation_needed_and_reason_wording() -> None:
    entries = [
        {"character_id": TOBIN, "reason": "fixed_role", "needed": "speech"},
        {"character_id": JENNI, "reason": "budget", "needed": "decision"},
        {"character_id": TOBIN, "reason": "budget", "needed": "effect"},
        {"character_id": JENNI, "reason": "fixed_role", "needed": None},
        {"character_id": JENNI, "reason": "fixed_role", "needed": 7},  # wrong-typed: absent
    ]
    html = panel(answer(escalation_deferred=entries))
    lines = re.findall(r"<li>(.*?)</li>", html)
    fixed = '<span title="fixed_role">its role is fixed</span>'
    budget = '<span title="budget">the turn&#x27;s budget ran out</span>'
    tail = " — bring them in on a later turn"

    def line(name: str, needed: str, reason: str) -> str:
        return f"{name}'s reaction needed {needed} but stayed reactive: {reason}{tail}"

    assert lines == [
        line("Tobin", '<span title="speech">speech</span>', fixed),
        line("Jenni", '<span title="decision">a decision</span>', budget),
        line("Tobin", '<span title="effect">an effect</span>', budget),
        line("Jenni", "something", fixed),
        line("Jenni", "something", fixed),
    ]
    assert "More" not in html


def test_deferred_and_reduced_lines() -> None:
    html = panel(
        answer(
            deferred_characters=[
                {"character_id": TOBIN, "reason": "budget", "fixed": True},
                {"character_id": JENNI, "reason": "budget", "fixed": False},
                {"character_id": JENNI, "reason": "budget", "fixed": "yes"},  # wrong-typed
            ],
            reduced_characters=[JENNI, OUTSIDER],
        )
    )
    budget = '<span title="budget">the turn&#x27;s budget ran out</span>'
    deferred = html.split('<ul class="composition-deferred">')[1].split("</ul>")[0]
    assert re.findall(r"<li>(.*?)</li>", deferred) == [
        f"Tobin — {budget}; fixed primary",
        f"Jenni — {budget}",
        f"Jenni — {budget}",
    ]
    reduced = html.split('<ul class="composition-reduced">')[1].split("</ul>")[0]
    assert re.findall(r"<li>(.*?)</li>", reduced) == [
        "Jenni",
        f'<span title="{OUTSIDER}">{short_id(OUTSIDER)}</span>',
    ]


def test_adjudication_object_links_entity_on_structured() -> None:
    html = panel(FULL)
    url = entity_url(STORY, KNIFE, 3, "omniscient")
    assert url is not None
    assert "<th>Contested object</th><th>Stood</th><th>Became an attempt</th><th>Rule</th>" in html
    cell = (
        f'<a href="{url.replace("&", "&amp;")}"><code title="{KNIFE}">{short_id(KNIFE)}</code></a>'
    )
    assert f"<td>{cell}</td>" in html
    assert "as_of=3" in url
    assert "viewpoint=omniscient" in url
    # A non-structured turn, or an unknown turn: the short id, unlinked.
    for options in ({"structured": False}, {"turn": None}):
        plain = panel(FULL, **options)
        assert f'<td><code title="{KNIFE}">{short_id(KNIFE)}</code></td>' in plain
        assert "/entities/" not in plain


def test_adjudication_winner_and_loser_are_labelled() -> None:
    entries = [
        {"object": KNIFE, "winner": JENNI, "loser": TOBIN, "rule": "request_order"},
        {"object": KNIFE, "winner": OUTSIDER, "loser": JENNI, "rule": "director"},
        {"object": KNIFE, "winner": TOBIN, "loser": JENNI, "rule": "state"},
    ]
    html = panel(answer(adjudications=entries), structured=False)
    rows = re.findall(
        r"<tr><td><code[^>]*>[^<]*</code></td><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", html
    )
    assert rows == [
        (
            "Jenni",
            "Tobin",
            '<span title="request_order">request order (earlier-named primary)</span>',
        ),
        (
            f'<span title="{OUTSIDER}">{short_id(OUTSIDER)}</span>',
            "Jenni",
            '<span title="director">director</span>',
        ),
        ("Tobin", "Jenni", '<span title="state">state</span>'),
    ]


def test_reconcile_beats_link_anchors() -> None:
    html = panel(FULL, anchors=frozenset({2, 4}))
    assert "<th>Proposition</th><th>Stood</th><th>Rewritten</th><th>Rule</th>" in html
    assert (
        '<tr><td><code>holds:knife</code></td><td><a href="#beat-2">beat 2</a></td>'
        '<td><a href="#beat-4">beat 4</a></td>'
        '<td><span title="earlier_beat">earlier beat (sequential)</span></td></tr>'
    ) in html
    rules = {
        "author": "author (narrator or the player&#x27;s input)",
        "request_order": "request order (simultaneous)",
        "dedupe": "duplicate dropped",
    }
    for rule, text in rules.items():
        entry = {"proposition": "p", "winner_beat": 1, "loser_beat": 2, "rule": rule}
        assert f'<span title="{rule}">{text}</span>' in panel(answer(reconcile=[entry]))


def test_reconcile_beat_without_anchor_is_plain() -> None:
    html = panel(FULL, anchors=frozenset({2}))
    assert '<td><a href="#beat-2">beat 2</a></td><td>beat 4</td>' in html
    assert "#beat-4" not in html


def test_non_ulid_object_is_a_short_id() -> None:
    entry = {"object": "the-kitchen-knife", "winner": JENNI, "loser": TOBIN, "rule": "state"}
    html = panel(answer(adjudications=[entry]))
    assert (
        f'<td><code title="the-kitchen-knife">{short_id("the-kitchen-knife")}</code></td>' in html
    )
    assert "<a href" not in html


def test_unknown_enum_values_are_humanised() -> None:
    html = panel(
        answer(
            roles={JENNI: {"role": "background_extra", "source": "scenario_cast"}},
            deferred_characters=[{"character_id": TOBIN, "reason": "off_stage", "fixed": False}],
            escalation_deferred=[{"character_id": TOBIN, "reason": "too_far", "needed": "gesture"}],
            adjudications=[{"object": KNIFE, "winner": JENNI, "loser": TOBIN, "rule": "coin_toss"}],
            reconcile=[{"proposition": "p", "winner_beat": 1, "loser_beat": 2, "rule": "latest"}],
        )
    )
    for raw, text in (
        ("background_extra", "Background extra"),
        ("scenario_cast", "Scenario cast"),
        ("off_stage", "Off stage"),
        ("too_far", "Too far"),
        ("coin_toss", "Coin toss"),
        ("latest", "Latest"),
    ):
        assert f'<span title="{raw}">{text}</span>' in html, raw
    assert 'reaction needed <span title="gesture">Gesture</span> but stayed reactive' in html


def test_malformed_entry_is_dropped_and_kept_raw() -> None:
    bad_role = {"role": 1, "source": "request"}
    bad_adjudication = {"object": KNIFE, "winner": JENNI, "rule": "state"}  # no loser
    bad_reconcile = {"proposition": "p", "winner_beat": "2", "loser_beat": 4, "rule": "dedupe"}
    html = panel(
        answer(
            roles={JENNI: {"role": "primary", "source": "request"}, TOBIN: bad_role},
            reduced_characters=[JENNI, 7],
            adjudications=[
                bad_adjudication,
                {"object": KNIFE, "winner": JENNI, "loser": TOBIN, "rule": "state"},
            ],
            reconcile=[bad_reconcile],
            escalation_deferred=["Tobin"],
        )
    )
    assert headings(html) == [
        "Roles",
        "Narrated instead of rendered",
        "Adjudications (simultaneous)",
    ]
    assert html.count("<tr><td>") == 2  # Jenni's role and the good adjudication
    assert html.rindex('<details class="more">') > html.rindex("</table>")  # at the end
    more = more_of(html)
    assert list(more) == [
        "roles",
        "reduced_characters",
        "escalation_deferred",
        "adjudications",
        "reconcile",
    ]
    assert more == {
        "roles": {TOBIN: bad_role},
        "reduced_characters": [7],
        "escalation_deferred": ["Tobin"],
        "adjudications": [bad_adjudication],
        "reconcile": [bad_reconcile],
    }


def test_id_outside_cast_is_short_id() -> None:
    html = panel(answer(reduced_characters=[OUTSIDER]))
    assert f'<li><span title="{OUTSIDER}">{short_id(OUTSIDER)}</span></li>' in html
    assert OUTSIDER not in html.replace(f'title="{OUTSIDER}"', "")
    assert "<a " not in html  # names are never links


def test_wholly_malformed_field_shows_only_more() -> None:
    html = panel(answer(roles=[JENNI], reconcile={"p": 1}))
    assert headings(html) == []
    assert "<h4>Composition</h4>" in html
    assert more_of(html) == {"roles": [JENNI], "reconcile": {"p": 1}}
    # Every field malformed still gives a panel.
    assert build(answer(adjudications="knife")) is not None


def test_null_fields_count_as_absent() -> None:
    """The live path's ``asdict`` shape: every omitted field is ``null`` or empty."""
    live = answer(
        roles={},
        escalation_deferred=[],
        deferred_characters=None,
        reduced_characters=[],
        adjudications=None,
        reconcile=[],
        interaction_mode=None,
    )
    assert build(live) is None
    html = panel({**live, "roles": {JENNI: {"role": "primary", "source": "request"}}})
    assert headings(html) == ["Roles"]
    assert "More" not in html


def test_bool_beat_number_drops_the_entry() -> None:
    entry = {"proposition": "p", "winner_beat": True, "loser_beat": 2, "rule": "dedupe"}
    html = panel(answer(reconcile=[entry]), anchors=frozenset({1, 2}))
    assert headings(html) == []
    assert more_of(html) == {"reconcile": [entry]}


def test_entries_are_shown_as_given_duplicates_included() -> None:
    html = panel(answer(reduced_characters=[JENNI, JENNI]))
    assert html.count("<li>Jenni</li>") == 2
