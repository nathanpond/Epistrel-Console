"""E13's quality wording (#295): ``console.certification.quality_line`` over a ``QualityStatus``
and a ``FloorsMet``, ``_quality.html``, ``floors_text`` and the E6 certification links."""

from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import NoReverseMatch
from django.urls import reverse as real_reverse

from console import certification
from console.certification import (
    CANARY_LIST_REQUEST,
    INVALIDATED_REASON_TEXT,
    QUALITY_TEXT,
    QualityLine,
    certification_run_url,
    certification_url,
    certifications_url,
    eval_run_url,
    eval_runs_url,
    floors_text,
    quality_line,
)
from console.engine.models import FloorsMet, QualityStatus
from tests.certification_helpers import unregistered_certification_pages

CERT = "01JM7CT0000000000000000001"
STUBS = "tests.urls_with_m7_pages"


def badge(line: QualityLine | None) -> str:
    return render_to_string("console/_quality.html", {"quality": line})


def test_the_wording_tables() -> None:
    assert QUALITY_TEXT == {
        "certified": "Certified",
        "uncertified": "Uncertified",
        "stale": "Stale",
    }
    assert INVALIDATED_REASON_TEXT == {
        "canary": "a canary eval fell below a floor",
        "drift": "the model changed under the same version",
        "manual": "withdrawn by an operator",
    }
    assert CANARY_LIST_REQUEST == "https://github.com/nathanpond/Epistrel/issues/776"


def test_certified_with_its_certification_and_floors(monkeypatch: pytest.MonkeyPatch) -> None:
    status = QualityStatus(
        status="certified",
        certification_id=CERT,
        floors_passed=["NFR-QUAL-2.false_support", "smoke.turn_completed"],
        failed_floors=["NFR-QUAL-4.recall"],
    )
    with monkeypatch.context() as patched:  # D19: before #313 registered its page
        unregistered_certification_pages(patched)
        line = quality_line(status)
    assert line.text == "Certified"
    assert line.css_class == "quality-certified"
    assert line.title == "certified"
    assert line.certification_url is None  # engine:certification unregistered (D19)
    assert line.floors == (
        "Floors passed: NFR-QUAL-2.false_support, smoke.turn_completed; failed: NFR-QUAL-4.recall"
    )
    html = badge(line)
    assert '<span class="quality quality-certified" title="certified">Certified</span>' in html
    assert f'<code title="{CERT}">…00000001</code>' in html  # unlinked, short id
    assert "<a " not in html
    linked = quality_line(status)  # #313 registered engine:certification
    assert linked.certification_url == f"/engine/certifications/{CERT}/"
    assert f'<a class="certification-link" href="/engine/certifications/{CERT}/">' in badge(linked)


def test_uncertified() -> None:
    line = quality_line(QualityStatus(status="uncertified"))
    assert (line.text, line.css_class, line.title, line.floors) == (
        "Uncertified",
        "",
        "uncertified",
        "",
    )


def test_uncertified_renders_exactly_uncertified() -> None:
    """**Complement:** the visible text the existing pages showed."""
    assert badge(quality_line(QualityStatus(status="uncertified"))).strip() == (
        '<span class="quality" title="uncertified">Uncertified</span>'
    )


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("canary", "Stale — a canary eval fell below a floor"),
        ("drift", "Stale — the model changed under the same version"),
        ("manual", "Stale — withdrawn by an operator"),
        ("later", "Stale — Later"),
        (None, "Stale"),
    ],
)
def test_stale_says_why(reason: str | None, text: str) -> None:
    line = quality_line(QualityStatus(status="stale", invalidated_reason=reason))
    assert line.text == text
    assert line.css_class == "quality-stale"
    assert line.title == (f"stale ({reason})" if reason else "stale")  # #312: raw status and reason


def test_an_unknown_status_is_humanised_without_a_class() -> None:
    line = quality_line("pending_review")
    assert (line.text, line.css_class, line.title) == ("Pending review", "", "pending_review")


def test_a_floors_met_reads_through_the_same_function() -> None:
    line = quality_line(
        FloorsMet(fingerprint="sha256:" + "a" * 64, floors_met=["NFR-QUAL-2"], status="certified")
    )
    assert line.text == "Certified"
    assert line.floors == "Floors passed: NFR-QUAL-2"
    assert line.certification_url is None


def test_floors_text() -> None:
    assert floors_text(["a", "b"], ["c"]) == "Floors passed: a, b; failed: c"
    assert floors_text(None, ["c"]) == "Floors failed: c"
    assert floors_text([], ()) == ""


def test_the_url_helpers_are_none_while_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    assert eval_run_url("2026-11-01-smoke") == "/engine/evals/2026-11-01-smoke/"  # #316
    assert eval_runs_url() == "/engine/evals/"
    assert eval_run_url("a/b") is None  # a run id with "/" can't be reversed
    unregistered_certification_pages(monkeypatch)  # D19: before #313 registered its pages
    assert certifications_url() is None
    assert certification_url(CERT) is None
    assert certification_run_url("01JM7CR0000000000000000001") is None

    def without_eval_runs(name: str, *args: Any, **kwargs: Any) -> str:
        if name in {"engine:eval-runs", "engine:eval-run"}:
            raise NoReverseMatch(name)
        return real_reverse(name, *args, **kwargs)

    monkeypatch.setattr(certification, "reverse", without_eval_runs)  # before #316
    assert eval_run_url("2026-11-01-smoke") is None
    assert eval_runs_url() is None


@override_settings(ROOT_URLCONF=STUBS)
def test_the_url_helpers_with_the_stub_urlconf() -> None:
    assert certifications_url() == "/engine/certifications/"
    assert (
        certifications_url(fingerprint="sha256:ab", profile_id="P")
        == "/engine/certifications/?fingerprint=sha256%3Aab&profile_id=P"
    )
    assert certification_url(CERT) == f"/engine/certifications/{CERT}/"
    assert certification_url("") is None
    assert certification_run_url("R1") == "/engine/certification-runs/R1/"
    assert eval_run_url("2026-11-01-smoke") == "/engine/evals/2026-11-01-smoke/"
