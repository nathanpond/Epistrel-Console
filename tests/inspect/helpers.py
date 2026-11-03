"""Helpers for the Inspect tests (#135): the seeded Lite, "Structured (M4)", Structured v2
(#188, every M5 module on) and Structured v3 (#239, D16: every M6 module on too) effective
configurations (``tests/fixtures/inspect/``), with changes laid over ``effective``; Lite as an M6
Engine serves it (``lite_m6``); the shared Alice/Bob/Pat cast (``story_cast.json``); and an
``m6/states.json`` State as a ``StateView`` body (``state_view``, #251 and #265)."""

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.fixtures import engine_fixture

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "inspect"
STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
EFFECTIVE = "/stories/{story_id}/effective-config"
STORY_PATH = "/stories/{story_id}"


def config_body(kind: str, **effective: Any) -> dict[str, Any]:
    """The ``effective_config_<kind>.json`` body; each ``effective`` key is a ``__``-separated
    path set in its ``effective`` document (``None`` removes the key)."""
    body: dict[str, Any] = json.loads((FIXTURES / f"effective_config_{kind}.json").read_text())
    body = copy.deepcopy(body)
    for path, value in effective.items():
        *parents, leaf = path.split("__")
        node = body["effective"]
        for key in parents:
            node = node.setdefault(key, {})
        if value is None:
            node.pop(leaf, None)
        else:
            node[leaf] = value
    return body


def lite(**effective: Any) -> dict[str, Any]:
    return config_body("lite", **effective)


def structured(**effective: Any) -> dict[str, Any]:
    return config_body("structured", **effective)


def structured_v2(**effective: Any) -> dict[str, Any]:
    """The seeded Structured v2 configuration (every M5 module on), ``__`` paths as above."""
    return config_body("structured_v2", **effective)


def with_settings(body: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    """A deep copy of the effective-config ``body`` with each ``"dotted.path": value`` of
    ``settings`` set under its ``effective`` (missing objects created, a non-object step
    replaced by ``{}``; ``None`` removes the key). ``capabilities`` and ``fingerprint`` are left
    as they are: gate tests read ``effective`` only."""
    copied: dict[str, Any] = copy.deepcopy(dict(body))
    for path, value in settings.items():
        *parents, leaf = path.split(".")
        node = copied["effective"]
        for key in parents:
            if not isinstance(node.get(key), dict):
                node[key] = {}
            node = node[key]
        if value is None:
            node.pop(leaf, None)
        else:
            node[leaf] = value
    return copied


def structured_v3(**effective: Any) -> dict[str, Any]:
    """The seeded Structured v3 configuration (D16: v2 plus the clock, both its flags and States
    ``on``), ``__`` paths as in :func:`config_body`."""
    return config_body("structured_v3", **effective)


# What an M6 Engine merges into a Lite story's configuration: its deployment defaults (D16).
LITE_M6_SETTINGS: Mapping[str, Any] = {
    "clock.level": "off",
    "clock.time_claims": False,
    "clock.scheduled_stops": False,
    "states.level": "off",
}


def lite_m6(**paths: Any) -> dict[str, Any]:
    """Lite as an M6 Engine serves it (D16): :data:`LITE_M6_SETTINGS` laid over :func:`lite` by
    :func:`with_settings`, then ``paths``: dotted paths passed through a mapping, e.g.
    ``lite_m6(**{"states.level": "prompt_only"})`` (``None`` removes a key).
    ``capabilities``/``fingerprint``/``deployment_defaults`` stay Lite's."""
    return with_settings(with_settings(lite(), LITE_M6_SETTINGS), paths)


CAST_STORY = "story_cast.json"
ALICE = "0000000000000000000000CA01"
BOB = "0000000000000000000000CA02"
PAT = "0000000000000000000000CA03"


def story_cast() -> dict[str, Any]:
    """The shared ``get_story`` body whose cast is Alice and Bob (``is_player: false``) and Pat
    (the player), for #190, #192 and #193."""
    loaded: dict[str, Any] = json.loads((FIXTURES / CAST_STORY).read_text())
    return loaded


def state_view(source: str, /, **changes: Any) -> dict[str, Any]:
    """A ``m6/states.json`` State as a ``StateView`` body (``changes`` laid over the State;
    revision 12, ``as_of: null``, omniscient)."""
    states = copy.deepcopy(engine_fixture("m6/states"))["states"]
    state: dict[str, Any] = next(s for s in states if s["name"] == source)
    state.update(changes)
    return {
        "state": state,
        "as_of": None,
        "timeline_revision": 12,
        "output_viewpoint": "omniscient",
    }
