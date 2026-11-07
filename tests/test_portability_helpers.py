"""``console.portability``'s URL helpers and ``bundle_filename`` (#295, E6)."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from django.test import override_settings

from console.portability import (
    IMPORTING_ROW_NOTE,
    bundle_filename,
    export_job_url,
    export_url,
    import_job_url,
    prune_url,
)

STORY = "01JM7ST0000000000000000001"
JOB = "01JM7JB0000000000000000006"
NOW = datetime(2026, 11, 6, 23, 30, tzinfo=UTC)


@dataclass(frozen=True)
class Named:
    id: str
    name: str | None


@pytest.fixture(autouse=True)
def today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("console.portability.timezone.now", lambda: NOW)


@pytest.mark.parametrize(
    ("name", "filename"),
    [
        ("Harbour Nights é", "harbour-nights-e-20261106.epistrel.tgz"),
        (None, "01jm7st0000000000000000001-20261106.epistrel.tgz"),
        ("港の夜", "01jm7st0000000000000000001-20261106.epistrel.tgz"),
        ("!!!", "01jm7st0000000000000000001-20261106.epistrel.tgz"),
    ],
)
def test_bundle_filename(name: str | None, filename: str) -> None:
    assert bundle_filename(Named(STORY, name)) == filename
    assert bundle_filename(Named(STORY, name)).isascii()


def test_bundle_filename_falls_back_to_story() -> None:
    assert bundle_filename(Named("", None)) == "story-20261106.epistrel.tgz"


def test_the_url_helpers_are_none_while_unregistered() -> None:
    assert export_url(STORY) is None
    assert export_job_url(STORY, JOB) is None
    assert import_job_url(JOB) is None


def test_prune_url_once_registered() -> None:
    """#317 registered ``stories:prune``: the real urlconf reverses it."""
    assert prune_url(STORY) == f"/stories/{STORY}/prune/"
    assert prune_url("") is None


@override_settings(ROOT_URLCONF="tests.urls_with_m7_pages")
def test_the_url_helpers_with_the_stub_urlconf() -> None:
    assert export_url(STORY) == f"/stories/{STORY}/export/"
    assert export_job_url(STORY, JOB) == f"/stories/{STORY}/export/{JOB}/"
    assert import_job_url(JOB) == f"/engine/import/{JOB}/"
    assert prune_url(STORY) == f"/stories/{STORY}/prune/"
    assert import_job_url(None) is None
    assert export_url("") is None


def test_the_row_note_is_293s() -> None:
    from console.forms.stories import IMPORTING_ROW_NOTE as OWN

    assert IMPORTING_ROW_NOTE is OWN
