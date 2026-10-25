"""Follow rating re-checks when undo, swap or fork reactivates turns (#81).

Undo rewind, swap and fork can answer 202: the turns they would make active were checked under
older guardrails, so the Engine holds the edit behind a ``kind: reactivation`` check and pauses
the story. The Console redirects to #80's check page with ``?op=``, follows the check and shows
its ending: the edit's answer (``reactivated``), the violations (``refused``) or the error
(``failed``), settling the #44 row the 202 recorded ``queued``."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine.models import Story
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.reactivation import heading, parse_outcome, shown_op
from console.story_pause import pause_banner
from console.views import history as history_views
from console.views import play as play_views
from console.views import stories as story_views
from console.views import story_rating as rating_views
from console.views import timeline as timeline_views
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.fixtures import reactivation as outcomes
from tests.play.helpers import STORY, answer_page, story_json
from tests.ratings.helpers import EVERYONE, TEEN, rating
from tests.timeline.helpers import KEY, path, posted, recheck_json, url

pytestmark = pytest.mark.django_db

JOB = recheck_json()["job_id"]
CHECK_PATH = "/stories/{story_id}/content-rating/checks/{job_id}"
CHECK_PAGE = f"/stories/{STORY}/rating/checks/{JOB}/"
HTMX = {"HX-Request": "true"}
WAITING = "The story is paused until the check ends — turns can&#x27;t be played meanwhile."
FAILED_LINE = "The edit could no longer be made. The story is unchanged."
NEW = outcomes.FORKED["result"]["story_id"]
OPS = {"untruncate": "Undo rewind", "swap": "Swap", "fork": "Fork"}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine behind the timeline handlers, the check page and the story pages (the
    real recording client, so the 202 lands in the operation log)."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)  # #85: the play header's fingerprint

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    modules = (timeline_views, rating_views, play_views, history_views, story_views)
    for module in modules:
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def check_json(status: str = "checking", **changes: Any) -> dict[str, Any]:
    """A ``kind: reactivation`` check (``RatingCheckBody``): from and target are the story's
    own rating at its current version."""
    body: dict[str, Any] = {
        "kind": "reactivation",
        "job_id": JOB,
        "story_id": STORY,
        "status": status,
        "progress": {"done": 2, "total": 4},
        "started_at": "2026-10-25T09:00:00Z",
        "started_by": "console:nate",
        "finished_at": None if status == "checking" else "2026-10-25T09:01:00Z",
        "target": {"rating_id": TEEN, "version": rating("Teen")["version_id"]},
        "from_rating_id": TEEN,
        "operation_id": recheck_json()["operation_id"],
        "rating_version": None,
        "config_revision": None,
        "violations": [],
        "error": None,
        "outcome": None,
    }
    body.update(changes)
    return body


def answer_check(engine: FakeEngine, check: dict[str, Any], *, story: Any = None) -> None:
    """The check page's reads: the story, the check and the rating names."""
    engine.respond("GET", "/stories/{story_id}", 200, json=story or story_json())
    engine.respond("GET", CHECK_PATH, 200, json=check)
    engine.respond(
        "GET", "/admin/content-ratings", 200, json={"items": [rating("Everyone"), rating("Teen")]}
    )


def hold(client: Client, engine: FakeEngine, op: str) -> Any:
    """POST ``op`` (its confirm, or the fork form) against a 202."""
    action = {"untruncate": "undo-rewind", "swap": "swap", "fork": "fork"}[op]
    engine.respond("POST", path(op), 202, json=recheck_json())
    if op == "untruncate":
        return client.post(url("undo-rewind"), posted())
    if op == "swap":
        return client.post(url(action, chain="3:2"), posted(chain="3:2"))
    return client.post(url("fork", 3), posted(new_name="Side", variant_chain="3:2"))


def page(client: Client, target: str, **headers: str) -> str:
    response = client.get(target, headers=headers)
    assert response.status_code == 200, response.status_code
    return response.content.decode()


def flashes(response: Any) -> list[str]:
    return [str(m) for m in response.wsgi_request._messages]


def row() -> EngineOperation:
    return EngineOperation.objects.get(job_id=JOB)


# --- the 202 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["untruncate", "swap", "fork"])
def test_a_202_redirects_to_the_check_page_with_the_op(
    operator_client: Client, engine: FakeEngine, op: str
) -> None:
    response = hold(operator_client, engine, op)

    assert response.status_code == 302
    assert response["Location"] == f"{CHECK_PAGE}?op={op}"
    (line,) = flashes(response)
    started = recheck_json()
    assert line.startswith(
        f"Held for a guardrail re-check: operation {started['operation_id']}, key {KEY}"
    )
    assert ("revision" in line) is (op != "fork")  # a fork's revision is the source's
    for success in ("Rewind undone", "Swapped", "Forked"):  # #53's success flash is not shown
        assert success not in line
    recorded = row()
    assert (recorded.kind, recorded.outcome, recorded.http_status) == (op, "queued", 202)

    answer_check(engine, check_json())
    html = page(operator_client, response["Location"])
    assert f"{OPS[op]} is waiting for a guardrail re-check" in html
    assert WAITING in html
    assert 'hx-trigger="every 2s"' in html
    assert f'hx-get="{CHECK_PAGE}?op={op}' in html  # the poll keeps the op
    assert "Checked against" in html
    assert 'class="check-from"' not in html  # no from-rating on a reactivation
    if op == "fork":  # the name the fork asked for, from the #44 row's request
        assert "Requested new story name: <em>Side</em>" in html
    else:
        assert "Requested new story name" not in html


@pytest.mark.parametrize("op", ["untruncate", "fork"])
def test_held_edits_wait_the_turn_timeout(
    operator_client: Client, engine: FakeEngine, op: str
) -> None:
    hold(operator_client, engine, op)
    (request,) = [r for r in engine.requests if r.method == "POST"]
    assert request.extensions["timeout"]["read"] == 60.0  # 45 s deadline + 15 s grace


def test_a_replayed_202_goes_to_the_same_check_without_a_second_queued_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    first = hold(operator_client, engine, "swap")
    engine.respond(
        "POST", path("swap"), 202, json=recheck_json(), headers={"Idempotent-Replay": "true"}
    )
    again = operator_client.post(url("swap", chain="3:2"), posted(chain="3:2"))

    assert again["Location"] == first["Location"]
    rows = EngineOperation.objects.filter(job_id=JOB).order_by("id")
    assert [(r.outcome, r.replayed) for r in rows] == [("queued", False), ("ok", True)]


def test_a_disagreeing_location_is_logged_and_ignored(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    other = f"/stories/{STORY}/content-rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0XXX"
    engine.respond("POST", path("swap"), 202, json=recheck_json(), headers={"Location": other})
    with caplog.at_level(logging.WARNING, logger="console.views.timeline"):
        response = operator_client.post(url("swap", chain="3:2"), posted(chain="3:2"))
    assert response["Location"] == f"{CHECK_PAGE}?op=swap"
    assert any("disagrees" in r.getMessage() for r in caplog.records)


def test_an_agreeing_location_logs_nothing(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    same = f"/stories/{STORY}/content-rating/checks/{JOB}"
    engine.respond("POST", path("swap"), 202, json=recheck_json(), headers={"Location": same})
    with caplog.at_level(logging.WARNING, logger="console.views.timeline"):
        operator_client.post(url("swap", chain="3:2"), posted(chain="3:2"))
    assert not any("disagrees" in r.getMessage() for r in caplog.records)


# --- reactivated ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "outcome", "head", "variant"),
    [
        ("untruncate", outcomes.UNTRUNCATED, 5, None),
        ("untruncate", outcomes.UNTRUNCATED_SWAPPED, 5, "4:1"),
        ("swap", outcomes.SWAPPED, 4, "4:2"),
    ],
)
def test_reactivated_undo_and_swap_show_the_restored_head(
    operator_client: Client,
    engine: FakeEngine,
    op: str,
    outcome: dict[str, Any],
    head: int,
    variant: str | None,
) -> None:
    hold(operator_client, engine, op)
    answer_check(engine, check_json("reactivated", outcome=outcome))

    html = page(operator_client, f"{CHECK_PAGE}?op={op}")

    result = outcome["result"]
    assert f"{OPS[op]} applied after re-check" in html  # an undo that swapped keeps its name
    assert f"Restored head turn {head}" in html
    query = f"as_of={head}" + (f"&amp;variant={variant.replace(':', '%3A')}" if variant else "")
    assert f'href="/stories/{STORY}/history/?{query}#turn-{head}"' in html
    assert f"<dt>Timeline revision</dt><dd>{result['timeline_revision']}</dd>" in html
    assert f"<code>{result['operation_id']}</code>" in html
    assert f"<dt>Idempotency key</dt><dd><code>{KEY}</code></dd>" in html  # from the #44 row
    if op == "untruncate":
        assert f"<dt>Restored operations</dt><dd>{result['restored_operations']}</dd>" in html
        assert f"<code>{result['truncate_operation_id']}</code>" in html
        assert "<dt>Swapped</dt><dd>" + ("yes" if result["swapped"] else "no") in html
    else:
        assert f"<dt>Activated operations</dt><dd>{result['activated_operations']}</dd>" in html
        assert "<dt>Active chain</dt><dd>turn 4 · variant 2</dd>" in html
    assert "hx-trigger" not in html
    assert "The rating was not changed" not in html
    recorded = row()
    assert (recorded.outcome, recorded.error_code) == ("ok", "")
    assert recorded.engine_operation_id == result["operation_id"]
    assert recorded.timeline_revision == result["timeline_revision"]
    assert recorded.story_id == STORY
    assert isinstance(recorded.response, dict)
    assert recorded.response["status"] == "reactivated"


def test_a_reactivated_fork_links_the_new_story_with_its_banner(
    operator_client: Client, engine: FakeEngine
) -> None:
    hold(operator_client, engine, "fork")
    answer_check(engine, check_json("reactivated", outcome=outcomes.FORKED))

    html = page(operator_client, f"{CHECK_PAGE}?op=fork")

    result = outcomes.FORKED["result"]
    assert "Fork applied after re-check" in html
    assert f'<a href="/stories/{NEW}/">Open the new story</a>' in html
    assert "Forked from <em>" in html
    assert "at turn 2" in html
    for character in result["characters"]:
        assert f"<code>{character['source_id']}</code>" in html
        assert f"<code>{character['id']}</code>" in html
    assert "<dt>New story</dt><dd>Harbour (fork at turn 2)</dd>" in html
    assert "<dt>Variant chain</dt><dd>active</dd>" in html
    assert "<dt>Forked from revision</dt><dd>13</dd>" in html  # in "More"
    assert "Restored head turn" not in html
    recorded = row()
    assert recorded.outcome == "ok"
    assert recorded.story_id == NEW  # the new story ...
    assert (recorded.entity_type, recorded.entity_id) == ("story", STORY)  # ... the source's row
    assert recorded.engine_operation_id == result["operation_id"]

    # The new story's first view shows #53's banner (set by the check page, no auto-redirect).
    answer_page(engine, story=story_json(id=NEW, name="Harbour (fork at turn 2)"))
    new_page = page(operator_client, f"/stories/{NEW}/")
    assert "Forked from <em>" in new_page


def test_the_poll_partial_reads_only_the_check_and_renders_the_ending(
    operator_client: Client, engine: FakeEngine
) -> None:
    hold(operator_client, engine, "swap")
    engine.reset()
    engine.respond("GET", CHECK_PATH, 200, json=check_json("reactivated", outcome=outcomes.SWAPPED))

    html = page(operator_client, f"{CHECK_PAGE}?op=swap", **HTMX)

    assert "Swap applied after re-check" in html
    assert "hx-trigger" not in html
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/content-rating/checks/{JOB}"
    ]


def test_a_missing_op_falls_back_to_the_outcome(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_check(engine, check_json("reactivated", outcome=outcomes.SWAPPED))
    html = page(operator_client, CHECK_PAGE)
    assert "Swap applied after re-check" in html
    assert "Restored head turn 4" in html


def test_a_missing_op_while_checking_is_generic(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_check(engine, check_json())
    html = page(operator_client, CHECK_PAGE)
    assert "A reactivated edit is waiting for a guardrail re-check" in html


@pytest.mark.parametrize(
    ("op", "outcome", "shown"),
    [
        ("swap", outcomes.MISMATCHED, "Swap"),  # outcome op outside the vocabulary: ?op
        ("untruncate", outcomes.SWAPPED, "Swap"),  # disagrees with ?op: the outcome's op
        ("swap", outcomes.UNPARSEABLE, "Swap"),
    ],
)
def test_an_unreadable_outcome_goes_to_more(
    operator_client: Client, engine: FakeEngine, op: str, outcome: dict[str, Any], shown: str
) -> None:
    hold(operator_client, engine, op)
    answer_check(engine, check_json("reactivated", outcome=outcome))

    html = page(operator_client, f"{CHECK_PAGE}?op={op}")

    assert f"{shown} applied after re-check" in html
    assert "Couldn&#x27;t read the Engine&#x27;s answer" in html
    assert '<details class="raw-outcome"><summary>More</summary>' in html
    assert "Restored head turn" not in html
    assert "Open the new story" not in html
    recorded = row()  # still completed ok, with what the raw result names
    assert recorded.outcome == "ok"
    if outcome is not outcomes.UNPARSEABLE:
        assert recorded.engine_operation_id == outcome["result"]["operation_id"]


def test_outcome_parse_and_headings() -> None:
    assert parse_outcome(outcomes.UNTRUNCATED, "untruncate").readable
    assert parse_outcome(outcomes.UNTRUNCATED, None).readable
    assert not parse_outcome(outcomes.UNTRUNCATED, "swap").readable
    assert not parse_outcome(None, "swap").readable
    assert not parse_outcome(outcomes.MISMATCHED, None).readable
    assert heading("checking", None) == "A reactivated edit is waiting for a guardrail re-check"
    assert heading("refused", "fork") == "Fork refused by re-check"
    assert heading("failed", "swap") == "Swap could not be completed"
    assert heading("expired", "swap") == "Swap: re-check ended (expired)"
    assert shown_op(None, "bogus") is None


# --- refused, failed, other endings ---------------------------------------------------------------


VIOLATION = {
    "position": {"turn": 4, "variant": 2, "beat": 0},
    "policy_id": "01M5R0P0L1CY00000000000001",
    "policy_name": "No graphic violence",
}


@pytest.mark.parametrize(
    ("op", "unchanged"), [("swap", "The story is unchanged"), ("fork", "No fork was created")]
)
def test_refused_shows_the_violations_and_claims_no_change(
    operator_client: Client, engine: FakeEngine, op: str, unchanged: str
) -> None:
    hold(operator_client, engine, op)
    answer_check(engine, check_json("refused", violations=[VIOLATION]))

    html = page(operator_client, f"{CHECK_PAGE}?op={op}")

    assert f"{OPS[op]} refused by re-check" in html
    assert unchanged in html
    assert "Violations (1)" in html
    assert f'href="/stories/{STORY}/history/?as_of=4&amp;variant=4%3A2#turn-4"' in html
    assert "No graphic violence" in html
    assert "The rating was not changed" not in html  # #80's line is not shown
    # complement: no transcript change is claimed
    assert "Restored head turn" not in html
    assert "applied after re-check" not in html
    assert "Open the new story" not in html
    assert "Back to the story" in html
    assert f'href="/stories/{STORY}/history/#variants"' in html
    assert "Retry" not in html
    recorded = row()
    assert (recorded.outcome, recorded.error_code) == ("error", "rating_refused")


def test_failed_shows_the_error_class_and_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    hold(operator_client, engine, "untruncate")
    error = {"class": "NothingToUndo", "message": "the reactivation stopped (NothingToUndo)"}
    answer_check(engine, check_json("failed", error=error))

    html = page(operator_client, f"{CHECK_PAGE}?op=untruncate")

    assert "Undo rewind could not be completed" in html
    assert "<code>NothingToUndo</code>" in html
    assert "the reactivation stopped (NothingToUndo)" in html
    assert FAILED_LINE in html
    assert "The rating was not changed" not in html
    recorded = row()
    assert (recorded.outcome, recorded.error_code) == ("error", "NothingToUndo")


def test_any_other_ending_stops_polling_with_the_raw_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    hold(operator_client, engine, "swap")
    answer_check(engine, check_json("expired"))

    html = page(operator_client, f"{CHECK_PAGE}?op=swap")

    assert "Swap: re-check ended (expired)" in html
    assert "hx-trigger" not in html  # only `checking` keeps polling
    assert '<details class="raw-check"><summary>More</summary>' in html
    recorded = row()
    assert (recorded.outcome, recorded.error_code) == ("error", "unexpected_status")


def test_a_second_terminal_read_changes_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    hold(operator_client, engine, "swap")
    answer_check(engine, check_json("reactivated", outcome=outcomes.SWAPPED))
    page(operator_client, f"{CHECK_PAGE}?op=swap")
    first = row().response
    answer_check(engine, check_json("failed", error={"class": "X", "message": "x"}))
    page(operator_client, f"{CHECK_PAGE}?op=swap")
    assert row().outcome == "ok"
    assert row().response == first


def test_a_check_of_another_kind_renders_the_rating_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    tightening = check_json(
        "checking", kind="tightening", target={"rating_id": EVERYONE, "version": "v"}
    )
    answer_check(engine, tightening)
    html = page(operator_client, f"{CHECK_PAGE}?op=swap")
    assert "waiting for a guardrail re-check" not in html
    assert 'class="check-from"' in html
    assert "<dt>Kind</dt><dd>tightening</dd>" in html


def test_a_check_404_says_it_no_longer_exists(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}", 200, json=story_json())
    engine.respond(
        "GET",
        CHECK_PATH,
        404,
        json={"error": {"code": "not_found", "message": "no such check", "details": {}}},
    )

    response = operator_client.get(f"{CHECK_PAGE}?op=fork")

    assert response.status_code == 404
    html = response.content.decode()
    assert (
        "This check no longer exists (checks are kept 30 days) or isn&#x27;t one of this "
        "story&#x27;s" in html
    )
    assert f'href="/stories/{STORY}/">Back to the story</a>' in html
    assert "hx-trigger" not in html


# --- the pause banner -----------------------------------------------------------------------------


def paused(**pause: Any) -> dict[str, Any]:
    found = {"job_id": JOB, "cause": "reactivation_check", "progress": {"done": 1, "total": 4}}
    found.update(pause)
    return story_json(status="paused", pause=found)


def test_the_pause_banner_names_the_held_edit(operator_client: Client, engine: FakeEngine) -> None:
    hold(operator_client, engine, "untruncate")
    answer_page(engine, story=paused())

    html = page(operator_client, f"/stories/{STORY}/")

    assert "Paused: guardrail re-check before undo rewind (1/4)" in html
    assert f'href="{CHECK_PAGE}?op=untruncate"' in html
    assert "op=untruncate" in html.split('hx-get="', 1)[1].split('"', 1)[0]


def test_the_pause_banner_without_a_row_says_an_edit() -> None:
    story = Story.from_json(paused())
    assert story is not None
    banner = pause_banner(story)
    assert banner is not None
    assert banner.text == "Paused: guardrail re-check before an edit (1/4)"
    assert banner.check_url == CHECK_PAGE  # no op
    assert "op=" not in banner.poll_url


def test_the_rating_check_banner_keeps_its_wording() -> None:
    story = Story.from_json(paused(cause="rating_check"))
    assert story is not None
    banner = pause_banner(story)
    assert banner is not None
    assert banner.text == "Paused: rating check in progress (1/4)"


def test_the_banner_poll_carries_the_op(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CHECK_PATH, 200, json=check_json())
    html = page(operator_client, f"{CHECK_PAGE}?banner=1&cause=reactivation_check&op=fork", **HTMX)
    assert "Paused: guardrail re-check before fork (2/4)" in html
    assert 'hx-trigger="every 2s"' in html


def test_the_banner_poll_refreshes_at_any_reactivation_ending(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", CHECK_PATH, 200, json=check_json("expired"))
    response = operator_client.get(f"{CHECK_PAGE}?banner=1&cause=reactivation_check", headers=HTMX)
    assert response["HX-Refresh"] == "true"


def test_an_archived_fork_source_shows_no_pause_banner(
    operator_client: Client, engine: FakeEngine
) -> None:
    hold(operator_client, engine, "fork")
    archived = story_json(status="archived", archived_at="2026-10-25T00:00:00Z")
    answer_page(engine, story=archived)
    html = page(operator_client, f"/stories/{STORY}/")
    assert "Paused:" not in html
    assert 'id="pause-banner"' not in html


# --- the operations log ---------------------------------------------------------------------------


def test_the_operation_row_links_its_check(operator_client: Client, engine: FakeEngine) -> None:
    hold(operator_client, engine, "fork")
    html = page(operator_client, f"/operations/{row().pk}/")
    assert f'<a class="check-link" href="{CHECK_PAGE}?op=fork">Check</a>' in html


def test_a_row_without_a_job_has_no_check_link(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", path("swap"), 200, json={**outcomes.SWAPPED["result"], "idempotency_key": KEY}
    )
    operator_client.post(url("swap", chain="4:2"), posted(chain="4:2"))
    recorded = EngineOperation.objects.get(kind="swap")
    assert recorded.outcome == "ok"
    assert "check-link" not in page(operator_client, f"/operations/{recorded.pk}/")
