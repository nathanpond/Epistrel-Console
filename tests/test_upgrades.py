"""E13's presets and E14's upgrade mode (#295): ``console.upgrades`` and
``console.forms.upgrades``."""

import pytest

from console.engine.models import EngineProfile
from console.forms.upgrades import (
    UPGRADE_MODE_CHOICES,
    UPGRADE_MODE_HELP,
    UpgradeModeField,
    UpgradeModeForm,
)
from console.upgrades import PRESET_IDS, preset_label, preset_order, upgrade_mode_text
from tests.fixtures import engine_fixture

LITE, FULL, STANDARD, MAX = (f"00000000000000000000PRF00{n}" for n in (1, 2, 3, 4))


def presets() -> list[EngineProfile]:
    page = engine_fixture("m7/presets")
    found = [EngineProfile.from_json(item) for item in page["items"]]
    assert all(p is not None for p in found)
    return [p for p in found if p is not None]


def custom(profile_id: str, name: str, *, preset: bool = False) -> EngineProfile:
    base = engine_fixture("m7/presets")["items"][0]
    found = EngineProfile.from_json({**base, "id": profile_id, "name": name, "preset": preset})
    assert found is not None
    return found


def test_preset_ids_are_the_ladder() -> None:
    assert PRESET_IDS == (LITE, STANDARD, FULL, MAX)


def test_preset_order_puts_the_presets_first_in_ladder_order() -> None:
    found = presets()  # the fixture's order: Lite, Full, Standard, Max
    shuffled = [found[3], found[1], found[0], found[2]]
    mine = custom("01JM7PF0000000000000000001", "Zebra")
    yours = custom("01JM7PF0000000000000000002", "Apple")
    ordered = preset_order([mine, *shuffled, yours])
    assert [p.name for p in ordered] == ["Lite", "Standard", "Full", "Max", "Zebra", "Apple"]


def test_an_unknown_preset_sorts_after_max_by_name() -> None:
    later = custom("01JM7PF0000000000000000009", "Ultra", preset=True)
    earlier = custom("01JM7PF0000000000000000008", "Mega", preset=True)
    mine = custom("01JM7PF0000000000000000001", "Mine")
    ordered = preset_order([mine, later, *presets(), earlier])
    assert [p.name for p in ordered] == ["Lite", "Standard", "Full", "Max", "Mega", "Ultra", "Mine"]


def test_preset_label() -> None:
    assert preset_label(presets()[0]) == "Lite (preset)"
    assert preset_label(custom("01JM7PF0000000000000000001", "Mine")) == "Mine"


@pytest.mark.parametrize("value", ["backfill", "forward_only"])
def test_the_two_upgrade_modes_clean(value: str) -> None:
    assert UpgradeModeField().clean(value) == value
    form = UpgradeModeForm({"upgrade_mode": value})
    assert form.is_valid()
    assert form.mode() == value


@pytest.mark.parametrize("value", ["upgradeMode", ""])
def test_anything_else_fails(value: str) -> None:
    form = UpgradeModeForm({"upgrade_mode": value})
    assert not form.is_valid()
    assert "upgrade_mode" in form.errors


def test_the_field_and_form() -> None:
    assert UPGRADE_MODE_CHOICES == (
        ("backfill", "Backfill the earlier turns (the story pauses while it runs)"),
        ("forward_only", "Forward only (earlier turns keep a coverage gap)"),
    )
    assert UPGRADE_MODE_HELP == (
        "Only matters when the change turns on something that produces data; otherwise it's ignored"
    )
    field = UpgradeModeField()
    assert field.initial == "backfill"
    assert field.help_text == UPGRADE_MODE_HELP
    html = str(UpgradeModeForm(form_id="config-confirm")["upgrade_mode"])
    assert html.count('form="config-confirm"') == 2
    assert 'value="backfill"' in html
    assert "checked" in html.split('value="forward_only"')[0]
    assert 'form="' not in str(UpgradeModeForm()["upgrade_mode"])
    assert UpgradeModeForm()["upgrade_mode"].label == "Earlier turns"
    assert UpgradeModeForm().fields["upgrade_mode"].required is True
    picked = str(UpgradeModeForm(initial_mode="forward_only")["upgrade_mode"])
    assert "checked" in picked.split('value="forward_only"')[1].split(">")[0]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("backfill", "Backfill"),
        ("forward_only", "Forward only"),
        ("deferred_backfill", "Deferred backfill"),
        (None, "—"),
        ("", "—"),
    ],
)
def test_upgrade_mode_text(value: str | None, text: str) -> None:
    assert upgrade_mode_text(value) == text
