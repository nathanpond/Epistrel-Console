"""The variants panel (#52): every chain of ``GET /stories/{id}/variants`` with its "view" link
and the guardrail re-check badge."""

import re

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.history.helpers import (
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
