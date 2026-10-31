"""Direct a belief (#194): the form, the call, the result page and the refusals.

Runs against the real urlconf on the schema-validating fake Engine, under #188's Structured v2
configuration (nested beliefs on, depth 2) and the shared Alice/Bob/Pat cast (Pat is the
player). Results:

- ``belief_written_first_order.json``: Alice's ``BW01``, suspected 0.3, closing ``BE02``, the
  transition ``TR01`` at turn 3 · after the turn, valid at minute 540 (revision 8, ``OP01``);
- ``belief_written_nested_d2.json``: ``BW02`` nested (attributed to Bob, depth 2), believed 0.6;
- ``belief_written_nested_d3.json``: ``BW03`` nested depth 3, known, no scalar, nothing closed.
"""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.beliefs import (
    CONFIDENCE_ALONE,
    HOLDER_NO_PARTITION,
    MISSING_KEYS,
    NOT_READABLE,
    NOT_THE_HOLDER,
    OUT_OF_ORDER,
    SCALAR_INVALID,
    UNKNOWN_PREDICATE_TEXT,
    nested_depth,
    scalar_syntax,
)
from console.inspect import GATE_UNREAD
from console.models import EngineOperation
from console.paging import CHARACTER_INVALID
from console.views import inspect as views
from console.views.inspect_beliefs import PREFILL_FAILED
from console.wording import UNKNOWN_CHARACTER
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import (
    ALICE,
    BOB,
    EFFECTIVE,
    FIXTURES,
    PAT,
    STORY,
    STORY_PATH,
    story_cast,
    structured_v2,
    with_settings,
)
from tests.inspect.test_knowledge_shell import partitions_off
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/knowledge/beliefs/direct/"
WRITTEN = f"/stories/{STORY}/inspect/knowledge/beliefs/written/"
BELIEFS_URL = f"/stories/{STORY}/inspect/knowledge/beliefs/"
WRITE = "/admin/stories/{story_id}/beliefs"
WRITE_PATH = f"/admin/stories/{STORY}/beliefs"
BELIEFS = "/stories/{story_id}/inspect/beliefs"
BELIEFS_PATH = f"/stories/{STORY}/inspect/beliefs"
PREDICATES = "/stories/{story_id}/predicates"
FACTS = "/admin/stories/{story_id}/facts"
P = "0000000000000000000000"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0K01"
PP01 = P + "PP01"
OUTSIDER = P + "ZZ99"
NESTED_ENABLED = "character_knowledge.nested_beliefs.enabled"
DEPTH = "character_knowledge.nested_beliefs.depth"
PROPOSITION: dict[str, Any] = {
    "subject_id": P + "EN01",
    "predicate_id": "01JB2Z8Y5Q3W6K9M1N4P7R0PR1",
    "object_id": P + "EN04",
    "valid_time": {"kind": "interval", "start": {"inner": 0, "outer": 0}},
}
NOT_FOUND = "Not found (it may have been deleted or truncated)"
NESTED_OFF = (
    "Needs the <code>nested_beliefs</code> module "
    "(<code>character_knowledge.nested_beliefs.enabled</code> is off under this story&#x27;s "
    "profile)"
)
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "console" / "inspect"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v2())
    fake.respond("GET", STORY_PATH, 200, json=story_cast())
    fake.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    fake.respond("GET", BELIEFS, 200, json=fixture("beliefs_page1"))
    fake.respond("GET", FACTS, 200, json={"facts": [], "next_cursor": None, "timeline_revision": 7})
    fake.respond("POST", WRITE, 201, json=fixture("belief_written_first_order"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def configure(engine: FakeEngine, settings: dict[str, Any]) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=with_settings(structured_v2(), settings))


def answer(engine: FakeEngine, name: str) -> None:
    engine.respond("POST", WRITE, 201, json=fixture(name))


def refuse(engine: FakeEngine, status: int, code: str, **details: Any) -> None:
    engine.respond("POST", WRITE, status, json=error_json(code, "Refused by the Engine", **details))


def post(client: Client, **fields: str) -> str:
    data = {"idempotency_key": KEY, "holder": ALICE, "proposition": json.dumps(PROPOSITION)}
    data.update(fields)
    response = client.post(URL, data)
    assert response.status_code == 200
    return response.content.decode()


def get(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == WRITE_PATH]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (call,) = writes(engine)
    body: dict[str, Any] = json.loads(call.content)
    return body


def prefill_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == BELIEFS_PATH]


def text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def errors_of(html: str, name: str) -> list[str]:
    return [
        text(e)
        for e in re.findall(r'<ul class="errorlist[^"]*"[^>]*>(.*?)</ul>', field(html, name), re.S)
    ]


def nested_errors(html: str) -> list[str]:
    found = re.search(r'<ul class="errorlist nested-errors">(.*?)</ul>', html, re.S)
    return [text(li) for li in re.findall(r"<li>(.*?)</li>", found.group(1), re.S)] if found else []


def refusal(html: str) -> str:
    found = re.search(r'<p class="errorlist belief-refusal" role="alert">(.*?)</p>', html, re.S)
    assert found is not None
    return text(found.group(1))


def rendered_key(html: str) -> str:
    found = re.search(r'name="idempotency_key" value="([^"]+)"', html)
    assert found is not None
    return found.group(1)


def detail(html: str, cls: str) -> str:
    found = re.search(rf'<dd class="{cls}">(.*?)</dd>', html, re.S)
    assert found is not None, cls
    return text(found.group(1))


def textarea(html: str) -> Any:
    found = re.search(r"<textarea[^>]*>(.*?)</textarea>", field(html, "proposition"), re.S)
    assert found is not None
    return json.loads(unescape(found.group(1)))


def fieldset(html: str) -> str:
    found = re.search(r'<fieldset class="belief-nested"[^>]*>.*?</fieldset>', html, re.S)
    assert found is not None
    return found.group(0)


# --- the request body ---


def test_body_minimal(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client)
    body = sent(engine)
    assert body == {"holder": ALICE, "proposition": PROPOSITION}
    for absent in (
        "polarity",
        "confidence_level",
        "confidence_scalar",
        "disclosure",
        "attributed_to",
        "attributed_confidence_level",
    ):
        assert absent not in body


def test_body_full(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, "belief_written_nested_d2")
    post(
        operator_client,
        holder=f"  {ALICE.lower()} ",
        polarity="negated",
        confidence_level="suspected",
        confidence_scalar="0.3",
        disclosure="secret",
        attributed_to_1=BOB,
        attributed_confidence_level="believed",
    )
    assert sent(engine) == {
        "holder": ALICE,
        "proposition": PROPOSITION,
        "polarity": "negated",
        "confidence_level": "suspected",
        "confidence_scalar": 0.3,
        "disclosure": "secret",
        "attributed_to": [BOB],
        "attributed_confidence_level": "believed",
    }


def test_nested_body_order(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {DEPTH: 3})
    answer(engine, "belief_written_nested_d3")
    post(operator_client, attributed_to_1=BOB, attributed_to_2=PAT)
    assert sent(engine)["attributed_to"] == [BOB, PAT]


@pytest.mark.parametrize(("typed", "number"), [(".5", 0.5), ("0.3000", 0.3), ("1", 1.0)])
def test_scalar_sent_as_three_dp_number(
    operator_client: Client, engine: FakeEngine, typed: str, number: float
) -> None:
    post(operator_client, confidence_scalar=f" {typed} ")
    (call,) = writes(engine)
    assert f'"confidence_scalar":{number}' in call.content.decode().replace(" ", "")
    assert sent(engine)["confidence_scalar"] == number


@pytest.mark.parametrize(("depth", "selects"), [(2, 1), (3, 2), (5, 4), (None, 1), (3.0, 1)])
def test_depth_selects_follow_config(
    operator_client: Client, engine: FakeEngine, depth: Any, selects: int
) -> None:
    configure(engine, {DEPTH: depth})
    html = get(operator_client)
    assert len(re.findall(r'<select name="attributed_to_\d+"', html)) == selects


def test_nested_depth_reads_only_integers() -> None:
    assert nested_depth(None) == 2
    assert nested_depth({"character_knowledge": {"nested_beliefs": {"depth": 1}}}) == 2
    assert nested_depth({"character_knowledge": {"nested_beliefs": {"depth": 4}}}) == 4
    assert nested_depth({"character_knowledge": {"nested_beliefs": {"depth": 9}}}) == 5
    assert nested_depth({"character_knowledge": {"nested_beliefs": {"depth": True}}}) == 2
    assert nested_depth({"character_knowledge": {"nested_beliefs": {"depth": 3.0}}}) == 2


# --- the form page ---


def test_form_lists_the_cast(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    holder = field(html, "holder")
    options = re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', holder)
    assert options == [("", "Choose a character"), (ALICE, "Alice"), (BOB, "Bob")]
    attributed = re.findall(r'<option value="([^"]*)"', field(html, "attributed_to_1"))
    assert attributed == ["", ALICE, BOB, PAT]
    assert "Pat (player)" in field(html, "attributed_to_1")
    assert "Predicate ids are on the Predicates tab" in html
    assert f'href="/stories/{STORY}/inspect/predicates/"' in html
    assert "Neither level nor scalar: the belief is known" in html
    assert "(as in the proposition)" in field(html, "polarity")
    levels = re.findall(r'<option value="([^"]*)"', field(html, "confidence_level"))
    assert levels == ["", "unknown", "suspected", "believed", "known"]
    assert "Nested: the holder thinks someone believes it" in html
    assert "The holder thinks … believes it" in html
    assert escape(NOT_READABLE) in html
    assert 'min="' not in html
    assert 'max="' not in html
    assert f'href="{BELIEFS_URL}"' in html  # Back to beliefs


@pytest.mark.parametrize(
    ("holder", "label"),
    [(PAT, "Pat (player)"), (OUTSIDER, "…0000ZZ99 (not in the cast)")],
)
def test_url_holder_outside_the_options_is_kept(
    operator_client: Client, engine: FakeEngine, holder: str, label: str
) -> None:
    html = get(operator_client, f"?holder={holder.lower()}")
    options = re.findall(
        r'<option value="([^"]*)"( selected)?>(.*?)</option>', field(html, "holder")
    )
    assert options[-1][:2] == (holder, " selected")
    assert text(options[-1][2]) == label
    assert f'href="{BELIEFS_URL}?character={holder}"' in html


def test_holder_kept_and_sent_so_the_engine_refuses(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, 422, "holder_has_no_partition")
    html = post(operator_client, holder=PAT)
    assert sent(engine)["holder"] == PAT
    assert errors_of(html, "holder") == [HOLDER_NO_PARTITION]
    assert re.search(rf'<option value="{PAT}" selected>', html)


def test_story_read_failed_gives_id_fields(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    html = get(operator_client)
    assert '<input type="text" name="holder"' in field(html, "holder")
    assert '<input type="text" name="attributed_to_1"' in field(html, "attributed_to_1")
    html = post(operator_client, holder="nope")
    assert errors_of(html, "holder") == [CHARACTER_INVALID]
    assert writes(engine) == []


def test_greyed_tab_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=partitions_off())
    html = get(operator_client, f"?holder={ALICE}&proposition_id={PP01}")
    assert "Not available for this story" in html
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
    ]
    post(operator_client)
    assert writes(engine) == []


# --- pre-call refusals: zero writes ---


@pytest.mark.parametrize("box", ["{", "[1, 2]", ""])
def test_bad_json_zero_calls(operator_client: Client, engine: FakeEngine, box: str) -> None:
    html = post(operator_client, proposition=box)
    assert errors_of(html, "proposition")
    assert writes(engine) == []


def test_missing_keys_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = post(
        operator_client,
        proposition=json.dumps({**PROPOSITION, "valid_time": None}),
        confidence_scalar="0.3001",
    )
    assert errors_of(html, "proposition") == [MISSING_KEYS]
    assert errors_of(html, "confidence_scalar") == [SCALAR_INVALID]  # all errors together
    assert writes(engine) == []


def test_scalar_band_mismatch_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, confidence_level="known", confidence_scalar="0.3")
    assert errors_of(html, "confidence_scalar") == ["The scalar 0.300 is Suspected, not Known"]
    assert writes(engine) == []


def test_level_alone_passes(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, confidence_level="known")
    assert sent(engine)["confidence_level"] == "known"


@pytest.mark.parametrize("typed", ["0.3001", "1.5", "2"])
def test_scalar_four_decimals_zero_calls(
    operator_client: Client, engine: FakeEngine, typed: str
) -> None:
    html = post(operator_client, confidence_scalar=typed)
    assert errors_of(html, "confidence_scalar") == [SCALAR_INVALID]
    assert writes(engine) == []


@pytest.mark.parametrize("typed", ["+0.5", "1E-1", "0,5", "-0.1", "."])
def test_scalar_syntax_refused_zero_calls(
    operator_client: Client, engine: FakeEngine, typed: str
) -> None:
    html = post(operator_client, confidence_scalar=typed)
    assert errors_of(html, "confidence_scalar") == [SCALAR_INVALID]
    assert writes(engine) == []


@pytest.mark.parametrize(
    ("typed", "valid"),
    [
        ("0", True),
        (".5", True),
        ("0.300", True),
        ("1.", False),
        (".", False),
        ("", False),
        ("0.5.1", False),
        ("1E-1", False),
        ("+0.5", False),
        ("0 5", False),
    ],
)
def test_scalar_syntax(typed: str, valid: bool) -> None:
    assert scalar_syntax(typed) is valid


def test_scalar_syntax_is_linear_on_a_long_adversarial_string() -> None:
    """CodeQL py/polynomial-redos: the old regex backtracked on a long run of digits."""
    zeros = "0" * 50000
    assert scalar_syntax(zeros) is True
    assert scalar_syntax(zeros + "x") is False
    assert scalar_syntax(zeros + "." + zeros) is True
    assert scalar_syntax(zeros + "." + zeros + "x") is False


def test_a_long_zero_scalar_is_sent_as_zero(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, confidence_scalar="0" * 50000)
    assert sent(engine)["confidence_scalar"] == 0


def test_a_long_adversarial_scalar_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, confidence_scalar="0" * 50000 + "x")
    assert errors_of(html, "confidence_scalar") == [SCALAR_INVALID]
    assert writes(engine) == []


def test_attribution_gap_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {DEPTH: 3})
    html = post(operator_client, attributed_to_2=BOB)
    assert errors_of(html, "attributed_to_2") == [OUT_OF_ORDER]
    assert writes(engine) == []


def test_attributed_self_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {DEPTH: 3})
    html = post(operator_client, attributed_to_1=ALICE, attributed_to_3=BOB)
    assert errors_of(html, "attributed_to_1") == [NOT_THE_HOLDER]
    assert writes(engine) == []


def test_inner_repeat_is_left_to_the_engine(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {DEPTH: 3})
    answer(engine, "belief_written_nested_d3")
    post(operator_client, attributed_to_1=BOB, attributed_to_2=ALICE)
    assert sent(engine)["attributed_to"] == [BOB, ALICE]


def test_attributed_confidence_alone_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, attributed_confidence_level="believed")
    assert errors_of(html, "attributed_confidence_level") == [CONFIDENCE_ALONE]
    assert writes(engine) == []


def test_nested_off_refuses_attributions_no_write(
    operator_client: Client, engine: FakeEngine
) -> None:
    configure(engine, {NESTED_ENABLED: False})
    html = post(operator_client, attributed_to_1=BOB)
    assert refusal(html) == text(NESTED_OFF)
    assert writes(engine) == []


def test_nested_key_missing_gate_unread_no_write(
    operator_client: Client, engine: FakeEngine
) -> None:
    configure(engine, {NESTED_ENABLED: None})
    html = post(operator_client, attributed_confidence_level="known")
    assert refusal(html) == GATE_UNREAD
    assert writes(engine) == []


def test_nested_off_first_order_still_writes(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {NESTED_ENABLED: False})
    post(operator_client)
    assert sent(engine) == {"holder": ALICE, "proposition": PROPOSITION}


def test_nested_off_fieldset_greyed_not_hidden(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {NESTED_ENABLED: False})
    html = get(operator_client)
    nested = fieldset(html)
    assert re.match(
        r'<fieldset class="belief-nested" disabled aria-describedby="gate-nested"', nested
    )
    assert NESTED_OFF in nested
    assert f'<option value="{BOB}">Bob</option>' in nested
    assert escape(NOT_READABLE) in nested


def test_nested_key_missing_reads_gate_unread(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {NESTED_ENABLED: None})
    nested = fieldset(get(operator_client))
    assert " disabled " in nested.split(">", 1)[0] + " "
    assert escape(GATE_UNREAD) in nested


# --- the write ---


def test_idempotency_key_sent_and_recorded(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client)
    (call,) = writes(engine)
    assert call.headers["Idempotency-Key"] == KEY
    assert "X-Epistrel-Expected-Revision" not in call.headers
    row = EngineOperation.objects.get()
    assert row.kind == "write_belief"
    assert row.entity_type == "story"
    assert row.entity_id == STORY
    assert row.idempotency_key == KEY


def test_every_render_mints_a_fresh_key(operator_client: Client, engine: FakeEngine) -> None:
    first, second = get(operator_client), get(operator_client)
    assert rendered_key(first) != rendered_key(second)
    refused = post(operator_client, confidence_scalar="+1")
    assert rendered_key(refused) != KEY


# --- the result page ---


def test_result_first_order(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, confidence_level="suspected", confidence_scalar="0.3")
    assert '<h3 class="belief-written">Belief written</h3>' in html
    assert detail(html, "holder") == "Alice"
    assert detail(html, "kind") == "First-order"
    assert "located_at" in detail(html, "proposition")
    assert detail(html, "confidence") == "Suspected · 0.300"
    assert detail(html, "accessibility") == "1.000"
    assert detail(html, "state") == "Held"
    assert detail(html, "provenance") == "…0000TR01"  # the transition's id (#227)
    assert f'<dd class="provenance"><code title="{P}TR01">' in html
    assert detail(html, "held-from") == "turn 3 · after the turn until now"
    assert detail(html, "valid") == "[0, …) min"
    transition = detail(html, "transition")
    assert "…0000TR01" in transition
    assert "Author · valid at 540 min · turn 3 · after the turn" in transition
    row = EngineOperation.objects.get()
    link = f'<a class="operation-link" href="/operations/{row.pk}/"><code>{P}OP01</code></a>'
    assert link in html
    assert detail(html, "operation").endswith("timeline revision 8")
    see = f"{BELIEFS_URL}?character={ALICE}&amp;proposition_id={P}PP05"
    assert f'<a class="see-belief" href="{see}">See Alice&#x27;s belief</a>' in html
    assert f'<a class="direct-another" href="{URL}?holder={ALICE}">' in html
    more = re.search(r'<details class="belief-written-more">(.*?)</details>', html, re.S)
    assert more is not None
    assert f"{P}BW01" in more.group(1)  # lineage id
    assert "Raw response" in more.group(1)
    assert "Suspected · 0.300" in more.group(1)  # the transition's confidence
    assert escape(NOT_READABLE) not in html.split('class="belief-written"')[1]


def test_result_nested(operator_client: Client, engine: FakeEngine) -> None:
    configure(engine, {DEPTH: 3})
    answer(engine, "belief_written_nested_d3")
    html = post(operator_client, attributed_to_1=BOB, attributed_to_2=PAT)
    assert (
        detail(html, "kind") == "Nested: Alice thinks Bob thinks Pat (player) believes it (depth 3)"
    )
    assert detail(html, "confidence") == "Known"
    assert 'class="see-belief"' not in html
    assert "See Alice" not in html
    assert escape(NOT_READABLE) in html.split('class="belief-written"')[1]
    assert f'<a class="direct-another" href="{URL}?holder={ALICE}">' in html


def test_closed_listed(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client)
    closed = detail(html, "closed")
    assert closed == "Closed 1 contradicting belief(s): …0000BE02"
    answer(engine, "belief_written_nested_d2")
    html = post(operator_client, attributed_to_1=BOB)
    assert detail(html, "closed") == "Closed none"


def test_report_again(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, "belief_written_nested_d2")
    post(operator_client, attributed_to_1=BOB)
    row = EngineOperation.objects.get()
    engine.requests.clear()
    response = operator_client.get(f"{WRITTEN}?report={row.pk}")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Belief written" in html
    assert detail(html, "kind") == "Nested: Alice thinks Bob believes it (depth 2)"
    assert not writes(engine)


def test_report_without_a_recorded_chain(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, "belief_written_nested_d2")
    post(operator_client, attributed_to_1=BOB)
    row = EngineOperation.objects.get()
    row.request = {}
    row.save()
    html = operator_client.get(f"{WRITTEN}?report={row.pk}").content.decode()
    assert detail(html, "kind") == (
        "Nested: Alice thinks … Bob believes it (depth 2, chain not recorded)"
    )


def test_report_wrong_kind_not_found(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client)
    row = EngineOperation.objects.get()
    row.kind = "commit_fact"
    row.save()
    html = operator_client.get(f"{WRITTEN}?report={row.pk}").content.decode()
    assert NOT_FOUND in html
    assert "Belief written</h3>" not in html


@pytest.mark.parametrize("query", ["", "?report=", "?report=abc", "?report=999"])
def test_report_missing_not_found(operator_client: Client, engine: FakeEngine, query: str) -> None:
    response = operator_client.get(f"{WRITTEN}{query}")
    assert response.status_code == 200
    assert NOT_FOUND in response.content.decode()


def test_report_greyed_tab(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=partitions_off())
    html = operator_client.get(f"{WRITTEN}?report=1").content.decode()
    assert "Not available for this story" in html


# --- Engine refusals ---


@pytest.mark.parametrize(
    ("code", "details", "fields", "where", "shown"),
    [
        ("holder_has_no_partition", {}, {}, "holder", HOLDER_NO_PARTITION),
        ("unknown_predicate", {}, {}, "proposition", UNKNOWN_PREDICATE_TEXT),
        (
            "confidence_inconsistent",
            {},
            {"confidence_level": "suspected", "confidence_scalar": "0.3"},
            "confidence_scalar",
            "The scalar 0.300 is Suspected, not Suspected",
        ),
        (
            "validation_error",
            {"field": "holder", "reason": "unknown_character"},
            {},
            "holder",
            UNKNOWN_CHARACTER,
        ),
        (
            "validation_error",
            {"field": "body.disclosure"},
            {"disclosure": "open"},
            "disclosure",
            "Refused by the Engine",
        ),
        (
            "validation_error",
            {"field": "body.proposition.valid_time"},
            {},
            "proposition",
            "Refused by the Engine",
        ),
    ],
)
def test_field_refusals(
    operator_client: Client,
    engine: FakeEngine,
    code: str,
    details: dict[str, Any],
    fields: dict[str, str],
    where: str,
    shown: str,
) -> None:
    refuse(engine, 422, code, **details)
    html = post(operator_client, **fields)
    assert errors_of(html, where) == [shown]
    assert rendered_key(html) != KEY
    assert textarea(html) == PROPOSITION


@pytest.mark.parametrize(
    ("code", "details", "shown"),
    [
        (
            "nesting_too_deep",
            {"depth": 3, "limit": 2, "field": "attributed_to"},
            "Too deep: this story allows depth 2 (a first-order belief is 1; this was 3)",
        ),
        (
            "validation_error",
            {"field": "attributed_holder", "reason": "unknown_character"},
            UNKNOWN_CHARACTER,
        ),
        (
            "validation_error",
            {"field": "attributed_holder", "reason": "attributed_self"},
            NOT_THE_HOLDER,
        ),
    ],
)
def test_nested_refusals_on_the_fieldset(
    operator_client: Client, engine: FakeEngine, code: str, details: dict[str, Any], shown: str
) -> None:
    refuse(engine, 422, code, **details)
    html = post(operator_client, attributed_to_1=BOB)
    assert nested_errors(html) == [shown]
    assert rendered_key(html) != KEY
    assert re.search(rf'<option value="{BOB}" selected>', fieldset(html))


def test_unplaced_422_above_the_form(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "validation_error")
    html = post(operator_client)
    assert "Refused by the Engine" in text(html.split('<div class="errors" role="alert">')[1])


def test_nested_module_off_regreys_fieldset(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "module_off", module="nested_beliefs")
    html = post(operator_client, attributed_to_1=BOB)
    assert refusal(html).startswith("Needs the nested_beliefs module")
    assert 'disabled aria-describedby="gate-nested"' in fieldset(html)
    assert "Not available for this story" not in html
    assert 'class="belief-form"' in html


def test_partitions_module_off_is_the_greyed_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, 409, "module_off", module="partitions")
    html = post(operator_client)
    assert "Not available for this story" in html
    assert "The story's configuration changed since this page loaded" in unescape(html)
    assert 'class="belief-form"' not in html


@pytest.mark.parametrize(
    ("status", "code", "details", "shown"),
    [
        (409, "story_archived", {}, "This story is archived — unarchive it to direct a belief."),
        (409, "story_paused", {"job_id": P + "JB01"}, "The story is paused for a check"),
        (423, "story_busy", {"reason": "in_flight"}, "Story busy — another turn is running"),
        (
            409,
            "revision_conflict",
            {"current_revision": 9},
            "The story changed since you loaded this page (now revision 9) — reload",
        ),
        (
            409,
            "erased_subject_referenced",
            {},
            "This text refers to an erased subject — nothing was saved",
        ),
    ],
)
def test_shared_refusals_above_the_form(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    shown: str,
) -> None:
    refuse(engine, status, code, **details)
    html = post(operator_client)
    assert refusal(html).startswith(shown)
    assert rendered_key(html) != KEY
    assert 'class="belief-form"' in html


def test_idempotency_conflict_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "idempotency_conflict")
    html = post(operator_client)
    assert "Engine error 409 idempotency_conflict" in html
    assert 'class="belief-form"' in html


def test_missing_story_is_the_not_found_page(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found")
    html = post(operator_client)
    assert NOT_FOUND in html
    assert 'class="belief-form"' not in html


def test_erasure_pending_is_held(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "erasure_pending")
    response = operator_client.post(
        URL, {"idempotency_key": KEY, "holder": ALICE, "proposition": json.dumps(PROPOSITION)}
    )
    assert response.status_code == 503


def test_other_failure_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "database_unavailable")
    html = post(operator_client)
    assert 'class="engine-error"' in html
    assert html.index('class="engine-error"') < html.index('class="belief-form"')


# --- the pre-fill ---


def test_prefill_from_belief(operator_client: Client, engine: FakeEngine) -> None:
    page = fixture("beliefs_page1")
    page["beliefs"] = page["beliefs"][1:2]  # BE02: believed 0.7
    page["next_after"] = None
    engine.respond(
        "GET",
        BELIEFS,
        200,
        json=page,
        query={"proposition_id": page["beliefs"][0]["proposition_id"]},
    )
    wanted = page["beliefs"][0]["proposition_id"]
    html = get(operator_client, f"?holder= {ALICE.lower()}&proposition_id={wanted.lower()}")
    (read,) = prefill_reads(engine)
    assert dict(read.url.params) == {
        "viewpoint": "omniscient",
        "character": ALICE,
        "limit": "1",
        "proposition_id": wanted,
    }
    assert textarea(html) == page["beliefs"][0]["proposition"]
    assert re.search(r'<option value="believed" selected>', field(html, "confidence_level"))
    assert 'value="0.700"' in field(html, "confidence_scalar")
    assert re.search(
        r'<option value="" selected>\(as in the proposition\)', field(html, "polarity")
    )
    assert re.search(rf'<option value="{ALICE}" selected>', field(html, "holder"))
    assert escape(PREFILL_FAILED) not in html


@pytest.mark.parametrize("failure", ["error", "empty"])
def test_prefill_failure_still_renders(
    operator_client: Client, engine: FakeEngine, failure: str
) -> None:
    if failure == "error":
        engine.respond("GET", BELIEFS, 503, json=error_json("database_unavailable"))
    else:
        engine.respond("GET", BELIEFS, 200, json=fixture("beliefs_empty"))
    html = get(operator_client, f"?holder={ALICE}&proposition_id={PP01}")
    assert len(prefill_reads(engine)) == 1
    assert escape(PREFILL_FAILED) in html
    assert 'class="belief-form"' in html


@pytest.mark.parametrize(
    "query",
    [
        f"?proposition_id={PP01}",
        f"?holder=nope&proposition_id={PP01}",
        f"?holder={ALICE}&proposition_id=bad",
    ],
)
def test_prefill_without_holder_makes_no_read(
    operator_client: Client, engine: FakeEngine, query: str
) -> None:
    html = get(operator_client, query)
    assert prefill_reads(engine) == []
    assert escape(PREFILL_FAILED) in html
    assert 'class="belief-form"' in html


def test_plain_get_makes_no_read(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"?holder={ALICE}")
    assert prefill_reads(engine) == []
    assert escape(PREFILL_FAILED) not in html


# --- the Beliefs section's links ---


def test_beliefs_section_links_direct_and_change(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(BELIEFS_URL).content.decode()
    assert f'<a href="{URL}?holder={ALICE}">Direct a belief…</a>' in html
    change = f"{URL}?holder={ALICE}&amp;proposition_id={PP01}"
    assert f'<a class="button belief-change" href="{change}">Change…</a>' in html


def test_beliefs_section_links_direct_without_a_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    html = operator_client.get(BELIEFS_URL).content.decode()
    assert f'<a href="{URL}">Direct a belief…</a>' in html


def test_no_verified_in_new_templates() -> None:
    for name in ("belief_direct.html", "belief_written.html", "_belief_actions.html"):
        assert "verified" not in (TEMPLATES / name).read_text().lower()
