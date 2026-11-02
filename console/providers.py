"""External-data provider manifests (#240): pure helpers, no Engine call.

The Engine's provider registry (FR-EXT-1,6) stores each provider's manifest as registered:
template text and ``{secret.<NAME>}`` references, never a secret value. These helpers read a
manifest the way the Engine does (``core/providers.py`` at origin/main):

- :data:`PROVIDER_NAME` / :func:`is_provider_name`: the Engine's ``PROVIDER_NAME_PATTERN``, so a
  page refuses a malformed name from the URL before any call;
- :func:`placeholders`: the inside of every pair of braces in every string of the manifest
  (object keys excluded), in document order, de-duplicated (``strings_in`` +
  ``placeholders_in``);
- :func:`binding_keys` / :func:`secret_names`: the names of ``{binding.<key>}`` and
  ``{secret.<NAME>}`` placeholders (the Engine's kind/dot partition, any name, empty names
  kept), as lists in document order. #246's forms, #247's bind form and #259's creation rows
  import these, never redefine them (D17's rule);
- :func:`secret_env_var`: the Engine environment variable a secret reference resolves from;
- :func:`manifest_values`: each tracked value's name, type and access, in manifest order.

Every manifest helper takes ``manifest: object`` (a parsed textarea value may be passed
uncast) and never raises: the Engine validated the manifest at registration (C15), so the
shape checks here are defensive only. Linking a provider's name is
``console.effects.provider_url`` (D19), not this module's.
"""

import re
from collections.abc import Iterator, Mapping
from typing import NamedTuple

PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
BRACES = re.compile(r"\{([^{}]*)\}")
BINDING = "binding"
SECRET = "secret"  # noqa: S105 - a placeholder kind, not a secret
HTTP = "http"
IN_PROCESS = "in_process"
READ = "read"
READ_WRITE = "read, write"
WRITABLE = "writable"
READ_ONLY = "read-only"
NO_ACCESS = "—"


class ManifestValue(NamedTuple):
    """One tracked value: its name, the manifest's ``type`` word verbatim ("" when missing or
    not a string) and its access ("read", "read, write", "writable", "read-only" or "—")."""

    name: str
    type: str
    access: str


def is_provider_name(value: object) -> bool:
    """A ``str`` that the whole of :data:`PROVIDER_NAME` matches (``bank\\n`` is refused)."""
    return isinstance(value, str) and PROVIDER_NAME.fullmatch(value) is not None


def _strings_in(value: object) -> Iterator[str]:
    """Every string inside a JSON value (object keys excluded), in document order."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings_in(item)


def _unique(items: Iterator[str]) -> list[str]:
    return list(dict.fromkeys(items))


def placeholders(manifest: object) -> list[str]:
    """The inside of every pair of braces in every string of ``manifest``, de-duplicated in
    first-use order."""
    return _unique(m.group(1) for text in _strings_in(manifest) for m in BRACES.finditer(text))


def _named(manifest: object, kind: str) -> list[str]:
    found: list[str] = []
    for inner in placeholders(manifest):
        head, dot, name = inner.partition(".")
        if head == kind and dot:
            found.append(name)
    return _unique(iter(found))


def binding_keys(manifest: object) -> list[str]:
    """The names of the manifest's ``{binding.<key>}`` placeholders: the ids each story using
    the provider must bind (the Engine's ``binding_keys``, as a list in document order)."""
    return _named(manifest, BINDING)


def secret_names(manifest: object) -> list[str]:
    """The names of the manifest's ``{secret.<NAME>}`` references, in document order."""
    return _named(manifest, SECRET)


def secret_env_var(name: str) -> str:
    """The Engine environment variable ``{secret.<name>}`` resolves from (Engine
    ``SECRET_ENV_PREFIX``); the name verbatim. The Console never reads it."""
    return "EPISTREL_PROVIDER_SECRET_" + name


def _access(spec: Mapping[object, object], transport: object) -> str:
    if transport == IN_PROCESS:
        return WRITABLE if spec.get(WRITABLE) is True else READ_ONLY
    if "write" in spec:
        return READ_WRITE
    if READ in spec:
        return READ
    return NO_ACCESS


def manifest_values(manifest: object, transport: object) -> list[ManifestValue]:
    """The manifest's tracked values in manifest order. ``values`` not an object → ``[]``; a
    value that isn't an object is skipped."""
    if not isinstance(manifest, Mapping):
        return []
    values = manifest.get("values")
    if not isinstance(values, Mapping):
        return []
    rows: list[ManifestValue] = []
    for name, spec in values.items():
        if not isinstance(spec, Mapping):
            continue
        kind = spec.get("type")
        rows.append(
            ManifestValue(
                str(name), kind if isinstance(kind, str) else "", _access(spec, transport)
            )
        )
    return rows
