"""#317: ``console/pruning.py``'s views of the Engine's prune report and past prunings, over
inline dicts built from #292's ``m7/prune_*.json`` (E16: one-off variants are inline)."""

import copy
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import override_settings

from console import pruning, twostage
from console.engine.models import Pruned, PruneRuns
from tests.fixtures import engine_fixture

STORY = "01JM7ST0000000000000000001"
TAIL = "01JM7CN0000000000000000011"
DORMANT = "01JM7CN0000000000000000012"
AMENDMENT = "01JM7AM0000000000000000001"
NAMES = {"story_name": "Harbour Ledger"}


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def pruned(**impact: Any) -> Pruned:
    body = m7("prune_impact")
    body["impact"].update(impact)
    parsed = Pruned.from_json(body)
    assert parsed is not None
    return parsed


def test_impact_view_over_the_fixture() -> None:
    view = pruning.impact_view(pruned(), STORY, NAMES)
    assert view.revision_text == "Dry run at story revision 20 — nothing was changed"
    assert [b.title for b in view.buckets] == ["Discarded tails (1)", "Dormant continuations (1)"]
    [tail] = view.buckets[0].rows
    assert (tail.id, tail.range_text, tail.operations_text, tail.flipped_at) == (
        TAIL,
        "turn 3 · variant 1 → turn 4 · variant 1",
        "2 operations",
        "2026-11-04T16:00:00Z",
    )
    assert view.buckets[1].rows[0].operations_text == "1 operation"
    assert [line.text for line in view.lost] == [
        "Undo the rewind that discarded turn 3 · variant 1 → turn 4 · variant 1",
        "Swap back to turn 4 · variant 2 → turn 4 · variant 2",
        "Fork turn 4 · variant 2 → turn 4 · variant 2 into its own chain",
    ]
    assert view.kept_text == "4 audit events, 1 outbox row, 2 ratings (marked pruned)"
    [excluded] = view.excluded
    assert (excluded.target, excluded.text) == ("01JM7CN0000000000000000013", EXCLUDED)
    assert [ref.id for ref in excluded.amendments] == [AMENDMENT]
    [keep] = view.keep_first
    assert keep.text == "To keep turn 4 · variant 2 → turn 4 · variant 2, fork it before pruning"
    assert keep.url == f"/stories/{STORY}/history/#variants"
    assert view.target_ids == (TAIL, DORMANT)
    assert view.counts == {"discarded_tails": 1, "dormant_continuations": 1, "operations": 3}
    assert not view.nothing_to_prune
    payload = twostage.unsign("prune", view.token)
    assert payload.body == {"targets": [TAIL, DORMANT], "ignore_warnings": True}
    assert payload.base["story_name"] == "Harbour Ledger"
    assert pruning.unsign_report(view.report_token, STORY).timeline_revision == 20


EXCLUDED = "An amendment names its records"


def test_a_malformed_position_reads_a_dash() -> None:
    to_delete = m7("prune_impact")["impact"]["to_delete"]
    to_delete["discarded_tails"][0]["from"] = {"turn": "3"}
    to_delete["discarded_tails"].append("junk")
    [bucket, _] = pruning.buckets(to_delete)
    assert [row.range_text for row in bucket.rows] == ["—", "—"]
    assert bucket.rows[1].id == ""
    assert bucket.rows[1].operations_text == "—"
    assert pruning.range_text({"from": {"turn": 1, "variant": 0}}) == "—"


def test_unknown_operation_reason_and_group_are_humanised() -> None:
    impact = m7("prune_impact")["impact"]
    impact["lost_operations"].append({"operation": "re_derive", "target": "nope"})
    impact["excluded"][0]["reason"] = "policy_hold"
    impact["to_delete"]["orphan_runs"] = [impact["to_delete"]["discarded_tails"][0]]
    impact["retained"]["outcome_rows"] = 3
    body = m7("prune_impact")
    body["impact"] = impact
    parsed = Pruned.from_json(body)
    assert parsed is not None
    view = pruning.impact_view(parsed, STORY, NAMES)
    assert view.lost[-1].text == "Re derive: nope"  # an unknown target reads its id
    assert view.excluded[0].text == "Policy hold"
    assert not view.excluded[0].known
    assert view.buckets[2].heading == "Orphan runs"
    assert view.kept_text.endswith(", Outcome rows 3")
    settled = pruning.impact_view(
        pruned(
            excluded=[
                {
                    "target": "t",
                    "reason": "settlement_dependency",
                    "amendment_ids": [],
                    "settling_change_ids": ["01JM7SM0000000000000000001"],
                },
                {"target": "u", "reason": "in_flight"},
            ]
        ),
        STORY,
        NAMES,
    )
    assert [row.text for row in settled.excluded] == [
        "An active change settles one of its State changes",
        "A turn is writing to it",
    ]
    assert settled.excluded[0].change_ids == ("01JM7SM0000000000000000001",)


def _render(view: pruning.PruneImpactView) -> str:
    return render_to_string(
        "console/stories/_prune_impact.html",
        {"report": view, "prune_url": "/p/", "confirm_url": "/c/", "nothing_to_prune": "x"},
    )


def test_amendment_ids_plain_while_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    """D19: ``amendment_url`` is ``None`` → plain ``<code>``. #302 registered
    ``stories:amendment``, so the unregistered case stands in through the helper."""
    monkeypatch.setattr(pruning, "amendment_url", lambda story_id, amendment_id: None)
    html = _render(pruning.impact_view(pruned(), STORY, NAMES))
    assert f"{EXCLUDED} <code>{AMENDMENT}</code>" in html
    assert 'class="amendment"' not in html


@pytest.mark.parametrize("urlconf", ["epistrel_console.urls", "tests.urls_with_m7_pages"])
def test_amendment_ids_link_once_registered(urlconf: str) -> None:
    with override_settings(ROOT_URLCONF=urlconf):
        html = _render(pruning.impact_view(pruned(), STORY, NAMES))
    href = f"/stories/{STORY}/amendments/{AMENDMENT}/"
    assert f'<a class="amendment" href="{href}"><code>{AMENDMENT}</code></a>' in html


def test_more_than_200_targets_sign_every_eligible() -> None:
    tail = m7("prune_impact")["impact"]["to_delete"]["discarded_tails"][0]
    many = [{**tail, "id": f"01JM7CN{n:019d}"} for n in range(201)]
    view = pruning.impact_view(
        pruned(to_delete={"discarded_tails": many, "dormant_continuations": []}), STORY, NAMES
    )
    payload = twostage.unsign("prune", view.token)
    assert payload.body == {"targets": None, "ignore_warnings": True}
    page = pruning.confirm_page(STORY, payload.base, view.token, "/p/")
    assert "More than 200 targets: every eligible one at revision 20 is pruned" in page.lines


def test_impact_counts() -> None:
    assert pruning.impact_counts({"deleted": {"events": 9}}) == "—"
    assert pruning.impact_counts(None) == "—"
    assert pruning.impact_counts({"to_delete": "x"}) == "—"
    impact = m7("prune_impact")["impact"]
    assert pruning.impact_counts(impact) == "1 discarded tail, 1 dormant continuation"


def test_run_rows_and_triggers() -> None:
    runs = PruneRuns.from_json(m7("prune_runs"))
    assert runs is not None
    view = pruning.run_rows(runs)
    assert [r.trigger_text for r in view.rows] == ["Manual", "By policy (older than 30 days)"]
    assert view.count_text == "2 prunings"
    assert view.rejected == ()
    assert pruning.trigger_text("policy", {"keep_days": 1}) == "By policy"
    assert pruning.trigger_text("policy", None) == "By policy"
    assert pruning.trigger_text("operator_sweep", None) == "Operator sweep"
    bad = PruneRuns.from_json(m7("prune_runs_bad_item"))
    assert bad is not None
    shown = pruning.run_rows(bad)
    assert len(shown.rows) == 2
    assert len(shown.rejected) == 1
    assert shown.rejected_text == "1 pruning couldn't be read — see More"


def test_confirm_page_for_an_unnamed_story() -> None:
    base = {
        "timeline_revision": 20,
        "story_name": None,
        "counts": {"discarded_tails": 2, "dormant_continuations": 0, "operations": 1},
    }
    page = pruning.confirm_page(STORY, base, "tok", "/p/")
    assert (page.typed_name, page.typed_label) == (STORY, "")
    assert page.emphasis == STORY
    assert page.lines[0] == (
        "This permanently deletes the records of 2 discarded tails and 0 dormant "
        "continuations (1 operation)."
    )
    assert page.hidden == {"confirm_token": "tok"}
    assert page.danger


@pytest.mark.parametrize(("revision", "shown"), [(0, "0"), (1234, "1,234"), (None, "—")])
def test_confirm_page_dry_run_revision(revision: object, shown: str) -> None:
    """#351: revision 0 is a revision ("0"); only a missing or non-int one reads "—"."""
    base = {"timeline_revision": revision, "story_name": "Harbour Ledger", "counts": {}}
    page = pruning.confirm_page(STORY, base, "tok", "/p/")
    assert dict(page.facts)["Dry run at revision"] == shown


def test_result_view() -> None:
    body = m7("prune_applied")
    answer = Pruned.from_json(body)
    assert answer is not None
    result = pruning.result_view(answer, {}, "K")
    assert result.pruned_text == "Pruned: 1 discarded tail and 1 dormant continuation"
    assert result.deleted_text == "Deleted: Events 9, Operations 3"
    assert result.revision_text == "Story revision now 21"
    assert pruning.deleted_text({}) == "Deleted: nothing"
    assert pruning.deleted_text({"events": 1200, "rows": 0, "odd": "x"}) == (
        "Deleted: Events 1,200, Rows 0"
    )
