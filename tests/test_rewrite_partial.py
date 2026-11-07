"""E3's rewrite-session partial (#294), rendered with ``render_to_string`` over #292's fixtures:
``templates/console/rewrites/_session.html`` and its parts, ``_modes.html`` and
``_omniscient.html``."""

import re
from typing import Any

from django.template.loader import render_to_string
from django.test import override_settings

from console.engine.models import RewriteSession
from console.forms.rewrites import AnswersForm, RewriteModesForm
from console.rewrites import (
    OMNISCIENT_LINE,
    PREVIEW_PENDING,
    REACH_TRUNCATED_TEXT,
    TRUNCATED_TEXT,
    session_view,
)
from tests.fixtures import engine_fixture

STORY = "01JM7ST0000000000000000001"
SESSION = "01JM7RW0000000000000000001"
Q1 = "01JM7QN0000000000000000001"
KEY = "01JM7KY0000000000000000009"
M7_PAGES = "tests.urls_with_m7_pages"


def view(name: str, **changes: Any) -> Any:
    body: dict[str, Any] = engine_fixture(f"m7/{name}")
    body.update(changes)
    parsed = RewriteSession.from_json(body)
    assert parsed is not None
    return session_view(parsed, STORY, {})


def render(shown: Any, **context: Any) -> str:
    return render_to_string(
        "console/rewrites/_session.html", {"view": shown, "idempotency_key": KEY, **context}
    )


def squeezed(html: str) -> str:
    return re.sub(r">\s+<", "><", html)


def test_the_recommended_option_is_checked_and_explained() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        shown = view("rewrite_open")
        html = render(shown, answers_form=AnswersForm(shown.questions))
    recommended = re.search(r'<input type="radio" name="choice_' + Q1 + r'" value="o1"[^>]*>', html)
    assert recommended is not None
    assert " checked" in recommended.group(0)
    other = re.search(r'<input type="radio" name="choice_' + Q1 + r'" value="o2"[^>]*>', html)
    assert other is not None
    assert " checked" not in other.group(0)
    assert "Rewrite Bob&#x27;s line: he only copied the ledger (recommended)</label>" in html
    label_end = html.index("(recommended)</label>")
    why = html.index("Why: Bob&#x27;s line is his own sincere claim")
    assert why > label_end
    assert html.index("Keep the line") > why  # "Why:" follows the recommended option
    assert f'name="text_{Q1}"' in html
    assert f'action="/stories/{STORY}/rewrites/{SESSION}/answers/"' in html
    assert ">Submit answers</button>" in html
    assert 'name="accept_all" value="1">Accept every recommendation</button>' in html
    assert f'value="{KEY}"' in html
    assert PREVIEW_PENDING in html
    # The footer has Cancel as a plain link, and no per-question accept button.
    assert f'<a href="/stories/{STORY}/rewrites/{SESSION}/cancel/" class="session-action">' in html
    assert html.count("<form") == 1


def test_severity_badges_carry_their_text() -> None:
    html = render(view("rewrite_open"))
    assert re.search(r'<span class="badge severity-high" title="high">High</span>', html)
    assert '<span class="badge">Required</span>' in html
    planned = render(view("rewrite_session"))
    assert re.search(r'class="badge severity-low" title="low">Low</span>', planned)


def test_the_radius_tables_in_order_with_counts() -> None:
    html = render(view("rewrite_session"))
    headings = [
        html.index("<h4>Conflicts (1)</h4>"),
        html.index("<h4>Cascaded repairs (1)</h4>"),
        html.index("<h4>Incidental mentions (1)</h4>"),
    ]
    assert headings == sorted(headings)
    assert "A later amendment relies on this" in html
    assert "Rewrite, Drop, Keep, Cascade undo" in html
    assert "<q>Bob confesses the forgery</q>" in html
    assert f'<p class="warning">{TRUNCATED_TEXT}</p>' in html
    assert "The Engine won&#x27;t make these changes (1)" in html
    assert "External owned" in html
    assert "Who is told (1)" in html
    assert "The bank was already told about the forged ledger" in html
    assert "<h4>Relabels (1)</h4>" in html
    assert "New entities" not in html  # an empty list leaves its heading out
    assert "Planned at story revision 20" in html


def test_an_empty_bucket_reads_none() -> None:
    body = engine_fixture("m7/rewrite_session")
    body["preview"]["blast_radius"]["cascaded"] = []
    parsed = RewriteSession.from_json(body)
    assert parsed is not None
    html = squeezed(render(session_view(parsed, STORY, {})))
    assert "<h4>Cascaded repairs (0)</h4><p>None</p>" in html


def test_a_lite_session_renders_its_reach() -> None:
    html = render(view("rewrite_session_lite"))
    assert '<p class="reach">Searched turns 1–4 (Model searched)</p>' in html  # noqa: RUF001
    assert f'<p class="warning">{REACH_TRUNCATED_TEXT}</p>' in html


def test_apply_is_a_post_form_with_the_key_and_base_revision() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        html = render(view("rewrite_session"), base_revision=20)
    apply = re.search(
        rf'<form method="post" action="/stories/{STORY}/rewrites/{SESSION}/apply/".*?</form>',
        html,
        re.DOTALL,
    )
    assert apply is not None
    assert f'name="idempotency_key" value="{KEY}"' in apply.group(0)
    assert 'name="base_revision" value="20"' in apply.group(0)
    assert ">Apply</button>" in apply.group(0)
    assert "Submit answers" not in html
    with override_settings(ROOT_URLCONF=M7_PAGES):
        bare = render(view("rewrite_session"))
    assert "base_revision" not in bare


def test_without_an_answers_form_there_is_no_answers_form() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        html = render(view("rewrite_open"))
    assert "Submit answers" not in html
    assert f'action="/stories/{STORY}/rewrites/{SESSION}/answers/"' not in html
    assert "<form" not in html  # Cancel is a link
    assert " disabled" in html  # the options still show, read-only


def test_a_terminal_session_renders_no_form() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        shown = view("rewrite_session_applied")
        html = render(shown, answers_form=AnswersForm(shown.questions), base_revision=20)
    assert "<form" not in html
    assert "<h3>Report</h3>" in html
    assert "1 resolution" in html
    assert f'href="/stories/{STORY}/amendments/01JM7AM0000000000000000003/"' in html


def test_the_held_reply_and_the_answered_question() -> None:
    html = render(view("rewrite_session_directed"))
    assert "Held reply (shown once applied)" in html
    assert "<blockquote>Bob lowers his eyes. &quot;I forged it, Alice.&quot;</blockquote>" in html
    assert "3 model calls to replay" in html
    planned = render(view("rewrite_session"))
    assert "Answer: Rewrite Bob&#x27;s line: he only copied the ledger (recommended)" in planned
    assert "console:nate" in planned


def test_a_rejected_question_is_one_row() -> None:
    body = engine_fixture("m7/rewrite_open")
    body["questions"].append({"id": "01JM7QN0000000000000000009"})
    parsed = RewriteSession.from_json(body)
    assert parsed is not None
    html = render(session_view(parsed, STORY, {}))
    assert "This question couldn&#x27;t be read" in html
    assert "{&quot;id&quot;:&quot;01JM7QN0000000000000000009&quot;}" in html


def test_the_modes_snippet_and_the_omniscient_line() -> None:
    html = render_to_string("console/rewrites/_modes.html", {"form": RewriteModesForm()})
    assert re.search(
        r"<details><summary>Advanced</summary>.*apply_directly.*</details>", html, re.S
    )
    assert 'name="resolution"' in html
    assert "Apply directly (skip the preview)" in html
    line = render_to_string("console/rewrites/_omniscient.html")
    assert line.strip() == f'<p class="viewpoint">{OMNISCIENT_LINE}</p>'
