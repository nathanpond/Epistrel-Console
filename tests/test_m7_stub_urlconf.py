"""The one shared M7 stub urlconf (#294, E6): every E6/E11 name reverses under it, none in
``NOT_YET_REGISTERED`` reverses under the real urlconf, and the real routes still work."""

import pytest
from django.test import override_settings
from django.urls import NoReverseMatch, URLPattern, URLResolver, resolve, reverse

from epistrel_console import urls as real
from tests import urls_with_m7_pages as m7

STORY = "01JM7ST0000000000000000001"
ULID = "01JM7RW0000000000000000001"

# E6's helpers and E11's tables, with the arguments each path takes.
ARGS: dict[str, list[str]] = {
    "stories:rewrites": [STORY],
    "stories:rewrite-answers": [STORY, ULID],
    "stories:rewrite-apply": [STORY, ULID],
    "stories:rewrite-cancel": [STORY, ULID],
    "stories:line-edit": [STORY],
    "stories:direct": [STORY],
    "stories:backfill": [STORY],
    "stories:backfill-start": [STORY],
    "stories:backfill-cancel": [STORY],
    "stories:backfill-conflict": [STORY, "c1"],
    "stories:certify": [STORY],
    "stories:prune": [STORY],
    "stories:prune-confirm": [STORY],
    "stories:export": [STORY],
    "stories:export-job": [STORY, ULID],
    "stories:export-download": [STORY, ULID],
    "stories:operations-list": [STORY],
    "inspect:entity-merge": [STORY],
    "inspect:outbox": [STORY],
    "inspect:resolve-listing": [STORY, "listing-1"],
    "inspect:resolved": [STORY],
    "engine:import": [],
    "engine:import-job": [ULID],
    "engine:certifications": [],
    "engine:certification-invalidate": ["cert-1"],
    "engine:certification-import": [],
    "engine:eval-runs": [],
    "engine:eval-run": ["run-1"],
    "profiles:certify": ["00000000000000000000PRF002"],
}
PATHS = {
    "stories:line-edit": f"/stories/{STORY}/history/line/",
    "stories:operations-list": f"/stories/{STORY}/operations/",
    "inspect:resolve-listing": (
        f"/stories/{STORY}/inspect/effects/reconciliation/listing-1/resolve/"
    ),
    "engine:certification-import": "/engine/certification-import/",
    "profiles:certify": "/profiles/00000000000000000000PRF002/certify/",
}


def test_the_table_is_every_stub() -> None:
    assert set(ARGS) == m7.STUB_NAMES
    assert m7.NOT_YET_REGISTERED <= m7.STUB_NAMES


@override_settings(ROOT_URLCONF="tests.urls_with_m7_pages")
@pytest.mark.parametrize("name", sorted(ARGS))
def test_every_m7_name_reverses_under_the_stub_urlconf(name: str) -> None:
    url = reverse(name, args=ARGS[name])
    assert resolve(url).view_name == name
    if name in PATHS:
        assert url == PATHS[name]


@pytest.mark.parametrize("name", sorted(m7.NOT_YET_REGISTERED))
def test_no_unregistered_name_reverses_under_the_real_urlconf(name: str) -> None:
    with pytest.raises(NoReverseMatch):
        reverse(name, args=ARGS[name])


@override_settings(ROOT_URLCONF="tests.urls_with_m7_pages")
def test_the_real_patterns_still_resolve_under_the_stub_urlconf() -> None:
    assert reverse("stories:history", args=[STORY]) == f"/stories/{STORY}/history/"
    assert (
        reverse("inspect:reconciliation", args=[STORY])
        == f"/stories/{STORY}/inspect/effects/reconciliation/"
    )
    assert resolve(f"/stories/{STORY}/history/").view_name == "stories:history"
    assert reverse("engine:home") == "/engine/"
    assert reverse("profiles:detail", args=["p1"]) == "/profiles/p1/"


def test_each_mount_is_a_real_mount() -> None:
    mounts = {str(p.pattern) for p in real.urlpatterns if isinstance(p, URLPattern | URLResolver)}
    assert set(m7.MOUNTS) <= mounts
