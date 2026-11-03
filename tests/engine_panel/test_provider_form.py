"""#246: the provider registration form (``console/forms/providers.py``), no Engine."""

import json
from typing import Any

import pytest

from console.engine_api_check import RECORDED
from console.forms.providers import (
    NAME_INVALID,
    READ_ONLY_NEEDS_NONE,
    SPEC_FIELDS,
    WRITE_NEEDS,
    ProviderForm,
    schema_defaults,
)

SPEC = RECORDED["components"]["schemas"]["ProviderSpec"]
MANIFEST = {"values": {"balance": {"type": "number", "read": {"url": "https://b.example/x"}}}}


def data(**changes: Any) -> dict[str, str]:
    """A valid read-only registration as posted."""
    posted = {
        "name": "bank",
        "trust_tier": "authoritative",
        "transport": "http",
        "manifest": json.dumps(MANIFEST),
        "write_capable": "false",
        "confirmation": "",
        "dedupes_dispatch": "",
        "returns_resulting_values": "",
        "unavailability": "last_known",
        "freshness_minutes": "",
        "confirm_wait_max_seconds": "10",
    }
    posted.update(changes)
    return posted


def form(registering: bool = True, **changes: Any) -> ProviderForm:
    return ProviderForm(data(**changes), registering=registering, initial=schema_defaults())


def test_spec_has_exactly_the_schema_members() -> None:
    f = form()
    assert f.is_valid(), f.errors
    assert set(f.spec()) == set(SPEC["properties"])
    assert set(SPEC_FIELDS) == set(SPEC["properties"])
    assert len(f.spec()) == 10
    assert set(f.registration()) - set(f.spec()) == {"name"}
    assert f.registration()["name"] == "bank"
    assert f.spec()["manifest"] == MANIFEST
    assert f.spec()["freshness_minutes"] is None
    assert f.spec()["write_capable"] is False


def test_register_initial_values_are_the_schema_defaults() -> None:
    """The guard: the register page starts from the recorded ``ProviderSpec`` defaults."""
    expected = {m: p.get("default") for m, p in SPEC["properties"].items()}
    assert schema_defaults() == expected
    assert expected["write_capable"] is False
    assert expected["unavailability"] == "last_known"
    assert expected["confirm_wait_max_seconds"] == 10
    f = ProviderForm(registering=True, initial=schema_defaults())
    assert f["write_capable"].value() == "false"
    assert f["unavailability"].value() == "last_known"
    assert f["confirm_wait_max_seconds"].value() == "10"
    assert f["freshness_minutes"].value() == ""
    assert f["confirmation"].value() == ""


def test_write_capable_needs_its_three_declarations() -> None:
    f = form(write_capable="true", dedupes_dispatch="true")
    assert not f.is_valid()
    assert f.errors["confirmation"] == [WRITE_NEEDS]
    assert f.errors["returns_resulting_values"] == [WRITE_NEEDS]
    assert "dedupes_dispatch" not in f.errors


def test_write_capable_with_all_three_is_valid() -> None:
    f = form(
        write_capable="true",
        confirmation="synchronous",
        dedupes_dispatch="false",
        returns_resulting_values="true",
    )
    assert f.is_valid(), f.errors
    spec = f.spec()
    assert (spec["confirmation"], spec["dedupes_dispatch"], spec["returns_resulting_values"]) == (
        "synchronous",
        False,
        True,
    )


def test_read_only_declares_none() -> None:
    f = form(confirmation="asynchronous", dedupes_dispatch="false")
    assert not f.is_valid()
    assert f.errors["confirmation"] == [READ_ONLY_NEEDS_NONE]
    assert f.errors["dedupes_dispatch"] == [READ_ONLY_NEEDS_NONE]
    assert "returns_resulting_values" not in f.errors


@pytest.mark.parametrize("name", ["Bank", "1x", "-x", "", "a" * 65, "bank x"])
def test_name_pattern(name: str) -> None:
    f = form(name=name)
    assert not f.is_valid()
    expected = [NAME_INVALID] if name else ["This field is required."]
    assert f.errors["name"] == expected


def test_name_is_stripped_not_lowercased() -> None:
    f = form(name="  bank.v2_x-1 ")
    assert f.is_valid(), f.errors
    assert f.registration()["name"] == "bank.v2_x-1"


def test_replace_form_has_no_name() -> None:
    f = form(registering=False)
    assert "name" not in f.fields
    assert f.is_valid(), f.errors
    assert set(f.spec()) == set(SPEC_FIELDS)


def test_freshness_blank_is_null() -> None:
    f = form(freshness_minutes="")
    assert f.is_valid(), f.errors
    assert f.spec()["freshness_minutes"] is None
    f = form(freshness_minutes="010")
    assert f.is_valid(), f.errors
    assert f.spec()["freshness_minutes"] == 10


@pytest.mark.parametrize(
    "value", ["0", "525601", "1.5", "-1", "\u0661\u0660"]
)  # the last: Arabic-Indic digits
def test_freshness_range(value: str) -> None:
    f = form(freshness_minutes=value)
    assert not f.is_valid()
    assert f.errors["freshness_minutes"] == ["Enter a whole number from 1 to 525,600"]


@pytest.mark.parametrize("value", ["", "0", "301", "x"])
def test_confirm_wait_range(value: str) -> None:
    f = form(confirm_wait_max_seconds=value)
    assert not f.is_valid()
    assert f.errors["confirm_wait_max_seconds"] == ["Enter a whole number from 1 to 300"]


def test_numbers_carry_no_min_max() -> None:
    f = ProviderForm(registering=True, initial=schema_defaults())
    for member in ("freshness_minutes", "confirm_wait_max_seconds"):
        html = str(f[member])
        assert 'type="number"' in html
        assert 'step="1"' in html
        assert "min=" not in html
        assert "max=" not in html


def test_manifest_must_be_an_object() -> None:
    f = form(manifest="[]")
    assert not f.is_valid()
    assert str(f.errors["manifest"][0]).startswith("Enter a JSON object")
    f = form(manifest="{bad")
    assert not f.is_valid()
    assert str(f.errors["manifest"][0]).startswith("Enter valid JSON")


def test_selects_offer_choose_and_null() -> None:
    f = ProviderForm(registering=True, initial=schema_defaults())
    trust = list(f.fields["trust_tier"].choices)  # type: ignore[attr-defined]
    assert trust == [("", "Choose…"), ("authoritative", "Authoritative"), ("advisory", "Advisory")]
    confirmation = list(f.fields["confirmation"].choices)  # type: ignore[attr-defined]
    assert confirmation[0] == ("", "— (read-only provider)")
    assert list(f.fields["dedupes_dispatch"].choices) == [  # type: ignore[attr-defined]
        ("", "—"),
        ("true", "Yes"),
        ("false", "No"),
    ]
    assert list(f.fields["write_capable"].choices) == [  # type: ignore[attr-defined]
        ("false", "No"),
        ("true", "Yes"),
    ]
    unavailable = [v for v, _ in f.fields["unavailability"].choices]  # type: ignore[attr-defined]
    assert unavailable == ["fail_turn", "last_known", "unknown"]


def test_unknown_stored_enum_is_an_extra_choice() -> None:
    initial = {**schema_defaults(), "trust_tier": "sovereign", "transport": "http"}
    f = ProviderForm(registering=False, initial=initial)
    assert ("sovereign", "sovereign (unknown)") in list(
        f.fields["trust_tier"].choices  # type: ignore[attr-defined]
    )
    bound = ProviderForm(data(trust_tier="sovereign"), registering=False, initial=initial)
    assert bound.is_valid(), bound.errors
    assert bound.spec()["trust_tier"] == "sovereign"


def test_help_comes_from_the_schema() -> None:
    f = ProviderForm(registering=True, initial=schema_defaults())
    manifest_help = f.fields["manifest"].help_text
    for word in (
        "`http`",
        "`in_process`",
        "{binding.<key>}",
        "{story.id}",
        "{value.scope_key}",
        "{effect.delta}",
        "{effect.value}",
        "{effect.idempotency_key}",
        "{secret.<NAME>}",
        "EPISTREL_PROVIDER_SECRET_<NAME>",
        "Paths are `$`, `.key` and `[n]` only",
    ):
        assert word in manifest_help, word
    assert "`fail_turn`" in f.fields["unavailability"].help_text
    assert f.fields["freshness_minutes"].help_text == "Blank: 60. Used only with Last known."
