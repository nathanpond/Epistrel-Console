"""E23's shared renderers for raw M7 values (#294): every M7 module that renders an Engine value
the Console doesn't type imports these and defines no copy of its own.

**E1 — the import rule.** A page story (#297 onwards) imports these helpers; it never defines a
pluraliser, a compact-JSON or number formatter, or a More renderer of its own. A story that
landed before this module (#292, #318, #322, #327) formats inline to the same rules and isn't
retrofitted.

**E23 — rendering raw M7 values (refines C3, convention 2, E15).**

- **Missing.** A null, absent or blank value reads "—"; an empty list reads "None" (or its
  heading is left out where E3 says so).
- **Malformed entries never raise.** A non-object entry, or one missing the members a cell
  needs, becomes a row whose cells read "—", with the raw entry as compact JSON under More.
- **Numbers** (:func:`number_text`). Ints with ``{:,}``; floats up to three decimals with
  trailing zeros stripped (0.910 → "0.91", 0.025 → "0.025"); a bool or non-number reads "—".
- **Plurals** (:func:`plural`). Every "(s)" in a wording constant is pluralised in the rendered
  text ("1 conflict", "2 conflicts", "3,650 days"); a verb agrees with the noun.
- **Other raw values** (:func:`compact_json`): a string as is, anything else as compact JSON
  (``separators=(",", ":")``, ``sort_keys=True``, ``ensure_ascii=False``), cut at about 120
  characters with "…" in a cell.
- **More** (:func:`more_json`) is the full JSON (indent 2, sorted keys, ``ensure_ascii=False``),
  never cut: ``<details><summary>More</summary><pre>…</pre></details>``.
"""

import json
import math
from typing import Any

EMPTY = "—"
ELLIPSIS = "…"
CELL_LIMIT = 120  # E23: an in-cell <code> value is cut at about this many characters


def _count(n: object) -> str:
    if isinstance(n, int) and not isinstance(n, bool):
        return f"{n:,}"
    return str(n)


def plural(n: int, singular: str, plural: str | None = None) -> str:
    """ "1 conflict", "2 conflicts", "3,650 days", ``plural(2, "story", "stories")`` → "2
    stories" (C11; the number with ``{:,}``)."""
    word = singular if n == 1 else (plural if plural is not None else f"{singular}s")
    return f"{_count(n)} {word}"


def compact_json(value: Any, limit: int | None = None) -> str:
    """``{"b": 1, "a": "é"}`` → ``{"a":"é","b":1}``; with ``limit``, a longer text is cut to
    its first ``limit`` characters and "…"."""
    try:
        text = json.dumps(
            value, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str
        )
    except TypeError:  # keys of mixed types can't be sorted
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    if limit is not None and len(text) > limit:
        return text[:limit] + ELLIPSIS
    return text


def number_text(value: Any) -> str:
    """E23's number rule: ``1200`` → "1,200", ``0.910`` → "0.91", ``0.025`` → "0.025"; a bool,
    a non-number or a non-finite float → "—"."""
    if isinstance(value, bool):
        return EMPTY
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            return EMPTY
        text = f"{value:,.3f}".rstrip("0").rstrip(".")
        return "0" if text == "-0" else text
    return EMPTY


def more_json(value: Any) -> str:
    """The full JSON for More: indent 2, sorted keys, non-ASCII kept, never cut."""
    try:
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return json.dumps(value, indent=2, ensure_ascii=False, default=str)
