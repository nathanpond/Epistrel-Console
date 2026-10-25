"""Change a story's rating: relax at once, tighten after a live history check (#80)."""

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.engine.client import EngineClient, EngineError
from console.engine.models import CheckViolation
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.story_pause import paused_refusal
from console.templatetags.principals import principal_link
from console.views import history as history_views
from console.views import play as play_views
from console.views import stories as story_views
from console.views import story_rating as views
from console.views import story_settings as settings_views
from console.views import timeline as timeline_views
from console.views.story_rating import (
    ALREADY_RUNNING,
    CANT_COMPARE,
    CHOOSE,
    CURRENT_UNREAD,
    NAMES_UNAVAILABLE,
    NOT_FOUND,
    RETIRED_TEXT,
    direction,
    violation_row,
)
from tests.engine_panel.helpers import form_data
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.history.helpers import HISTORY, VARIANTS, history_json, settings_json, variants_json
from tests.history.helpers import SETTINGS as STORY_SETTINGS
from tests.play.helpers import STORY, error_json, story_json, turn_data
from tests.play.helpers import answer_page as answer_play
from tests.ratings.helpers import EVERYONE, EXPLICIT, MATURE, TEEN, rating
from tests.settings.helpers import answer_page as answer_settings
from tests.settings.helpers import section

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/rating/"
CONFIRM = f"/stories/{STORY}/rating/confirm/"
JOB = "01JB2Z8Y5Q3W6K9M1N4P7R0J0B"
CHECK_URL = f"/stories/{STORY}/rating/checks/{JOB}/"
OP = "01JB2Z8Y5Q3W6K9M1N4P7R0OPR"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KYR"
SIDEWAYS = "01M5R0SIDE0000000000000001"
STORY_PATH = "/stories/{story_id}"
LIST = "/admin/content-ratings"
ITEM = "/admin/content-ratings/{rating_id}"
PUT = "/stories/{story_id}/content-rating"
CHECK = "/stories/{story_id}/content-rating/checks/{job_id}"
HTMX = {"HX-Request": "true"}
# Each rating's effective guardrails: Everyone ⊃ Teen ⊃ Mature ⊃ Explicit; Sideways swaps one.
GUARDRAILS: dict[str, tuple[str, ...]] = {
    EVERYONE: ("no_graphic_violence", "no_profanity", "no_romance"),
    TEEN: ("no_graphic_violence", "no_profanity"),
    MATURE: ("no_graphic_violence",),
    EXPLICIT: (),
    SIDEWAYS: ("no_graphic_violence", "no_gambling"),
}
PAUSE = {"job_id": JOB, "cause": "rating_check", "progress": {"done": 3, "total": 10}}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine behind every view a rating change touches (the real recording client)."""
    fake = FakeEngine()
    answer_relations(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    modules = (views, play_views, settings_views, history_views, story_views, timeline_views)
    for module in modules:
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def sideways() -> dict[str, Any]:
    return {**rating("Mature"), "id": SIDEWAYS, "name": "Sideways", "display_order": 35}


def answer(
    engine: FakeEngine,
    *,
    story: dict[str, Any] | None = None,
    failing: frozenset[str] = frozenset(),
    listed: list[dict[str, Any]] | None = None,
) -> None:
    """The rating page's reads: the story (Teen), the non-retired list and each detail
    (``failing`` ids answer 503)."""
    engine.respond("GET", STORY_PATH, 200, json=story or story_json())
    items = listed if listed is not None else [rating(n) for n in ("Everyone", "Teen", "Mature")]
    engine.respond("GET", LIST, 200, json={"items": items})

    def detail(request: httpx.Request) -> httpx.Response:
        rating_id = request.url.path.rsplit("/", 1)[-1]
        if rating_id in failing:
            return httpx.Response(503, json=error_json("database_unavailable"))
        found = next(
            (i for i in [*items, sideways(), rating("Explicit")] if i["id"] == rating_id),
            rating("Teen"),
        )
        guardrails = [
            {"policy_id": p, "source_rating_id": rating_id} for p in GUARDRAILS[rating_id]
        ]
        return httpx.Response(200, json={**found, "effective_guardrails": guardrails})

    engine.side_effect("GET", ITEM, detail)


def check_json(status: str = "checking", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "tightening",
        "job_id": JOB,
        "story_id": STORY,
        "status": status,
        "progress": {"done": 3, "total": 10},
        "started_at": "2026-10-24T21:40:00Z",
        "started_by": "console:nate",
        "finished_at": None,
        "target": {"rating_id": EVERYONE, "version": "00000000000000000000RTV001"},
        "from_rating_id": TEEN,
        "operation_id": OP,
        "rating_version": None,
        "config_revision": None,
        "violations": [],
        "error": None,
        "outcome": None,
    }
    body.update(changes)
    return body


def started_json() -> dict[str, Any]:
    return {
        "timeline_revision": 1,
        "job_id": JOB,
        "operation_id": OP,
        "status": "checking",
        "config_revision": 4,
        "idempotency_key": KEY,
    }


def applied_json(
    rating_id: str = MATURE, *, changed: bool = True, **changes: Any
) -> dict[str, Any]:
    body = {
        "timeline_revision": 1,
        "applied": True,
        "changed": changed,
        "content_rating_id": rating_id,
        "rating_version": rating({EVERYONE: "Everyone", TEEN: "Teen", MATURE: "Mature"}[rating_id])[
            "version_id"
        ],
        "config_revision": 5,
        "idempotency_key": KEY,
    }
    body.update(changes)
    return body


def calls(engine: FakeEngine, method: str, suffix: str = "") -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == method and r.url.path.endswith(suffix)]


def puts(engine: FakeEngine) -> list[httpx.Request]:
    return calls(engine, "PUT", "/content-rating")


def option_text(html: str, rating_id: str) -> str:
    match = re.search(rf'<option value="{rating_id}"[^>]*>([^<]*)</option>', html)
    assert match is not None, rating_id
    return match.group(1)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in response.wsgi_request._messages]


def post_rating(client: Client, rating_id: str, **extra: str) -> Any:
    data = {"rating": rating_id, "current": TEEN, "idempotency_key": KEY, **extra}
    return client.post(URL, data)


def confirm_token(response: Any) -> str:
    location: str = response["Location"]
    return parse_qs(urlsplit(location).query)["t"][0]


def start_check(client: Client, engine: FakeEngine) -> None:
    """Tighten to Everyone and confirm it: the 202 leaves the #44 row ``queued``."""
    answer(engine)
    token = confirm_token(post_rating(client, EVERYONE))
    engine.respond("PUT", PUT, 202, json=started_json())
    response = client.post(CONFIRM, {"confirm_token": token, "idempotency_key": KEY})
    assert response.status_code == 302
    assert response["Location"] == CHECK_URL


# --- relax or tighten -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        ({"a", "b"}, {"a"}, "relax"),  # subset
        ({"a", "b"}, {"a", "b"}, "relax"),  # equal
        ({"a"}, {"a", "b"}, "tighten"),  # superset
        ({"a", "b"}, {"a", "c"}, "tighten"),  # sideways
        (None, {"a"}, "unknown"),
        ({"a"}, None, "unknown"),
    ],
)
def test_direction_compares_guardrail_sets(
    current: set[str] | None, target: set[str] | None, expected: str
) -> None:
    def frozen(value: set[str] | None) -> frozenset[str] | None:
        return frozenset(value) if value is not None else None

    assert direction(frozen(current), frozen(target)) == expected


def test_the_page_labels_each_rating_by_its_guardrails(
    operator_client: Client, engine: FakeEngine
) -> None:
    listed = [rating(n) for n in ("Everyone", "Teen", "Mature")] + [sideways()]
    answer(engine, listed=listed)

    html = operator_client.get(URL).content.decode()

    assert option_text(html, MATURE) == "Mature (v1) — relaxes — applies at once"
    assert option_text(html, EVERYONE) == "Everyone (v1) — tightens — checks history first"
    assert option_text(html, SIDEWAYS) == "Sideways (v1) — tightens — checks history first"
    assert option_text(html, TEEN) == "Teen (v1) — current — no change"
    assert re.search(rf'<option value="{TEEN}" selected>', html)
    assert "Current rating" in html
    assert ">Teen</a>" in html
    # One list read (non-retired), then the current and each listed rating's detail.
    assert len(calls(engine, "GET", "/admin/content-ratings")) == 1
    assert "include_retired" not in str(calls(engine, "GET", "/admin/content-ratings")[0].url)
    assert len([r for r in engine.requests if "/admin/content-ratings/" in r.url.path]) == 4


def test_a_failed_rating_read_labels_unknown(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, failing=frozenset({EVERYONE}))

    html = operator_client.get(URL).content.decode()

    assert option_text(html, EVERYONE) == "Everyone (v1) — unknown — treated as tighten"
    assert option_text(html, MATURE) == "Mature (v1) — relaxes — applies at once"
    assert CURRENT_UNREAD not in html


def test_a_failed_current_read_makes_every_change_unknown(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, failing=frozenset({TEEN}))

    html = operator_client.get(URL).content.decode()

    assert option_text(html, MATURE) == "Mature (v1) — unknown — treated as tighten"
    assert option_text(html, EVERYONE) == "Everyone (v1) — unknown — treated as tighten"
    assert CURRENT_UNREAD.replace("'", "&#x27;") in html


def test_an_unknown_target_requires_the_confirm(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, failing=frozenset({MATURE}))

    response = post_rating(operator_client, MATURE)

    assert response["Location"].startswith(CONFIRM)
    assert puts(engine) == []
    page = operator_client.get(response["Location"]).content.decode()
    assert CANT_COMPARE.replace("'", "&#x27;") in page


def test_a_retired_current_rating_stays_selected(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = story_json(
        content_rating={"id": EXPLICIT, "version_id": "00000000000000000000RTV004", "name": "Gone"}
    )
    answer(engine, story=story)

    html = operator_client.get(URL).content.decode()

    assert re.search(rf'<option value="{EXPLICIT}" selected>Gone \(retired\)', html)
    assert "— current — no change" in option_text(html, EXPLICIT)
    assert option_text(html, MATURE) == "Mature (v1) — tightens — checks history first"


def test_a_failed_list_read_shows_the_panel_instead_of_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json())
    engine.respond("GET", LIST, 503, json=error_json("database_unavailable"))

    html = operator_client.get(URL).content.decode()

    assert "database_unavailable" in html
    assert 'class="rating-form"' not in html


# --- saving ---------------------------------------------------------------------------------------


def test_a_relax_applies_at_once_without_a_confirm(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond("PUT", PUT, 200, json=applied_json())

    response = post_rating(operator_client, MATURE)

    assert response.status_code == 302
    assert response["Location"] == URL
    assert len(puts(engine)) == 1
    assert puts(engine)[0].headers["Idempotency-Key"] == KEY
    assert flashes(response) == ["Rating changed to Mature (version 1)"]
    row = EngineOperation.objects.get(kind="set_story_content_rating")
    assert (row.outcome, row.entity_type, row.entity_id) == ("ok", "story", STORY)


def test_a_tighten_goes_to_the_confirm_and_the_confirm_puts(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)

    response = post_rating(operator_client, EVERYONE)

    assert response["Location"].startswith(CONFIRM)
    assert puts(engine) == []
    engine.reset()
    page = operator_client.get(response["Location"]).content.decode()
    assert engine.requests == []  # the confirm page makes no Engine call
    assert (
        "The story is paused while every past turn, ingested event, pinned memory and custom "
        "instruction is checked against <em>Everyone</em>. Turns are refused until it finishes."
    ) in page
    assert "<code>no_romance</code>" in page.split("Policies this adds")[1].split("drops")[0]
    assert "None." in page.split("Policies this drops")[1]

    engine.respond("PUT", PUT, 202, json=started_json())
    data = form_data(page, CONFIRM)
    confirmed = operator_client.post(CONFIRM, data)

    assert confirmed["Location"] == CHECK_URL
    assert [r.method for r in engine.requests] == ["GET", "PUT"]  # one story read, then the PUT
    assert flashes(confirmed) == [f"Rating check started — operation {OP}, idempotency key {KEY}"]
    row = EngineOperation.objects.get(kind="set_story_content_rating")
    assert (row.outcome, row.job_id) == ("queued", JOB)


def test_a_forged_relax_still_lands_on_the_confirm(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)

    response = post_rating(operator_client, EVERYONE, direction="relax", confirm="1")

    assert response["Location"].startswith(CONFIRM)
    assert puts(engine) == []


def test_choosing_the_current_rating_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post_rating(operator_client, TEEN)

    assert response["Location"] == URL
    assert engine.requests == []
    assert flashes(response) == ["No change"]


def test_an_unchanged_answer_says_no_change(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    engine.respond("PUT", PUT, 200, json=applied_json(changed=False))

    response = post_rating(operator_client, MATURE)

    assert response["Location"] == URL
    assert flashes(response) == ["No change"]


def test_a_202_on_a_relax_goes_to_the_check(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    engine.respond("PUT", PUT, 202, json=started_json())

    response = post_rating(operator_client, MATURE)

    assert response["Location"] == CHECK_URL


def test_a_rating_not_in_the_list_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)

    response = post_rating(operator_client, EXPLICIT)

    assert response.status_code == 200
    assert CHOOSE in response.content.decode()
    assert puts(engine) == []


def test_an_expired_or_foreign_token_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    other = twostage.sign(
        "story_rating", "01JB2Z8Y5Q3W6K9M1N4P7R0OTH", {"rating_id": EVERYONE}, {"current": TEEN}
    )
    for token in ("garbage", other):
        response = operator_client.get(f"{CONFIRM}?t={token}")
        assert response["Location"] == URL
        assert flashes(response)[-1] == twostage.EXPIRED_TEXT
        posted = operator_client.post(CONFIRM, {"confirm_token": token})
        assert posted["Location"] == URL
    assert engine.requests == []


def test_a_rating_changed_since_the_confirm_is_expired(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    token = confirm_token(post_rating(operator_client, EVERYONE))
    mature = {"id": MATURE, "version_id": "00000000000000000000RTV003", "name": "Mature"}
    engine.respond("GET", STORY_PATH, 200, json=story_json(content_rating=mature))

    response = operator_client.post(CONFIRM, {"confirm_token": token})

    assert response["Location"] == URL
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert puts(engine) == []


def test_story_paused_on_put_links_the_running_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond(
        "PUT", PUT, 409, json=error_json("story_paused", "paused", story_id=STORY, job_id=JOB)
    )

    html = post_rating(operator_client, MATURE).content.decode()

    assert f'<a class="refusal-link" href="{CHECK_URL}">{ALREADY_RUNNING}</a>' in html


def test_story_paused_without_a_job_reads_the_story_for_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond("PUT", PUT, 409, json=error_json("story_paused", "paused"))
    engine.respond_sequence(
        "GET",
        STORY_PATH,
        [
            {"status": 200, "json": story_json()},
            {"status": 200, "json": story_json(status="paused", pause=PAUSE)},
        ],
    )

    html = post_rating(operator_client, MATURE).content.decode()

    assert f'<a class="refusal-link" href="{CHECK_URL}">{ALREADY_RUNNING}</a>' in html


def test_rating_retired_sits_at_the_select(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    engine.respond("PUT", PUT, 422, json=error_json("rating_retired", "retired"))

    html = post_rating(operator_client, MATURE).content.decode()

    field = html.split('class="field field-rating"')[1].split("</div>")[0]
    assert RETIRED_TEXT in field
    assert len(calls(engine, "GET", "/admin/content-ratings")) == 2  # the list is re-read


def test_story_busy_says_when_to_retry(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    engine.respond(
        "PUT",
        PUT,
        423,
        json=error_json("story_busy", "busy", reason="in_flight"),
        headers={"Retry-After": "5"},
    )

    html = post_rating(operator_client, MATURE).content.decode()

    assert "A turn is running — try again in 5 s" in html


def test_an_archived_story_disables_the_form(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, story=story_json(status="archived", archived_at="2026-10-24T22:00:00Z"))

    html = operator_client.get(URL).content.decode()

    assert "Archived — unarchive to change its rating" in html
    assert "<fieldset disabled>" in html
    assert option_text(html, MATURE) == "Mature (v1)"  # no labels: the details were not read
    assert [r for r in engine.requests if "/admin/content-ratings/" in r.url.path] == []


def test_a_paused_story_disables_the_form_and_links_the_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, story=story_json(status="paused", pause=PAUSE))

    html = operator_client.get(URL).content.decode()

    form = html.split('class="rating-form"')[0].split("current-rating")[1]
    assert f'<a href="{CHECK_URL}">A rating check is running</a>' in form
    assert "<fieldset disabled>" in html
    assert 'id="pause-banner"' in html


# --- the check page -------------------------------------------------------------------------------


def test_the_check_page_polls_while_checking(operator_client: Client, engine: FakeEngine) -> None:
    start_check(operator_client, engine)
    engine.respond("GET", CHECK, 200, json=check_json())
    engine.respond("GET", LIST, 200, json={"items": [rating("Everyone"), rating("Teen")]})

    html = operator_client.get(CHECK_URL).content.decode()

    assert 'hx-trigger="every 2s"' in html
    assert '<progress value="3" max="10">' in html
    assert "3/10" in html
    assert 'class="check-target">Everyone (version 1)' in html
    assert 'class="check-from">Teen' in html
    assert f"<code>{OP}</code>" in html
    assert str(principal_link("console:nate")) in html
    assert "include_retired=1" in str(calls(engine, "GET", "/admin/content-ratings")[-1].url)
    assert EngineOperation.objects.get(kind="set_story_content_rating").outcome == "queued"


def test_polling_stops_at_a_terminal_status(operator_client: Client, engine: FakeEngine) -> None:
    start_check(operator_client, engine)
    engine.reset()
    engine.respond(
        "GET",
        CHECK,
        200,
        json=check_json(
            "applied",
            progress={"done": 10, "total": 10},
            rating_version="00000000000000000000RTV001",
            config_revision=6,
            finished_at="2026-10-24T21:45:00Z",
        ),
    )
    poll = f"{CHECK_URL}?t_name=Everyone&t_vid=00000000000000000000RTV001&t_v=1"

    html = operator_client.get(poll, headers=HTMX).content.decode()

    assert "hx-trigger" not in html
    assert "Rating changed to <em>Everyone</em>" in html
    assert "<dt>Rating version</dt><dd>1</dd>" in html
    assert "<dt>Config revision</dt><dd>6</dd>" in html
    assert "Back to rating" in html
    assert "Back to story" in html
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/content-rating/checks/{JOB}"
    ]
    row = EngineOperation.objects.get(kind="set_story_content_rating")
    assert (row.outcome, row.error_code) == ("ok", "")
    assert isinstance(row.response, dict)
    assert row.response["status"] == "applied"


def test_an_unresolved_version_shows_its_id(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", CHECK, 200, json=check_json("applied", rating_version="01M5R0RTVOLD00000000000001")
    )

    html = operator_client.get(f"{CHECK_URL}?t_name=Everyone", headers=HTMX).content.decode()

    assert "<code>01M5R0RTVOLD00000000000001</code>" in html


def test_the_check_header_without_names(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json())
    engine.respond("GET", CHECK, 200, json=check_json())
    engine.respond("GET", LIST, 503, json=error_json("database_unavailable"))

    html = operator_client.get(CHECK_URL).content.decode()

    assert NAMES_UNAVAILABLE in html
    assert f'class="check-target"><code>{EVERYONE}</code>' in html
    assert f'class="check-from"><code>{TEEN}</code>' in html


def test_the_refused_table_shows_where_and_which_policy_never_the_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    start_check(operator_client, engine)
    violations = [
        {
            "position": {"turn": 3, "variant": 1, "beat": 2},
            "policy_id": "no_graphic_violence",
            "policy_name": "No graphic violence",
            "code": None,
            "text": "SECRET OFFENDING TEXT",
        },
        {
            "position": {"kind": "pinned_memory", "id": "01PINNED"},
            "policy_id": None,
            "policy_name": None,
            "code": "model_unavailable",
        },
        {"position": {"odd": True}, "policy_id": None, "policy_name": "Named only"},
    ]
    engine.respond("GET", CHECK, 200, json=check_json("refused", violations=violations))

    html = operator_client.get(CHECK_URL, headers=HTMX).content.decode()

    assert "SECRET OFFENDING TEXT" not in html
    assert "The rating was not changed" in html
    assert "Violations (3)" in html
    history = f"/stories/{STORY}/history/?as_of=3&amp;variant=3%3A1#turn-3"
    assert f'<a href="{history}">turn 3, variant 1, beat 2</a>' in html
    assert (
        '<a href="/guardrails/policies/no_graphic_violence/"><code>no_graphic_violence</code></a>'
        in html
    )
    assert "pinned_memory <code>01PINNED</code>" in html
    assert "— (could not be checked: <code>model_unavailable</code>)" in html
    assert "Named only" in html
    assert "<summary>More</summary><pre>{&quot;odd&quot;: true}</pre>" in html
    assert "hx-trigger" not in html
    row = EngineOperation.objects.get(kind="set_story_content_rating")
    assert (row.outcome, row.error_code) == ("error", "rating_refused")
    assert "SECRET OFFENDING TEXT" not in str(row.response)


def test_a_violation_parse_drops_unknown_keys() -> None:
    parsed = CheckViolation.from_json(
        {"position": {"turn": 1}, "policy_id": None, "policy_name": None, "text": "x"}
    )
    assert parsed is not None
    row = violation_row(STORY, parsed)
    assert row.position == "—"
    assert row.raw == '{"turn": 1}'
    assert "x" not in repr(parsed)


def test_a_failed_check_shows_its_error(operator_client: Client, engine: FakeEngine) -> None:
    start_check(operator_client, engine)
    error = {"class": "model_unavailable", "message": "the model did not answer"}
    engine.respond("GET", CHECK, 200, json=check_json("failed", error=error))

    html = operator_client.get(CHECK_URL, headers=HTMX).content.decode()

    assert "<code>model_unavailable</code>" in html
    assert "the model did not answer" in html
    row = EngineOperation.objects.get(kind="set_story_content_rating")
    assert (row.outcome, row.error_code) == ("error", "model_unavailable")


def test_an_unknown_status_keeps_polling(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CHECK, 200, json=check_json("pondering"))

    html = operator_client.get(CHECK_URL, headers=HTMX).content.decode()

    assert 'hx-trigger="every 2s"' in html
    assert "<code>pondering</code>" in html


def test_a_starting_check_shows_an_indeterminate_progress(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", CHECK, 200, json=check_json(progress={"done": 0, "total": 0}))

    html = operator_client.get(CHECK_URL, headers=HTMX).content.decode()

    assert "<progress></progress>" in html
    assert "starting…" in html


def test_a_check_404_says_it_expired(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json())
    engine.respond("GET", CHECK, 404, json=error_json("not_found", "no such check"))

    response = operator_client.get(CHECK_URL)

    assert response.status_code == 404
    assert NOT_FOUND in response.content.decode()
    assert "hx-trigger" not in response.content.decode()


# --- the pause banner -----------------------------------------------------------------------------


def test_the_banner_poll_refreshes_once_the_check_ends(
    operator_client: Client, engine: FakeEngine
) -> None:
    start_check(operator_client, engine)
    engine.respond("GET", CHECK, 200, json=check_json("refused"))

    response = operator_client.get(f"{CHECK_URL}?banner=1&cause=rating_check", headers=HTMX)

    assert response["HX-Refresh"] == "true"
    row = EngineOperation.objects.get(kind="set_story_content_rating")
    assert (row.outcome, row.error_code) == ("error", "rating_refused")


def test_the_banner_poll_keeps_polling_while_checking(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", CHECK, 200, json=check_json(progress={"done": 7, "total": 10}))

    response = operator_client.get(f"{CHECK_URL}?banner=1&cause=rating_check", headers=HTMX)
    html = response.content.decode()

    assert "HX-Refresh" not in response
    assert 'hx-trigger="every 2s"' in html
    assert "Paused: rating check in progress (7/10)" in html


def test_a_failed_banner_poll_stops_and_says_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", CHECK, 404, json=error_json("not_found", "gone"))

    html = operator_client.get(f"{CHECK_URL}?banner=1", headers=HTMX).content.decode()

    assert "Paused — check status unavailable" in html
    assert "hx-trigger" not in html
    assert "Refresh</a>" in html


def test_the_play_view_shows_the_banner_and_disables_its_controls(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_play(engine, story=story_json(status="paused", pause=PAUSE))

    html = operator_client.get(f"/stories/{STORY}/").content.decode()

    banner = html.split('id="pause-banner"')[1].split("</div>")[0]
    assert (
        f'<a class="pause-check" href="{CHECK_URL}">Paused: rating check in progress (3/10)</a>'
        in banner
    )
    assert f'hx-get="{CHECK_URL}?banner=1&amp;cause=rating_check"' in html
    assert '<dd class="status">Paused</dd>' in html
    assert "Paused — turns are refused until the check finishes" in html
    turn_form = html.split('id="turn-form"')[1].split("</form>")[0]
    assert "<fieldset disabled>" in turn_form
    assert 'class="button fork"' not in html
    assert 'class="fork" disabled title="Paused while the check runs"' in html
    # Archive and unarchive disabled; delete stays enabled; the ownership panel stays.
    assert '<button type="button" disabled>Archive</button>' in html
    assert f"/stories/{STORY}/archive/" not in html
    assert f'href="/stories/{STORY}/delete/' in html
    assert f'class="rating-link" href="{URL}"' in html


def test_no_banner_when_not_paused(operator_client: Client, engine: FakeEngine) -> None:
    answer_play(engine)

    html = operator_client.get(f"/stories/{STORY}/").content.decode()

    assert 'id="pause-banner"' not in html
    assert "Paused — turns are refused" not in html
    assert 'class="button fork"' in html
    assert f"/stories/{STORY}/archive/" in html


def test_a_banner_without_a_job_neither_polls_nor_links(
    operator_client: Client, engine: FakeEngine
) -> None:
    pause = {"job_id": None, "cause": "maintenance", "progress": {"done": 0, "total": 0}}
    answer_play(engine, story=story_json(status="paused", pause=pause))

    html = operator_client.get(f"/stories/{STORY}/").content.decode()

    banner = html.split('id="pause-banner"')[1].split("</div>")[0]
    assert "Paused: maintenance (starting…)" in banner
    assert "hx-get" not in banner
    assert "pause-check" not in banner
    assert "Refresh</a>" in banner


def test_a_forced_turn_shows_story_paused(operator_client: Client, engine: FakeEngine) -> None:
    progress = {"done": 3, "total": 10}
    engine.respond(
        "POST",
        "/stories/{story_id}/respond",
        409,
        json=error_json("story_paused", "paused", story_id=STORY, job_id=JOB, progress=progress),
    )

    html = operator_client.post(
        f"/stories/{STORY}/turn/", turn_data(), headers=HTMX
    ).content.decode()

    assert (
        f'<a class="notice-link" href="{CHECK_URL}">The story is paused for a check (3/10)</a>'
        in html
    )


def test_a_paused_refusal_without_a_job_is_unlinked() -> None:
    found = paused_refusal(EngineError("story_paused", "paused", {}, 409), STORY)
    assert (found.text, found.url) == ("The story is paused for a check", "")


def test_the_settings_page_shows_the_banner_and_the_rating_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine, story=story_json(status="paused", pause=PAUSE))

    html = operator_client.get(f"/stories/{STORY}/settings/").content.decode()

    assert 'id="pause-banner"' in html
    assert f'<a class="change-rating" href="{URL}">Change rating</a>' in html
    assert "<fieldset disabled>" not in section(html, "pinned")  # pinned memories stay enabled


def test_story_paused_on_a_settings_save_shows_the_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine)
    html = operator_client.get(f"/stories/{STORY}/settings/").content.decode()
    engine.respond(
        "PATCH",
        "/stories/{story_id}/settings",
        409,
        json=error_json(
            "story_paused", "paused", story_id=STORY, job_id=JOB, progress=PAUSE["progress"]
        ),
    )
    data = form_data(html, f"/stories/{STORY}/settings/")
    data["regenerate_retry_budget"] = "7"

    page = operator_client.post(f"/stories/{STORY}/settings/", data).content.decode()

    assert f'href="{CHECK_URL}">The story is paused for a check (3/10)</a>' in page


def test_the_history_page_disables_variants_while_paused(
    operator_client: Client, engine: FakeEngine
) -> None:
    chains = [
        {
            "turn": 1,
            "variant": 1,
            "membership": "active",
            "tip_turn": 1,
            "turn_count": 1,
            "membership_tx": None,
            "parent": None,
            "requires_check": False,
        }
    ]
    story = story_json(status="paused", pause=PAUSE, next_turn_index=2, variant_chains=chains)
    engine.respond("GET", STORY_PATH, 200, json=story)
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, 200, json=history_json())
    engine.respond("GET", STORY_SETTINGS, 200, json=settings_json())

    html = operator_client.get(f"/stories/{STORY}/history/").content.decode()

    assert 'id="pause-banner"' in html
    assert 'class="button fork"' not in html
    assert "Paused while the check runs" in html
