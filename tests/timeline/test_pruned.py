"""#293: pruned chains (FR-STORE-19, ADR-175). A turn's Swap links leave a pruned chain out, and
a swap, undo rewind or fork the Engine refuses with 409 ``pruned`` reads C5's one sentence in
place of the error panel."""

from types import SimpleNamespace
from typing import Any

import pytest
from django.test import Client

from console.timeline import Basis, turn_controls
from console.wording import PRUNED_TEXT
from tests.fakes.engine import FakeEngine
from tests.timeline.helpers import STORY, error_json, path, posted, url

pytestmark = pytest.mark.django_db


def _chain(turn: int, variant: int, membership: str) -> SimpleNamespace:
    return SimpleNamespace(turn=turn, variant=variant, membership=membership, requires_check=False)


def test_turn_controls_skip_pruned_chains() -> None:
    basis = Basis(STORY, 6, 5, "Harbour", False, f"/stories/{STORY}/")
    chains = [_chain(3, 1, "active"), _chain(3, 2, "dormant"), _chain(3, 3, "pruned")]
    controls = turn_controls(basis, 3, chains=chains)
    (swap,) = controls.swaps
    assert "chain=3%3A2" in swap.url
    assert "chain=3%3A3" not in swap.url


OPERATIONS: list[tuple[str, str, list[Any], dict[str, str]]] = [
    ("swap", "swap", [], {"chain": "3:2"}),
    ("untruncate", "undo-rewind", [], {}),
    ("fork", "fork", [2], {"new_name": "Harbour (fork at turn 2)"}),
]


@pytest.mark.parametrize(("action", "route", "args", "extra"), OPERATIONS)
def test_a_pruned_swap_reads_the_sentence(
    operator_client: Client,
    engine: FakeEngine,
    action: str,
    route: str,
    args: list[Any],
    extra: dict[str, str],
) -> None:
    engine.respond("POST", path(action), 409, json=error_json("pruned", "that chain was pruned"))
    link = {"chain": extra["chain"]} if "chain" in extra else {}
    response = operator_client.post(url(route, *args, **link), posted(**extra))
    assert response.status_code == 200
    page = response.content.decode()
    assert PRUNED_TEXT in page.replace("&#x27;", "'")
    assert 'class="inspector"' not in page  # no error panel
    assert "Engine error" not in page
    assert 'class="refusal-links"' not in page

    # Complement: a 409 with a code nobody words still renders the panel.
    engine.respond("POST", path(action), 409, json=error_json("made_up_refusal", "no"))
    other = operator_client.post(url(route, *args, **link), posted(**extra)).content.decode()
    assert 'class="inspector"' in other
    assert "made_up_refusal" in other
    assert PRUNED_TEXT not in other.replace("&#x27;", "'")
