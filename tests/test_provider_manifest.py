"""#240: the provider manifest helpers (``console/providers.py``), table-driven over inline
manifests."""

from typing import Any

import pytest

from console.providers import (
    ManifestValue,
    binding_keys,
    is_provider_name,
    manifest_values,
    placeholders,
    secret_env_var,
    secret_names,
)

# The Engine's own ``orchestrator/providers/manifest.py`` ``EXAMPLE`` manifest (origin/main).
EXAMPLE: dict[str, Any] = {
    "values": {
        "gold": {
            "type": "number",
            "read": {
                "url": "https://api.game.example/players/{binding.player_id}/gold",
                "headers": {"Authorization": "Bearer {secret.GAME_TOKEN}"},
                "value_path": "$.balance",
            },
        }
    }
}

EVERYWHERE: dict[str, Any] = {
    "values": {
        "balance": {
            "type": "number",
            "read": {
                "method": "GET",
                "url": "https://x.test/{binding.account}/{binding.account}",
                "headers": {"X-Realm": "{binding.realm}"},
                "query": {"region": "{binding.region}"},
                "value_path": "$.v",
            },
            "write": {
                "method": "POST",
                "url": "https://x.test/{story.id}",
                "headers": {},
                "body": {"nested": [{"deep": "{binding.deep}"}, "{effect.delta}"]},
                "idempotency_header": "Idempotency-Key",
                "outcome_path": "$.s",
                "outcome_map": {"confirmed": ["ok"]},
            },
        }
    }
}


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (EXAMPLE, ["player_id"]),
        # A key used twice is listed once, in first-use order; header, query and a nested body.
        (EVERYWHERE, ["account", "realm", "region", "deep"]),
        # **Complement:** an object key is no string of the manifest; secrets and story ids
        # are other kinds.
        ({"{binding.k}": "x"}, []),
        ({"a": "{secret.X}", "b": "{story.id}"}, []),
        # The Engine's partition: any name after ``binding.``, empty kept; no dot is no kind.
        ({"a": "{binding.}", "b": "{binding}"}, [""]),
        ([("{binding.t}",)], ["t"]),
        ("not a manifest {binding.s}", ["s"]),
        (None, []),
    ],
)
def test_binding_keys(manifest: object, expected: list[str]) -> None:
    assert binding_keys(manifest) == expected


def test_placeholders_in_document_order_deduplicated() -> None:
    assert placeholders(EVERYWHERE) == [
        "binding.account",
        "binding.realm",
        "binding.region",
        "story.id",
        "binding.deep",
        "effect.delta",
    ]
    assert placeholders({"{binding.k}": "x"}) == []
    assert placeholders({"a": "{{nested}}"}) == ["nested"]


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (EXAMPLE, ["GAME_TOKEN"]),
        (
            {"h": "Bearer {secret.GAME_TOKEN}", "i": "{secret.GAME_TOKEN} {secret.B}"},
            ["GAME_TOKEN", "B"],
        ),
        ({"{secret.KEY}": "x"}, []),
        (EVERYWHERE, []),
    ],
)
def test_secret_names(manifest: object, expected: list[str]) -> None:
    assert secret_names(manifest) == expected


def test_secret_env_var() -> None:
    assert secret_env_var("GAME_TOKEN") == "EPISTREL_PROVIDER_SECRET_GAME_TOKEN"


@pytest.mark.parametrize(
    ("manifest", "transport", "expected"),
    [
        (EXAMPLE, "http", [ManifestValue("gold", "number", "read")]),
        (EVERYWHERE, "http", [ManifestValue("balance", "number", "read, write")]),
        (
            {"values": {"stock": {"type": "number", "writable": True}}},
            "in_process",
            [ManifestValue("stock", "number", "writable")],
        ),
        (
            {"values": {"stock": {"type": "string", "writable": False}}},
            "in_process",
            [ManifestValue("stock", "string", "read-only")],
        ),
        (
            {"values": {"stock": {"type": "number"}}},
            "in_process",
            [ManifestValue("stock", "number", "read-only")],
        ),
        # Manifest order; a value that isn't an object is skipped; a missing type is "".
        (
            {"values": {"b": {"writable": True}, "skip": 3, "a": {"type": 7}}},
            "in_process",
            [ManifestValue("b", "", "writable"), ManifestValue("a", "", "read-only")],
        ),
        ({"values": {"x": {"type": "number"}}}, "grpc", [ManifestValue("x", "number", "—")]),
        ({"values": []}, "http", []),
        ({}, "http", []),
        ("nope", "http", []),
    ],
)
def test_manifest_values(manifest: object, transport: str, expected: list[ManifestValue]) -> None:
    assert manifest_values(manifest, transport) == expected


@pytest.mark.parametrize("name", ["bank", "game_ledger", "a.b-c", "a" * 64])
def test_provider_names_accepted(name: str) -> None:
    assert is_provider_name(name) is True


@pytest.mark.parametrize("name", ["Bank", "1bank", "_x", "", "a" * 65, "bank\n", None, 7])
def test_provider_names_refused(name: object) -> None:
    assert is_provider_name(name) is False
