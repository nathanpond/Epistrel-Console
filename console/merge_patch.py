"""JSON Merge Patch (RFC 7386) diffs for PATCH edits (#64, M3 convention 5).

``diff(original, new)`` is the smallest merge patch that turns ``original`` into ``new``:

- a key whose value changed is sent with its new value; when both values are objects the diff
  recurses, so only the changed leaves are sent;
- a key present in ``original`` and absent from ``new`` is sent as ``null`` (removed);
- a list is replaced whole (merge patch has no list operations);
- nothing changed → ``{}``, and an edit view makes **no** Engine call for an empty diff.

``original`` is the snapshot the edit page rendered from (#43's signed snapshot), ``new`` the
cleaned form. Merge patch cannot carry a ``null`` *value* (``null`` means "remove"): an edit that
clears a nullable field sends ``null``, which the Engine reads as unset. A key that is new in
``new`` with an object value is sent as given; ``None`` values inside it would read as removals,
so callers leave blank optionals out instead (convention 5: create omits blank optionals).
"""

from collections.abc import Mapping
from typing import Any


def diff(original: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """The RFC 7386 merge patch from ``original`` to ``new`` (both JSON objects)."""
    patch: dict[str, Any] = {}
    for key in original:
        if key not in new:
            patch[key] = None
    for key, value in new.items():
        if key not in original:
            patch[key] = value
            continue
        before = original[key]
        if isinstance(before, Mapping) and isinstance(value, Mapping):
            nested = diff(before, value)
            if nested:
                patch[key] = nested
        elif not _same(before, value):
            patch[key] = value
    return patch


def _same(a: Any, b: Any) -> bool:
    """JSON equality: ``1 == 1.0`` but ``True != 1`` (a JSON boolean is not a number)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return bool(a == b)


def apply(target: Any, patch: Any) -> Any:
    """Apply merge patch ``patch`` to ``target`` (RFC 7386 §2), returning a new value; for
    tests and previews (``apply(original, diff(original, new)) == new``)."""
    if not isinstance(patch, Mapping):
        return patch
    result: dict[str, Any] = dict(target) if isinstance(target, Mapping) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = apply(result.get(key), value)
    return result
