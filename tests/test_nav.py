"""E11's page registries and nav rows (#296, ``console/nav.py``)."""

from django.test import override_settings

from console.nav import (
    ENGINE_PAGES,
    PAGE_PARENT,
    STORY_PAGES,
    NavLink,
    engine_links,
    story_links,
)

STORY = "01JM7ST0000000000000000001"
M7_PAGES = "tests.urls_with_m7_pages"
S = f"/stories/{STORY}/"

# E11's tables, frozen: every URL name in them (nav rows and the pages without one).
E11_NAMES = frozenset(
    {
        "stories:rewrites",
        "stories:rewrite",
        "stories:rewrite-answers",
        "stories:rewrite-apply",
        "stories:rewrite-cancel",
        "stories:line-edit",
        "stories:direct",
        "stories:amendments",
        "stories:amendment",
        "stories:amendment-undo",
        "stories:amendment-redo",
        "stories:backfill",
        "stories:backfill-start",
        "stories:backfill-cancel",
        "stories:backfill-conflict",
        "stories:certify",
        "stories:prune",
        "stories:prune-confirm",
        "stories:export",
        "stories:export-job",
        "stories:export-download",
        "stories:operations-list",
        "engine:import",
        "engine:import-job",
        "engine:certifications",
        "engine:certification",
        "engine:certification-invalidate",
        "engine:certification-import",
        "engine:certification-run",
        "engine:eval-runs",
        "engine:eval-run",
    }
)


def test_the_registries_are_e11s_rows() -> None:
    assert STORY_PAGES == (
        ("stories:rewrites", "Rewrite"),
        ("stories:amendments", "Amendments"),
        ("stories:backfill", "Backfill"),
        ("stories:export", "Export"),
        ("stories:operations-list", "Operations"),
    )
    assert ENGINE_PAGES == (
        ("engine:import", "Import a bundle"),
        ("engine:certifications", "Certifications"),
        ("engine:eval-runs", "Eval runs"),
    )


def test_every_page_parent_is_in_e11s_tables() -> None:
    """A typo in a key or a parent fails here; every parent is a nav row."""
    assert set(PAGE_PARENT) <= E11_NAMES
    rows = {name for name, _ in (*STORY_PAGES, *ENGINE_PAGES)}
    assert set(PAGE_PARENT.values()) <= rows
    assert not set(PAGE_PARENT) & rows
    # The pages that mark no row.
    assert E11_NAMES - set(PAGE_PARENT) - rows == {
        "stories:direct",
        "stories:certify",
        "stories:prune",
        "stories:prune-confirm",
    }


def test_rewrite_link_once_registered() -> None:
    """#297 registered ``stories:rewrites`` (E24: this replaces #296's "empty under the real
    urlconf" baseline): the real urlconf's nav holds Rewrite alone, marked current from the
    session page through ``PAGE_PARENT``."""
    assert story_links(STORY, None) == [
        NavLink("stories:rewrites", "Rewrite", f"{S}rewrites/", False)
    ]
    assert story_links(STORY, "stories:rewrite") == [
        NavLink("stories:rewrites", "Rewrite", f"{S}rewrites/", True)
    ]


@override_settings(ROOT_URLCONF=M7_PAGES)
def test_story_links_are_the_five_rows_once_registered() -> None:
    """**Complement:** under the stub urlconf, the five rows in order, reversed from the id."""
    assert story_links(STORY, None) == [
        NavLink("stories:rewrites", "Rewrite", f"{S}rewrites/", False),
        NavLink("stories:amendments", "Amendments", f"{S}amendments/", False),
        NavLink("stories:backfill", "Backfill", f"{S}backfill/", False),
        NavLink("stories:export", "Export", f"{S}export/", False),
        NavLink("stories:operations-list", "Operations", f"{S}operations/", False),
    ]


@override_settings(ROOT_URLCONF=M7_PAGES)
def test_a_sub_page_marks_its_row_current() -> None:
    current = [link.name for link in story_links(STORY, "stories:rewrite") if link.current]
    assert current == ["stories:rewrites"]
    assert [link.name for link in story_links(STORY, "stories:export") if link.current] == [
        "stories:export"
    ]
    # Complement: a page that isn't an M7 page marks none.
    assert not any(link.current for link in story_links(STORY, "stories:history"))


def test_engine_links_are_empty_under_the_real_urlconf() -> None:
    assert engine_links(None) == []


@override_settings(ROOT_URLCONF=M7_PAGES)
def test_engine_links_are_the_three_rows_once_registered() -> None:
    assert engine_links(None) == [
        NavLink("engine:import", "Import a bundle", "/engine/import/", False),
        NavLink("engine:certifications", "Certifications", "/engine/certifications/", False),
        NavLink("engine:eval-runs", "Eval runs", "/engine/evals/", False),
    ]
    marked = [link.label for link in engine_links("engine:certification-run") if link.current]
    assert marked == ["Certifications"]
    assert not any(link.current for link in engine_links("engine:home"))
