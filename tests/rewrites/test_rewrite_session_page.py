"""#297's session page (``stories:rewrite``, ``console/views/rewrites.py``): its reads, E3's
partial over the awaiting-answers, awaiting-approval, Lite and applied fixtures, the amendment
link and the footer actions (D19: real vs the stub urlconf), the 404, and E2's omniscient-only
rule across the list, the open and the session read."""

import re
from html import unescape
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client, override_settings
from django.utils.html import escape

from console.rewrites import (
    NOT_FOUND_TEXT,
    OMNISCIENT_LINE,
    PREVIEW_PENDING,
    REACH_TRUNCATED_TEXT,
    TRUNCATED_TEXT,
)
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    GET_ONE,
    LIST_ROUTE,
    M7_PAGES,
    ONE_ROUTE,
    SESSION,
    SESSION_URL,
    STORY_READ,
    URL,
    S,
    calls,
    engine,
    m7,
)

pytestmark = pytest.mark.django_db
__all__ = ["engine"]  # the fixture, re-exported for pytest

WRITTEN_ONLY = ("timeline_revision", "apply", "idempotency_key")


def session_body(name: str, **changes: Any) -> dict[str, Any]:
    """A session fixture as ``GET …/rewrites/{sid}`` answers it (a write's extras dropped)."""
    body: dict[str, Any] = m7(name)
    for key in WRITTEN_ONLY:
        body.pop(key, None)
    body.update(changes)
    return body


def served(engine: FakeEngine, name: str, **changes: Any) -> dict[str, Any]:
    body = session_body(name, **changes)
    engine.respond("GET", ONE_ROUTE, 200, json=body)
    return body


def page(client: Client, url: str = SESSION_URL, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return str(response.content.decode())


def test_session_page_reads_in_order(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, "rewrite_session")
    page(operator_client)
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("GET", GET_ONE)]
    (request,) = [r for r in engine.requests if r.url.path == GET_ONE]
    assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
    # **Complement:** nothing else, and no POST of any kind.
    assert not [m for m, _ in calls(engine) if m != "GET"]


def test_the_heading_links_title_and_current_nav(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, "rewrite_session")
    html = page(operator_client)
    assert "<title>Rewrite · Harbour Ledger</title>" in html
    assert f"<h3>Rewrite session <code>{SESSION}</code></h3>" in html
    assert f'<a href="{URL}">← All rewrites</a> · <a href="{SESSION_URL}">Reload</a>' in html
    assert f'<a href="{URL}" aria-current="page">Rewrite</a>' in html
    assert OMNISCIENT_LINE in html
    assert 'name="viewpoint"' not in html


def test_awaiting_answers_shows_the_question_and_pending_preview(
    operator_client: Client, engine: FakeEngine
) -> None:
    served(engine, "rewrite_open")
    html = unescape(page(operator_client))
    assert "Bob told Alice he forged the ledger in turn 2. Does that line change too?" in html
    assert '<span class="badge severity-high" title="high">High</span>' in html
    assert '<span class="badge">Required</span>' in html
    assert "Rewrite Bob's line: he only copied the ledger (recommended)" in html
    recommended = html.index("Rewrite Bob's line: he only copied the ledger (recommended)")
    why = html.index("Why: Bob's line is his own sincere claim")
    assert recommended < why
    assert PREVIEW_PENDING in html
    assert "Waiting for your answers" in html


def test_awaiting_approval_shows_the_preview(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, "rewrite_session")
    html = unescape(page(operator_client))
    assert "Ready to apply" in html
    assert "Planned at story revision 20" in html
    assert "Bob copied the ledger (was: forged it)" in html
    assert "Alice believes Bob copied the ledger" in html
    order = [
        html.index("Conflicts (1)"),
        html.index("Cascaded repairs (1)"),
        html.index("Incidental mentions (1)"),
    ]
    assert order == sorted(order)
    assert f'<p class="warning">{TRUNCATED_TEXT}</p>' in html
    assert "The Engine won't make these changes (1)" in html
    assert "External owned" in html
    assert "Who is told (1)" in html
    # #336: the fixtures' records are the Engine's ``record: {table, id}``, so they show.
    assert "Amendment parts …00000001" in html
    assert "State changes …00000001" in html
    assert "Events …00000003" in html
    assert "turn 3" in html  # the incidental mention's position


def test_lite_session_shows_reach(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, "rewrite_session_lite")
    html = unescape(page(operator_client))
    assert "Searched turns 1–4 (Model searched)" in html  # noqa: RUF001
    assert REACH_TRUNCATED_TEXT in html


def test_applied_session_links_its_amendment(operator_client: Client, engine: FakeEngine) -> None:
    served(engine, "rewrite_session_applied")
    amendment = "01JM7AM0000000000000000003"
    with override_settings(ROOT_URLCONF=M7_PAGES):
        html = page(operator_client)
    link = f'<a href="{S}amendments/{amendment}/"><code title="{amendment}">…00000003</code></a>'
    assert link in html
    # **Complement:** under the real urlconf (before #302) it is plain ``<code>``.
    html = page(operator_client)
    assert f'<dd><code title="{amendment}">…00000003</code></dd>' in html
    assert f"amendments/{amendment}/" not in html
    assert "<form" not in html.split('<section class="rewrite-session"')[1]


def _session_forms(html: str) -> list[str]:
    session = html.split('<section class="rewrite-session"', 1)[1]
    return re.findall(r"<form[^>]*>", session)


INSTRUCTION = "Bob never forged the ledger; he only copied it"


def cancel_link(state: str) -> str:
    """E3's Cancel link (#299): the confirm page, carrying the display facts."""
    query = urlencode({"kind": "instruction", "state": state, "instruction": INSTRUCTION})
    return f'<a href="{escape(f"{SESSION_URL}cancel/?{query}")}" class="session-action">Cancel</a>'


def test_apply_and_cancel_once_s7_registered(operator_client: Client, engine: FakeEngine) -> None:
    """#299 registered ``stories:rewrite-apply`` and ``stories:rewrite-cancel``: under the real
    urlconf an awaiting-approval session renders the Apply form and the Cancel link."""
    served(engine, "rewrite_session")
    html = page(operator_client)
    assert _session_forms(html) == [
        f'<form method="post" action="{SESSION_URL}apply/" class="session-action">'
    ]
    assert '<input type="hidden" name="base_revision" value="20">' in html
    assert cancel_link("awaiting_approval") in html


def test_awaiting_answers_has_the_answers_form_and_cancel(
    operator_client: Client, engine: FakeEngine
) -> None:
    """An awaiting-answers session renders the questions form (#298) and the Cancel link
    (#299), and no Apply form."""
    served(engine, "rewrite_open")
    html = page(operator_client)
    assert _session_forms(html) == [
        f'<form method="post" action="{SESSION_URL}answers/" class="answers">'
    ]
    assert cancel_link("awaiting_answers") in html
    assert "Apply this plan" not in html


def test_actions_render_under_the_stub_urlconf(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** the stub urlconf serves the real routes the same way."""
    served(engine, "rewrite_session")
    with override_settings(ROOT_URLCONF=M7_PAGES):
        html = page(operator_client)
    assert _session_forms(html) == [
        f'<form method="post" action="{SESSION_URL}apply/" class="session-action">'
    ]
    assert cancel_link("awaiting_approval") in html
    served(engine, "rewrite_open")
    with override_settings(ROOT_URLCONF=M7_PAGES):
        html = page(operator_client)
    assert "Submit answers" in html
    assert cancel_link("awaiting_answers") in html
    assert "Apply this plan" not in html


def test_unknown_session_is_404(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", ONE_ROUTE, 404, json=error_json("rewrite_session_not_found", "no such session")
    )
    html = unescape(page(operator_client, status=404))
    assert f'<p class="not-found">{NOT_FOUND_TEXT}</p>' in html
    assert "<title>Rewrite · Harbour Ledger</title>" in html
    assert f"<h3>Rewrite session <code>{SESSION}</code></h3>" in html


def test_a_non_ulid_session_never_reaches_the_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    operator_client.get(f"{URL}not-a-session/")
    assert calls(engine) == []


def test_another_read_failure_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert f"<h3>Rewrite session <code>{SESSION}</code></h3>" in html
    assert f'<a href="{URL}">← All rewrites</a>' in html
    assert "engine-error" in html


def test_names_fall_back_to_ids_without_the_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = served(engine, "rewrite_session")
    radius = body["preview"]["blast_radius"]
    radius["incidental"][0]["record"] = {"table": "characters", "id": "01JM7CH0000000000000000003"}
    engine.respond("GET", ONE_ROUTE, 200, json=body)
    assert "Characters Bob" in page(operator_client)
    engine.respond("GET", "/stories/{story_id}", 503, json=error_json("database_unavailable"))
    html = page(operator_client)
    assert "Characters …00000003" in html
    assert "Characters Bob" not in html


def test_character_viewpoint_never_sent(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST_ROUTE, 200, json=m7("rewrites"))
    engine.respond("POST", LIST_ROUTE, 201, json=m7("rewrite_open"))
    served(engine, "rewrite_open")
    viewpoint = "?viewpoint=character:01JM7CH0000000000000000002"
    operator_client.get(f"{URL}{viewpoint}")
    operator_client.post(
        f"{URL}{viewpoint}",
        {"instruction": "Bob copied it.", "resolution": "synchronous"},
    )
    operator_client.get(f"{SESSION_URL}{viewpoint}")
    rewrite_calls = [r for r in engine.requests if "/rewrites" in r.url.path]
    assert len(rewrite_calls) == 3
    for request in rewrite_calls:
        params = list(request.url.params.multi_items())
        assert ("viewpoint", "omniscient") in params
        assert "actor_character_id" not in dict(params)
        assert not [v for k, v in params if k == "viewpoint" and v.startswith("character:")]
