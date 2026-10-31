"""Follow each beat of a structured turn from claim to verdict to repair (#147).

Unit tests drive :func:`console.history.beat_trace` and ``_beat_trace.html`` directly (the
schema-validating fake refuses malformed labels and spans, so those are tested here). Page tests
run the history browser and the play page against the fake Engine: the "Harbour" story (Mara the
player, Tobin), with structured beats built by :func:`beat`."""

import copy
import json
import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

import httpx
import pytest
from django.template.loader import render_to_string
from django.test import Client

from console.engine.models import (
    DetectedClaim,
    EvalHooks,
    HistoryItem,
    OperationRecord,
    ReadSet,
    RepairRung,
    Verdict,
)
from console.history import (
    LOADED,
    NO_HOOKS,
    NO_TRACE,
    NOT_LOADED,
    UNAVAILABLE,
    BeatTrace,
    beat_trace,
    repair_line,
    segments,
)
from console.turn_trace import TurnTrace, declared_beat
from console.views import history as history_views
from tests.fakes.engine import FakeEngine
from tests.history.helpers import HISTORY, STORY, STORY_PATH, VARIANTS, variants_json
from tests.history.test_lite_golden import lite_history
from tests.inspect.helpers import FIXTURES
from tests.play.helpers import MARA, TOBIN, answer_page, error_json, story_json

pytestmark = pytest.mark.django_db

OPERATION = "/operations/{operation_id}"
PREDICATES = "/stories/{story_id}/predicates"
URL = f"/stories/{STORY}/history/"
PR1 = "01JB2Z8Y5Q3W6K9M1N4P7R0PR1"  # located_at
PA1 = "01JB2Z8Y5Q3W6K9M1N4P7R0PA1"  # not a cast character
DROP = object()  # a key to leave out of an item
DASH = "\u2013"  # "(invalid span 12<en dash>40)"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return body


def op_id(turn: int) -> str:
    return f"01JB2Z8Y5Q3W6K9M1N4P7S{turn:04d}"


def proposition(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "subject_id": TOBIN,
        "predicate_id": PR1,
        "object_id": PA1,
        "polarity": "affirmed",
        "qualifiers": {},
        "valid_time": {"kind": "interval", "start": {"outer": 10, "inner": 12}, "end": None},
    }
    body.update(over)
    return body


SAY_LABELS = {"intent": "sincere", "belief_relation": "unknown", "objective_relation": "true"}


def beat(
    turn: int = 2, number: int | None = 2, text: str = "Tobin is 41.", **over: Any
) -> dict[str, Any]:
    """A structured ``beat_say`` by Tobin: two propositions (supported, novel), one detected
    claim over "41" ("age · contradicted")."""
    body: dict[str, Any] = {
        "turn": turn,
        "variant": 1,
        "beat": number,
        "kind": "beat_say",
        "actor_character_id": TOBIN,
        "render_character_ids": None,
        "channel": "say",
        "text": text,
        "operation_id": op_id(turn),
        "message_id": f"01JB2Z8Y5Q3W6K9M1N4P7M{turn:02d}{number or 0:02d}",
        "origin": "generated",
        "propositions": [
            {"proposition": proposition(), "verdict": "supported"},
            {"proposition": proposition(object_id=MARA), "verdict": "novel"},
        ],
        "effects": [],
        "labels": dict(SAY_LABELS),
        "detected": [{"start": 9, "end": 11, "category": "age", "verdict": "contradicted"}],
    }
    for key, value in over.items():
        if value is DROP:
            body.pop(key, None)
        else:
            body[key] = value
    return body


def item_of(body: dict[str, Any]) -> HistoryItem:
    parsed = HistoryItem.from_json(body)
    assert parsed is not None
    return parsed


def hooks(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "raw_render_event_ids": [],
        "verdicts": [
            {"claim_id": "d2.0", "kind": "supported", "escalated": False},
            {"claim_id": "d2.1", "kind": "novel", "escalated": True},
            {"claim_id": "d3.0", "kind": "contradicted", "escalated": True},
            {"claim_id": f"{op_id(2)}:0", "kind": "uncertain", "escalated": True},
        ],
        "repair_rungs": [{"beat": 2, "rung": 1, "accepted": True}, {"beat": 3, "rung": 4}],
        "model_calls_by_purpose": {},
        "latency_ms_by_stage": {},
    }
    body.update(over)
    return body


def operation(turn: int, eval_hooks: Any = DROP, **over: Any) -> dict[str, Any]:
    body = copy.deepcopy(fixture("operation_turn_structured"))
    body.update(id=op_id(turn), story_id=STORY, narrative_turn=turn, turn_variant=1)
    body["eval_hooks"] = hooks() if eval_hooks is DROP else eval_hooks
    body.update(over)
    return body


def trace_of(eval_hooks: Any = DROP, withheld_for: str | None = None) -> TurnTrace:
    record = OperationRecord.from_json(operation(2, eval_hooks))
    assert record is not None
    parsed = EvalHooks.parse(record.eval_hooks) if withheld_for is None else None
    return TurnTrace(record, parsed, ReadSet.parse(record.read_set), withheld_for)


def trace(body: dict[str, Any], turn_trace: TurnTrace | None = None, **kw: Any) -> BeatTrace:
    found = beat_trace(item_of(body), turn_trace, story_id=STORY, **kw)
    assert found is not None
    return found


def render(t: BeatTrace) -> str:
    return render_to_string("console/history/_beat_trace.html", {"t": t})


def table_rows(html: str, table: str) -> list[list[str]]:
    found = re.search(rf'<table class="{table}">(.*?)</table>', html, re.S)
    assert found is not None, table
    body = found.group(1).split("<tbody>", 1)[1]
    return [
        [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)]
        for r in re.findall(r"<tr>(.*?)</tr>", body, re.S)
    ]


class MarkNesting(HTMLParser):
    """Fails on a ``<mark>`` inside a ``<mark>`` or an unbalanced one."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.titles: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "mark":
            assert self.depth == 0
            self.depth += 1
            self.titles.append(dict(attrs).get("title") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "mark":
            self.depth -= 1
            assert self.depth == 0


# --- beat_trace: claims to beats ----------------------------------------------------------------


def test_a_non_structured_item_has_no_trace() -> None:
    assert beat_trace(item_of(beat(propositions=DROP)), None, story_id=STORY) is None


def test_declared_verdicts_map_to_their_beat_and_not_another() -> None:
    two = trace(beat(number=2), trace_of())
    assert [v.claim_id for v in two.declared] == ["d2.0", "d2.1"]
    three = trace(beat(number=3), trace_of())
    assert [v.claim_id for v in three.declared] == ["d3.0"]


@pytest.mark.parametrize("claim_id", [f"{op_id(2)}:0", 7, None, "d2", "dX.y", "d2.", "d.0"])
def test_other_claim_ids_map_to_no_beat(claim_id: Any) -> None:
    assert declared_beat(claim_id) is None
    t = trace(beat(), trace_of(hooks(verdicts=[{"claim_id": claim_id, "kind": "novel"}])))
    assert t.declared == ()
    assert t.repair_line == "Repaired at rung 1 (accepted)"  # repairs still match by beat


def test_duplicated_verdicts_are_kept_in_engine_order() -> None:
    verdicts = [
        {"claim_id": "d2.1", "kind": "novel"},
        {"claim_id": "d2.0", "kind": "supported"},
        {"claim_id": "d2.1", "kind": "novel"},
    ]
    t = trace(beat(), trace_of(hooks(verdicts=verdicts)))
    assert [v.claim_id for v in t.declared] == ["d2.1", "d2.0", "d2.1"]


def rung(beat_no: int, number: int, accepted: bool | None = None) -> RepairRung:
    return RepairRung(1, beat_no, number, accepted)


DECLARED = (Verdict(1, claim_id="d2.0"),)


@pytest.mark.parametrize(
    ("repairs", "line"),
    [
        ([rung(2, 1, True)], "Repaired at rung 1 (accepted)"),
        ([rung(2, 3, True)], "Repaired at rung 3 (accepted)"),
        ([rung(2, 2, False)], "Rung 2"),
        ([rung(2, 2)], "Rung 2"),
        ([rung(2, 4)], "Safe fallback (rung 4)"),
        ([rung(2, 4, True)], "Safe fallback (rung 4)"),
        ([rung(2, 7, True)], "Rung 7"),
        ([rung(2, 1, False), rung(2, 4)], "Rung 1; Safe fallback (rung 4)"),
        ([], "Not repaired"),
    ],
)
def test_repair_line_wording(repairs: list[RepairRung], line: str) -> None:
    assert repair_line(2, DECLARED, repairs) == line


def test_repair_line_without_declared_claims_or_beat() -> None:
    assert repair_line(2, (), ()) == "None"
    assert repair_line(None, DECLARED, ()) == "—"


def test_repairs_match_by_beat_in_engine_order() -> None:
    rungs = [
        {"beat": 2, "rung": 1, "accepted": False},
        {"beat": 3, "rung": 1},
        {"beat": 2, "rung": 4},
    ]
    t = trace(beat(), trace_of(hooks(repair_rungs=rungs)))
    assert [(r.beat, r.rung) for r in t.repairs] == [(2, 1), (2, 4)]
    assert t.repair_line == "Rung 1; Safe fallback (rung 4)"
    html = render(t)
    assert "Repair: Rung 1; Safe fallback (rung 4)" in html


def test_a_beat_with_no_declared_verdicts_or_repair() -> None:
    t = trace(beat(number=5), trace_of())
    assert render(t).count('<p class="declared-claims">None</p>') == 1
    assert "Repair: None" in render(t)
    declared_only = trace(beat(number=3), trace_of(hooks(repair_rungs=[])))
    assert "Repair: Not repaired" in render(declared_only)


def test_an_item_with_no_beat_reads_a_dash() -> None:
    t = trace(beat(number=None), trace_of())
    html = render(t)
    assert t.declared == ()
    assert '<p class="declared-claims">—</p>' in html
    assert "Repair: —" in html


# --- beat_trace: states -------------------------------------------------------------------------


def test_states() -> None:
    assert trace(beat()).state == NOT_LOADED
    assert trace(beat(), None, state=UNAVAILABLE).state == UNAVAILABLE
    assert trace(beat(), trace_of()).state == LOADED
    assert trace(beat(), trace_of(None)).state == NO_HOOKS
    assert trace(beat(), trace_of(withheld_for="character:x")).state == UNAVAILABLE
    assert trace(beat(origin="ingested"), trace_of()).state == NO_TRACE


def test_state_lines() -> None:
    assert "Tick Show all traces to load escalation and repair" in render(trace(beat()))
    no_hooks = render(trace(beat(), trace_of(None)))
    assert "No eval hooks recorded for this turn" in no_hooks
    assert "Not repaired" not in no_hooks
    unavailable = render(trace(beat(), None, state=UNAVAILABLE))
    assert (
        f'<a href="/stories/{STORY}/?operation={op_id(2)}&amp;origin=generated">'
        "unavailable (see turn trace)</a>"
    ) in unavailable
    assert '<p class="trace-state">—</p>' in render(trace(beat(origin="ingested")))
    play = render(trace(beat(), inspector_hint=True))
    assert "Escalation and repair: open this turn's inspector → Eval hooks &amp; read set" in play


def test_loaded_declared_table() -> None:
    html = render(trace(beat(), trace_of()))
    assert table_rows(html, "beat-declared") == [
        ["d2.0", "supported", "—"],
        ["d2.1", "novel", "escalated"],
    ]
    assert "<td><strong>escalated</strong></td>" in html
    assert "Repair: Repaired at rung 1 (accepted)" in html


# --- spans and segments -------------------------------------------------------------------------


def claim(
    start: int, end: int, category: str = "age", verdict: str | None = None
) -> dict[str, Any]:
    return {"start": start, "end": end, "category": category, "verdict": verdict}


@pytest.mark.parametrize(
    ("text", "span"),
    [
        ("Tobin is 41.", (5, 40)),
        ("Tobin is 41.", (6, 6)),
        ("Tobin is 41.", (8, 3)),
        ("Tobin is 41.", (-1, 3)),
        ("", (0, 1)),
    ],
)
def test_invalid_spans_are_listed_not_highlighted(text: str, span: tuple[int, int]) -> None:
    t = trace(beat(text=text, detected=[claim(*span)]))
    html = render(t)
    assert "<mark" not in html
    assert f"(invalid span {span[0]}{DASH}{span[1]})" in html
    assert t.detected_count == 1
    assert f'<span class="beat-text">{text}</span>' in html


def test_a_valid_span_is_marked_with_its_category_and_verdict() -> None:
    html = render(trace(beat()))
    assert (
        '<span class="beat-text">Tobin is <mark title="age · contradicted">41</mark>.</span>'
        in (html)
    )
    assert table_rows(html, "beat-detected") == [["41", "age", "contradicted"]]


def test_a_null_verdict_reads_not_checked() -> None:
    html = render(trace(beat(detected=[claim(0, 5, "identity")])))
    assert '<mark title="identity · not checked">Tobin</mark>' in html
    assert table_rows(html, "beat-detected") == [["Tobin", "identity", "not checked"]]


def test_overlapping_spans_split_into_valid_segments() -> None:
    claims = [
        claim(2, 6, "age", "novel"),
        claim(4, 8, "place", "supported"),
        claim(4, 8, "place", "supported"),
    ]
    t = trace(beat(text="abcdefghij", detected=claims))
    assert [(s.text, len(s.claims)) for s in t.segments] == [
        ("ab", 0),
        ("cd", 1),
        ("ef", 3),
        ("gh", 2),
        ("ij", 0),
    ]
    parser = MarkNesting()
    parser.feed(render(t))
    assert parser.depth == 0
    assert parser.titles == [
        "age · novel",
        "age · novel; place · supported; place · supported",
        "place · supported; place · supported",
    ]


def test_segments_cover_code_points() -> None:
    found = segments("né 41 ✓", [DetectedClaim(1, 3, 5, "age")])
    assert [(s.text, bool(s.claims)) for s in found] == [
        ("né ", False),
        ("41", True),
        (" ✓", False),
    ]


def test_script_in_the_text_is_escaped() -> None:
    text = "<script>alert(1)</script> & 41"
    html = render(trace(beat(text=text, detected=[claim(28, 30)])))
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; <mark" in html


def test_a_redacted_item_gets_no_marks() -> None:
    text = "[erased] is 41."
    html = render(trace(beat(text=text, redacted=True, detected=[claim(12, 14)])))
    assert "<mark title" not in html
    assert '<span class="beat-text"><mark class="erased">[erased]</mark> is 41.</span>' in html
    assert table_rows(html, "beat-detected") == [
        ["(span unavailable: redacted)", "age", "not checked"]
    ]


def test_an_empty_text_lists_every_span_as_invalid() -> None:
    html = render(trace(beat(text="", detected=[claim(0, 2), claim(1, 3)])))
    assert '<span class="beat-text"></span>' in html
    assert f"(invalid span 0{DASH}2)" in html
    assert f"(invalid span 1{DASH}3)" in html


# --- labels, visibility, propositions -----------------------------------------------------------


def test_say_labels_render_as_three_tags() -> None:
    html = render(trace(beat()))
    assert (
        '<span class="beat-labels"><span class="beat-label" title="intent: sincere">Sincere</span>'
        ' · <span class="beat-label" title="belief: unknown">Unknown</span>'
        ' · <span class="beat-label" title="objective: true">True</span></span>'
    ) in html


def test_a_player_inputs_say_part_has_intent_unspecified() -> None:
    labels = {**SAY_LABELS, "intent": "unspecified", "objective_relation": "unresolved"}
    body = beat(kind="player_input", actor=MARA, labels=labels)
    html = render(trace(body))
    assert 'title="intent: unspecified">Unspecified</span>' in html
    assert 'title="objective: unresolved">Unresolved</span>' in html
    assert "Same propositions as this input" not in html


@pytest.mark.parametrize(
    "labels",
    [
        {"intent": "sincere", "belief_relation": "unknown"},
        {**SAY_LABELS, "intent": "sarcastic"},
        "sincere",
    ],
)
def test_malformed_labels_render_no_tags(labels: Any) -> None:
    t = trace(beat(labels=labels))
    html = render(t)
    assert "beat-label" not in html
    assert [p.label for p in t.more] == ["labels"]
    assert "<h6>labels</h6>" in html


def test_later_label_fields_go_to_more() -> None:
    t = trace(beat(labels={**SAY_LABELS, "later_label": "sincere"}))
    assert len(t.tags) == 3
    assert [p.label for p in t.more] == ["labels"]


# --- classification and corrected intent (#207) --------------------------------------------------

CLASSIFICATION_LINE = (
    "<dt>classification</dt><dd>how a say compares with what its speaker believes (omniscient "
    "reads only). A lie or sincere error says nothing about whether the claim is true in "
    "canon.</dd>"
)


def classified(classification: Any = "lie", **labels: Any) -> dict[str, Any]:
    return beat(labels={**SAY_LABELS, "classification": classification, **labels})


def legend(**context: Any) -> str:
    return render_to_string("console/_verdict_legend.html", context)


@pytest.mark.parametrize(
    ("raw", "text", "css"),
    [
        ("sincere", "Sincere", "classification-sincere"),
        ("sincere_error", "Sincere error", "classification-sincere_error"),
        ("lie", "Lie", "classification-lie"),
        ("joke", "Joke", "classification-joke"),
        (
            "hallucination_repaired",
            "Hallucination repaired",
            "classification-hallucination_repaired",
        ),
        ("white lie", "White lie", "classification-white-lie"),
    ],
)
def test_classification_tag_for_each_value(raw: str, text: str, css: str) -> None:
    t = trace(classified(raw))
    assert len(t.tags) == 4
    tag = t.tags[3]
    assert (tag.text, tag.title, tag.css_class) == (
        text,
        f"classification: {raw}",
        f"beat-classification {css}",
    )
    assert t.classified
    assert (
        ' · <span class="beat-label" title="objective: true">True</span>'
        f' · <span class="beat-label beat-classification {css}" title="classification: {raw}">'
        f"{text}</span></span>"
    ) in render(t)


def test_a_classification_that_slugifies_to_nothing_gets_only_the_base_class() -> None:
    tag = trace(classified("!!")).tags[3]
    assert tag.css_class == "beat-classification"


@pytest.mark.parametrize("classification", [None, DROP, ""])
def test_null_or_absent_classification_adds_no_tag(classification: Any) -> None:
    labels = dict(SAY_LABELS)
    if classification is not DROP:
        labels["classification"] = classification
    t = trace(beat(labels=labels))
    assert len(t.tags) == 3
    assert not t.classified
    assert "beat-classification" not in render(t)


def test_intent_corrected_reads_sincere_corrected() -> None:
    t = trace(classified("sincere", intent_corrected=True))
    intent = t.tags[0]
    assert intent.text == "Sincere (corrected)"
    assert intent.css_class == "intent-corrected"
    assert intent.title == (
        "intent: sincere — declared deceptive; relabelled sincere because the speaker's beliefs "
        "support it"
    )
    assert '<span class="beat-label intent-corrected" title="intent: sincere — declared' in render(
        t
    )


@pytest.mark.parametrize("corrected", [False, None, DROP])
def test_intent_corrected_false_leaves_the_intent_tag(corrected: Any) -> None:
    labels = dict(SAY_LABELS)
    if corrected is not DROP:
        labels["intent_corrected"] = corrected
    t = trace(beat(labels=labels))
    assert t.tags[0].text == "Sincere"
    assert t.tags[0].css_class == ""
    assert (
        '<span class="beat-labels"><span class="beat-label" title="intent: sincere">Sincere</span>'
        in render(t)
    )
    assert "(corrected)" not in render(t)


def test_classification_fields_no_longer_go_to_more() -> None:
    t = trace(classified("lie", intent_corrected=True))
    assert t.more == ()
    assert "<h6>labels</h6>" not in render(t)
    # Complement: a wrong-typed value stays in extras and sends the raw labels to More.
    assert [p.label for p in trace(classified(3)).more] == ["labels"]
    assert [p.label for p in trace(classified("lie", intent_corrected="yes")).more] == ["labels"]


def test_legend_line_only_with_a_classification() -> None:
    assert CLASSIFICATION_LINE in legend(classification=True)
    assert "<dt>classification</dt>" not in legend(classification=False)
    assert "<dt>classification</dt>" not in legend()
    shown = legend(classification=True)
    assert shown.index("<dt>not checked</dt>") < shown.index("<dt>classification</dt>")


def latent(visibility: Any) -> dict[str, Any]:
    return beat(kind="beat_latent", channel="latent", labels=DROP, visibility=visibility)


@pytest.mark.parametrize(
    ("visibility", "shown"),
    [
        ({"scope": "objective", "characters": []}, "Latent · visible to everyone (objective)"),
        ({"scope": "public"}, "Latent · visible to public"),
        ({"scope": "characters", "characters": []}, "Latent · visible to no character"),
        (
            {"scope": "characters", "characters": [TOBIN, PA1]},
            f'Latent · visible to <span title="{TOBIN}">Tobin</span>, '
            f'<code title="{PA1}">…{PA1[-8:]}</code>',
        ),
        (
            {"scope": "everyone"},
            "Latent · visibility <code>{&quot;scope&quot;:&quot;everyone&quot;}</code>",
        ),
        (
            {"scope": "characters", "characters": "x"},
            "Latent · visibility <code>{&quot;characters&quot;:&quot;x&quot;,"
            "&quot;scope&quot;:&quot;characters&quot;}</code>",
        ),
    ],
)
def test_latent_visibility(visibility: Any, shown: str) -> None:
    html = render(trace(latent(visibility), cast_names={TOBIN: "Tobin", MARA: "Mara (player)"}))
    assert shown in html
    assert "beat-label" not in html


def test_an_empty_structured_item_still_has_a_trace() -> None:
    t = trace(beat(propositions=[], detected=DROP, labels=DROP))
    html = render(t)
    assert "<summary>Trace · 0 propositions · 0 detected</summary>" in html
    assert '<details class="beat-trace">' in html


def test_the_summary_is_singular_at_one() -> None:
    body = beat(propositions=[{"proposition": proposition(), "verdict": None}])
    assert trace(body).summary == "Trace · 1 proposition · 1 detected"


def test_propositions_render_with_their_verdicts() -> None:
    names = {PR1: "located_at"}
    html = render(
        trace(beat(), cast_names={TOBIN: "Tobin", MARA: "Mara (player)"}, predicate_names=names)
    )
    rows = table_rows(html, "beat-propositions")
    assert [r[1] for r in rows] == ["supported", "novel"]
    assert "located_at" in rows[0][0]
    assert "Tobin" in rows[0][0]
    assert "Mara (player)" in rows[1][0]


def test_an_unparsable_proposition_renders_as_json() -> None:
    body = beat(propositions=[{"proposition": {"subject_id": TOBIN}, "verdict": None}, "junk"])
    t = trace(body)
    html = render(t)
    assert '<code class="raw-proposition">{\n  &quot;subject_id&quot;' in html
    assert table_rows(html, "beat-propositions")[0][1] == "not checked"
    assert [p.label for p in t.more] == ["proposition 2"]


def test_a_player_inputs_do_part_shows_the_shared_propositions() -> None:
    body = beat(kind="player_input", channel="do", actor=MARA, labels=DROP)
    html = render(trace(body))
    assert '<p class="muted shared-propositions">Same propositions as this input' in html
    assert [r[1] for r in table_rows(html, "beat-propositions")] == ["supported", "novel"]


def test_quarantined_source_note() -> None:
    assert "Source of a quarantined memory" in render(trace(beat(quarantined=True)))
    assert "Source of a quarantined memory" not in render(trace(beat()))


def test_malformed_detected_entries_go_to_more_and_count() -> None:
    t = trace(beat(detected=[claim(9, 11), {"start": "9"}, 3]))
    assert t.detected_count == 3
    assert len(t.detected) == 1
    assert [p.label for p in t.more] == ["detected 2", "detected 3"]


def test_verified_never_appears() -> None:
    corrected = classified("sincere", intent_corrected=True)
    for t in (
        trace(beat(), trace_of()),
        trace(beat()),
        trace(beat(), trace_of(None)),
        trace(classified("lie")),
        trace(corrected, trace_of()),
    ):
        assert "verified" not in render(t).lower()
    assert "verified" not in legend(classification=True).lower()


# --- the history page ---------------------------------------------------------------------------


def turn_items(turn: int, *, structured: bool = True) -> list[dict[str, Any]]:
    """A turn: Mara's structured input (say + do), Tobin's say beat, the composed message."""
    message = f"01JB2Z8Y5Q3W6K9M1N4P7M{turn:02d}99"
    say = beat(
        turn,
        1,
        "Is the ferry late?",
        kind="player_input",
        actor=MARA,
        detected=DROP,
        message_id=f"01JB2Z8Y5Q3W6K9M1N4P7M{turn:02d}01",
    )
    say["labels"] = {**SAY_LABELS, "intent": "unspecified"}
    do = {**say, "channel": "do", "text": "checks the board"}
    do.pop("labels")
    reply = {
        **{
            k: v
            for k, v in beat(turn, 3).items()
            if k not in ("propositions", "effects", "labels", "detected")
        },
        "kind": "message_rendered",
        "channel": "message",
        "actor_character_id": None,
        "render_character_ids": [TOBIN],
        "message_id": message,
    }
    if not structured:
        for part in (say, do):
            for key in ("propositions", "effects", "labels"):
                part.pop(key, None)
        return [say, do, reply]
    return [say, do, beat(turn, 2), reply]


def history_body(turns: list[int], *, structured: bool = True) -> dict[str, Any]:
    return {
        "items": [i for t in turns for i in turn_items(t, structured=structured)],
        "turns": [
            {
                "turn": t,
                "variant": 1,
                "message": {
                    "message_id": f"01JB2Z8Y5Q3W6K9M1N4P7M{t:02d}99",
                    "text": "Tobin is 41.",
                },
            }
            for t in turns
        ],
        "output_viewpoint": "omniscient",
        "audience_filter": "none",
        "player_view": "character",
        "as_of": max(turns),
        "timeline_revision": 9,
        "next_before_turn": min(turns) if min(turns) > 1 else None,
    }


Handler = Callable[[httpx.Request], httpx.Response]


def answer_operations(engine: FakeEngine, special: dict[str, Handler] | None = None) -> None:
    """Every trace read answers its turn's operation with :func:`hooks`, unless ``special``
    names a handler for that operation id."""

    def handler(request: httpx.Request) -> httpx.Response:
        op = request.url.path.rsplit("/", 1)[1]
        if special and op in special:
            return special[op](request)
        turn = int(op[-4:])
        return httpx.Response(200, json=operation(turn))

    engine.side_effect("GET", OPERATION, handler)


def answer_history(engine: FakeEngine, body: dict[str, Any], *, predicates: bool = True) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=40))
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, 200, json=body)
    if predicates:
        engine.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    answer_operations(engine)


def calls(engine: FakeEngine, prefix: str) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path.startswith(prefix)]


def trace_reads(engine: FakeEngine) -> list[httpx.Request]:
    return calls(engine, "/operations/")


def predicate_reads(engine: FakeEngine) -> int:
    return sum(1 for r in engine.requests if r.url.path.endswith("/predicates"))


def turn_html(html: str, turn: int) -> str:
    """One turn's ``<tbody>`` (up to the next turn's)."""
    return html.split(f'id="turn-{turn}-1"', 1)[1].split('<tbody class="turn"', 1)[0]


def page(client: Client, query: str = "viewpoint=omniscient") -> str:
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 200
    return response.content.decode()


def test_traces_off_reads_no_trace_and_the_registry_once(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_history(engine, history_body([2, 3]))
    html = page(operator_client)
    assert trace_reads(engine) == []
    assert predicate_reads(engine) == 1
    assert html.count('<dl class="verdict-legend">') == 1
    assert html.index('<dl class="verdict-legend">') < html.index('<table class="history">')
    assert '<details class="beat-trace">' in html
    assert 'beat-trace" open' not in html
    assert "Tick Show all traces to load escalation and repair" in html
    assert 'title="intent: sincere">Sincere</span>' in html
    assert '<mark title="age · contradicted">41</mark>' in html
    assert "located_at" in html
    assert '<input type="checkbox" name="traces" value="1" id="id_traces"> Show all traces' in html


def test_an_omniscient_page_shows_the_classification_and_its_legend_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = history_body([2, 3])
    body["items"][2]["labels"] = {**SAY_LABELS, "classification": "lie"}
    answer_history(engine, body)
    html = page(operator_client)
    assert 'class="beat-label beat-classification classification-lie"' in html
    assert html.count("<dt>classification</dt>") == 1
    assert html.index("<dt>classification</dt>") < html.index('<table class="history">')
    # Complement: no classified say, no legend line.
    engine.reset()
    answer_history(engine, history_body([2, 3]))
    assert "<dt>classification</dt>" not in page(operator_client)


def test_traces_on_reads_each_structured_turn_once(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_history(engine, history_body([2, 3]))
    html = page(operator_client, "viewpoint=omniscient&traces=1")
    reads = trace_reads(engine)
    assert sorted(r.url.path for r in reads) == [
        f"/operations/{op_id(2)}",
        f"/operations/{op_id(3)}",
    ]
    assert all(r.url.params.get("viewpoint") == "omniscient" for r in reads)
    assert all("traces" not in r.url.params for r in calls(engine, f"/stories/{STORY}/history"))
    assert 'id="id_traces" checked> Show all traces' in html
    assert '<details class="beat-trace" open>' in html
    assert "Tick Show all traces" not in html
    turn2 = turn_html(html, 2)
    assert table_rows(turn2, "beat-declared") == [
        ["d2.0", "supported", "—"],
        ["d2.1", "novel", "escalated"],
    ]
    assert "Repair: Repaired at rung 1 (accepted)" in turn2
    assert "trace-notice" not in html
    assert "verified" not in html.lower()


def test_twenty_five_turns_read_twenty_and_note_the_cap(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_history(engine, history_body(list(range(1, 26))))
    html = page(operator_client, "viewpoint=omniscient&traces=1")
    reads = trace_reads(engine)
    assert len(reads) == 20
    assert {r.url.path for r in reads} == {f"/operations/{op_id(t)}" for t in range(1, 21)}
    assert (
        '<p class="warning trace-notice">Escalation and repair loaded for the first 20 turns on '
        "this page.</p>"
    ) in html
    late = turn_html(html, 25)
    assert "Tick Show all traces to load escalation and repair" in late


def test_a_lite_page_makes_no_trace_or_registry_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_history(engine, lite_history(), predicates=False)
    html = page(operator_client, "viewpoint=omniscient&traces=1")
    assert trace_reads(engine) == []
    assert predicate_reads(engine) == 0
    assert '<dl class="verdict-legend">' not in html
    assert '<details class="beat-trace"' not in html
    unstructured = history_body([2], structured=False)
    engine.respond("GET", HISTORY, 200, json=unstructured)
    page(operator_client, "viewpoint=omniscient&traces=1")
    assert trace_reads(engine) == []
    assert predicate_reads(engine) == 0


def test_failed_withheld_and_late_reads_mark_their_turns_unavailable(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(history_views, "TRACE_DEADLINE_S", 0.1)

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json=error_json("database_unavailable"))

    def withheld(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("viewpoint"):
            return httpx.Response(409, json=fixture("idempotency_conflict"))
        return httpx.Response(200, json=operation(4, viewpoint=f"character:{TOBIN}"))

    def late(request: httpx.Request) -> httpx.Response:
        time.sleep(0.5)
        return httpx.Response(200, json=operation(5))

    answer_history(engine, history_body([2, 3, 4, 5]))
    answer_operations(engine, {op_id(3): failing, op_id(4): withheld, op_id(5): late})
    response = operator_client.get(f"{URL}?viewpoint=omniscient&traces=1")
    assert response.status_code == 200
    html = response.content.decode()
    assert (
        '<p class="warning trace-notice">Turn trace unavailable: T3 (HTTP 503 '
        "database_unavailable), T4 (withheld: produced for another viewpoint), T5 (timeout)</p>"
    ) in html
    assert "engine-error" not in html.split('<section class="items">')[1]
    for turn in (3, 4, 5):
        body = turn_html(html, turn)
        assert "unavailable (see turn trace)</a>" in body
        assert [r[1] for r in table_rows(body, "beat-propositions")] == ["supported", "novel"]
        assert table_rows(body, "beat-detected") == [["41", "age", "contradicted"]]
    ok = turn_html(html, 2)
    assert "Repair: Repaired at rung 1 (accepted)" in ok


def test_a_turn_without_eval_hooks(operator_client: Client, engine: FakeEngine) -> None:
    answer_history(engine, history_body([2]))
    answer_operations(engine, {op_id(2): lambda r: httpx.Response(200, json=operation(2, None))})
    html = page(operator_client, "viewpoint=omniscient&traces=1")
    assert "No eval hooks recorded for this turn" in html
    assert "Not repaired" not in html


def test_a_failed_registry_read_degrades_to_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_history(engine, history_body([2]), predicates=False)
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable"))
    html = page(operator_client)
    assert "located_at" not in html
    assert f'title="{PR1}"' in html
    assert "beat-trace" in html


def test_an_invalid_form_keeps_traces_checked_and_reads_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_history(engine, history_body([2]))
    html = page(operator_client, "viewpoint=omniscient&traces=1&as_of=-1")
    assert 'id="id_traces" checked> Show all traces' in html
    assert trace_reads(engine) == []
    assert calls(engine, f"/stories/{STORY}/history") == []


def test_paging_links_keep_traces(operator_client: Client, engine: FakeEngine) -> None:
    answer_history(engine, history_body([2, 3]))
    html = page(operator_client, "viewpoint=omniscient&traces=1")
    older = re.search(r'<p class="paging"><a href="([^"]+)">Older', html)
    assert older is not None
    assert "traces=1" in older.group(1)


# --- the play page ------------------------------------------------------------------------------


def test_the_play_transcript_shows_item_level_traces_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, history=history_body([2, 3]))
    answer_operations(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert trace_reads(engine) == []
    assert predicate_reads(engine) == 0
    transcript = html.split('<div id="transcript">', 1)[1].split('<div id="turn-status"', 1)[0]
    assert transcript.count('<dl class="verdict-legend">') == 1
    assert transcript.index("verdict-legend") < transcript.index('<ol class="transcript">')
    assert "Escalation and repair: open this turn's inspector" in transcript
    assert '<mark title="age · contradicted">41</mark>' in transcript
    assert f'title="{PR1}"' in transcript  # predicate ids, no registry read
    assert "Same propositions as this input" in transcript
    assert "verified" not in transcript.lower()
