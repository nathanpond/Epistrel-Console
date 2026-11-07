"""The variants panel (#52): every chain of ``GET /stories/{id}/variants`` with its "view" link
and the guardrail re-check badge."""

import re
from typing import Any

import pytest
from django.test import Client

from console.timeline import PRUNED_CHAIN_NOTE
from tests.fakes.engine import FakeEngine
from tests.history.helpers import (
    HISTORY,
    VARIANTS,
    answer,
    history_calls,
    page,
    query_of,
    variants_json,
)

pytestmark = pytest.mark.django_db


def chain_rows(html: str) -> list[str]:
    return re.findall(r'<tr class="chain membership-[^"]+">.*?</tr>', html, re.S)


def test_every_chain_is_listed(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    active, dormant = chain_rows(page(operator_client))
    assert "turn 1 · variant 1" in active
    assert "membership-active" in active
    assert "<td>—</td>" in active  # no parent
    assert re.search(r"since <[^>]*>2026-10-25 00:03:43 UTC<", active), active
    assert "turn 3 · variant 2" in dormant
    assert "membership-dormant" in dormant
    assert "<td>1:1</td>" in dormant  # parent
    assert "<td>3</td>" in dormant  # tip turn
    assert "<td>1</td>" in dormant  # turn count


def test_view_links_carry_the_variant(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient&limit=200&as_of=2&before_turn=3")
    links = re.findall(r'<a class="view-chain" href="([^"]+)">view</a>', html)
    assert [query_of(link) for link in links] == [
        {"viewpoint": ["omniscient"], "limit": ["200"], "variant": ["1:1"]},
        {"viewpoint": ["omniscient"], "limit": ["200"], "variant": ["3:2"]},
    ]
    operator_client.get(links[1].replace("&amp;", "&"))
    assert history_calls(engine)[-1] == {
        "viewpoint": ["omniscient"],
        "limit": ["200"],
        "variant": ["3:2"],
    }


def test_recheck_badge_only_when_required(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    active, dormant = chain_rows(page(operator_client))
    assert "needs a guardrail re-check to activate" in dormant
    # Complement: absent when requires_check is false.
    assert "needs a guardrail re-check" not in active
    chains = variants_json()
    chains["items"][1]["requires_check"] = False
    engine.respond("GET", VARIANTS, 200, json=chains)
    assert "needs a guardrail re-check" not in page(operator_client)


def test_a_failed_variants_read_degrades_only_its_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond(
        "GET",
        VARIANTS,
        503,
        json={"error": {"code": "database_unavailable", "message": "down", "details": {}}},
    )
    html = page(operator_client)
    assert "Engine error 503 database_unavailable" in html
    assert "Turn 1 · variant 1" in html  # the items still render


# --- Pruned chains (#293, FR-STORE-19) ---------------------------------------------------------


def _pruned_variants() -> dict[str, Any]:
    """The variants fixture with its dormant chain (3:2) pruned (E16: an inline derivation)."""
    chains = variants_json()
    chains["items"][1]["membership"] = "pruned"
    return chains


def test_a_pruned_chain_offers_no_swap_or_fork(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    engine.respond("GET", VARIANTS, 200, json=_pruned_variants())
    _, pruned = chain_rows(page(operator_client))
    assert "membership-pruned" in pruned  # the row class keeps the raw value
    assert '<span class="membership" title="pruned">Pruned</span>' in pruned
    assert f'<span class="note">{PRUNED_CHAIN_NOTE}</span>' in pruned
    for absent in ('class="button swap"', 'class="button fork"', "<button", "view-chain"):
        assert absent not in pruned, absent
    assert "needs a guardrail re-check" not in pruned  # it can never be activated
    assert re.search(r"since <time", pruned)  # the membership time stays


def test_a_dormant_chain_still_offers_swap_and_fork(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: a dormant chain renders as on main, its membership humanised (C3)."""
    answer(engine)
    active, dormant = chain_rows(page(operator_client))
    assert '<span class="membership" title="dormant">Dormant</span>' in dormant
    assert '<a class="button swap"' in dormant
    assert '<a class="button fork"' in dormant
    assert 'class="view-chain"' in dormant
    assert PRUNED_CHAIN_NOTE not in dormant
    assert '<span class="membership" title="active">Active</span>' in active


def test_a_pruned_variant_url_reads_the_note(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    engine.respond("GET", VARIANTS, 200, json=_pruned_variants())
    engine.respond(
        "GET",
        HISTORY,
        409,
        json={"error": {"code": "pruned", "message": "chain pruned", "details": {}}},
    )
    html = page(operator_client, "variant=3:2&limit=50")
    items = html.split('<section class="items">')[1].split("</section>")[0]
    assert PRUNED_CHAIN_NOTE in items
    [clear] = re.findall(r'<a class="clear-chain" href="([^"]+)">Clear chain</a>', items)
    assert query_of(clear) == {"limit": ["50"]}
    assert "Engine error" not in items  # no bare error panel
    assert "Viewing chain" not in items  # one clear link only
    # Complement: another refusal of the chain view keeps the error panel.
    engine.respond(
        "GET",
        HISTORY,
        409,
        json={"error": {"code": "unknown_chain", "message": "no such chain", "details": {}}},
    )
    other = page(operator_client, "variant=3:2").split('<section class="items">')[1]
    other = other.split("</section>")[0]
    assert "Engine error 409 unknown_chain" in other
    assert PRUNED_CHAIN_NOTE not in other
