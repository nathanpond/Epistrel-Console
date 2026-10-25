"""How the Console presents the Engine's model configuration (#46): each purpose's values with
where they come from, the purposes' parents, and the model-guide link.

The Engine reports a source per field (``own | inherited | default``; ``params`` per key) but not
which purpose ``inherited`` means, so :data:`PARENTS` mirrors the Engine's ``parent_of`` (each
purpose's registry ``default_model_key``, FR-MODEL-2). A param no row sets has no source; it
shows as ``default`` with no value.
"""

import re
from dataclasses import dataclass
from typing import Any

from console.engine.models import ModelConfig, ModelConfigEntry

DEFAULT = "default"
# The Engine's parent purposes (``parent_of``): the row a purpose inherits from before default.
PARENTS = {
    "lite_summary": "consolidation",
    "rating_suggestion": "guardrail_check",
    "regeneration": "generation",
}
# Purposes shown read-only, with the note shown in their place (owner decision B).
READ_ONLY = {"embedding": "Configured in M4 (#59)"}
# The parameters the Console edits, in display order.
PARAMS = ("temperature", "top_p", "max_tokens", "timeout", "stop", "seed")
PARAM_LABELS = {
    "temperature": "temperature",
    "top_p": "top_p",
    "max_tokens": "max_tokens",
    "timeout": "timeout (s)",
    "stop": "stop",
    "seed": "seed",
}
NOT_SET = "— (not set)"

GUIDE_REPO = "https://github.com/nathanpond/Epistrel/blob"
GUIDE_PATH = "docs/models.md"
_RELEASE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")  # exactly X.Y.Z (fullmatch)


def parent_of(purpose: str) -> str | None:
    return PARENTS.get(purpose)


def is_read_only(purpose: str) -> bool:
    return purpose in READ_ONLY


def value_text(value: object) -> str:
    """A value as the list shows it: ``stop`` comma-joined, numbers as returned, null as not set."""
    if value is None:
        return NOT_SET
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "[]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@dataclass(frozen=True)
class Sourced:
    """One effective value and where it came from (``source`` is ``None`` on ``default``)."""

    value: object
    source: str | None
    origin: str  # the purpose the value comes from: itself, its parent, or "default"
    marker: str  # "own", "inherited from <parent>", "default", or "" on default

    @property
    def text(self) -> str:
        return value_text(self.value)

    @property
    def own(self) -> bool:
        return self.source is None or self.source == "own"

    @property
    def placeholder(self) -> str:
        """An edit form's hint for a field the purpose does not set itself."""
        return f"{self.origin}: {self.text}"


@dataclass(frozen=True)
class PurposeConfig:
    name: str
    parent: str | None
    base_url: Sourced
    model: Sourced
    params: tuple[tuple[str, Sourced], ...]  # PARAMS, in order
    other_params: tuple[tuple[str, str], ...]  # keys beyond PARAMS, read-only
    api_key_set: bool
    api_key_fingerprint: str | None
    read_only_note: str

    @property
    def is_default(self) -> bool:
        return self.name == DEFAULT

    @property
    def read_only(self) -> bool:
        return bool(self.read_only_note)

    def param(self, name: str) -> Sourced:
        return dict(self.params)[name]


def _sourced(purpose: str, value: object, source: str | None) -> Sourced:
    if purpose == DEFAULT:
        return Sourced(value, None, DEFAULT, "")
    if source == "own":
        return Sourced(value, "own", purpose, "own")
    if source == "inherited":
        parent = parent_of(purpose)
        if parent is None:
            return Sourced(value, "inherited", "parent", "inherited")
        return Sourced(value, "inherited", parent, f"inherited from {parent}")
    return Sourced(value, "default", DEFAULT, "default")


def purpose_config(name: str, entry: ModelConfigEntry) -> PurposeConfig:
    sources = entry.sources
    params = tuple(
        (key, _sourced(name, entry.params.get(key), sources.params.get(key))) for key in PARAMS
    )
    others = tuple(
        (key, value_text(value)) for key, value in entry.params.items() if key not in PARAMS
    )
    return PurposeConfig(
        name=name,
        parent=parent_of(name),
        base_url=_sourced(name, entry.base_url, sources.base_url),
        model=_sourced(name, entry.model, sources.model),
        params=params,
        other_params=others,
        api_key_set=entry.api_key_set,
        api_key_fingerprint=entry.api_key_fingerprint,
        read_only_note=READ_ONLY.get(name, ""),
    )


def purposes(config: ModelConfig) -> list[PurposeConfig]:
    """Every purpose, ``default`` first, then the rest in the Engine's order."""
    names = sorted(config.purposes, key=lambda n: n != DEFAULT)  # stable: Engine order kept
    return [purpose_config(name, config.purposes[name]) for name in names]


@dataclass(frozen=True)
class GuideLink:
    url: str
    label: str


def guide_link(engine_version: str | None) -> GuideLink:
    """The model guide for the running Engine: its release tag's ``docs/models.md``, else
    ``main``'s (a pre-release, a dev build, or an unknown version)."""
    if engine_version is None:
        return GuideLink(f"{GUIDE_REPO}/main/{GUIDE_PATH}", "Model guide (Engine version unknown)")
    if _RELEASE.fullmatch(engine_version):
        return GuideLink(
            f"{GUIDE_REPO}/v{engine_version}/{GUIDE_PATH}",
            f"Model guide for Engine {engine_version}",
        )
    return GuideLink(
        f"{GUIDE_REPO}/main/{GUIDE_PATH}", f"Model guide for Engine {engine_version} (main)"
    )


def engine_version(doc: dict[str, Any]) -> str | None:
    """``info.version`` of the Engine's OpenAPI document, when it is a string."""
    info = doc.get("info")
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) and version else None
