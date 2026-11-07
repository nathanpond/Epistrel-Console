"""Certification and quality wording in M7 (#295).

**E1 — the import rule.** Every M7 page that shows a quality status or links a certification, a
certification run or an eval run imports these; none defines its own wording or link.

**E13 — quality status.** :func:`quality_line` is the one wording for every place a quality
status appears (the Overview, Config, the profile pages, the turn report, the impact report's
"Quality after", the certification pages). It reads a ``QualityStatus`` (#291's model), a
``FloorsMet`` (``EngineProfile.quality``; read as ``floors_text(passed=floors_met, failed=())``)
or a bare status string (the impact report's per-row ``quality_after``):

- ``certified`` → "Certified" (``quality-certified``), its ``certification_id`` linked through
  :func:`certification_url` (plain ``<code>`` while unregistered);
- ``uncertified`` → "Uncertified" (no modifier class);
- ``stale`` → "Stale — <INVALIDATED_REASON_TEXT>" (``quality-stale``); with no
  ``invalidated_reason``, "Stale" alone; an unknown reason humanised;
- any other status humanised, with no class.

``QualityLine.title`` is the raw status; ``css_class`` is ``console.impact.quality_class``'s
modifier ("" for uncertified), and ``templates/console/_quality.html`` always prepends
``quality``. The floors line (:func:`floors_text`) shows whenever either list is non-empty, for
any status; ``intervention_types_*`` aren't shown. The one template-side adapter is the
``quality_line`` filter in ``console/templatetags/impact.py`` (``None`` or "" → ``None``, and
``_quality.html`` renders "—").

**E6 — URL helpers** (D19: ``None`` while unregistered, or for a blank or non-string id):
:func:`certifications_url` (``engine:certifications`` with the ``fingerprint``/``profile_id``
query), :func:`certification_url`, :func:`certification_run_url` and :func:`eval_run_url`.

**E18 — no canary-run list.** Canary results are reached only through the certification they made
stale; :data:`CANARY_LIST_REQUEST` is the Engine request, cited once on the certification page.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse

from console.engine.models import FloorsMet, QualityStatus
from console.impact import quality_class
from console.wording import humanised

CANARY_LIST_REQUEST = "https://github.com/nathanpond/Epistrel/issues/776"

CERTIFIED = "certified"
UNCERTIFIED = "uncertified"
STALE = "stale"
QUALITY_TEXT: dict[str, str] = {CERTIFIED: "Certified", UNCERTIFIED: "Uncertified", STALE: "Stale"}
INVALIDATED_REASON_TEXT: dict[str, str] = {
    "canary": "a canary eval fell below a floor",
    "drift": "the model changed under the same version",
    "manual": "withdrawn by an operator",
}
STALE_SEPARATOR = " — "


@dataclass(frozen=True)
class QualityLine:
    """One quality status as ``_quality.html`` shows it: the badge ``text`` and its modifier
    class, the raw status for ``title``, the certification's page (``None`` when there is none
    or while unregistered) and id, and the floors line ("" when none)."""

    text: str
    css_class: str
    title: str
    certification_url: str | None
    floors: str
    certification_id: str = ""


def _names(values: Sequence[object] | None) -> list[str]:
    return [str(v) for v in values or () if str(v).strip()]


def floors_text(passed: Sequence[object] | None, failed: Sequence[object] | None) -> str:
    """ "Floors passed: a, b; failed: c", each list only when non-empty ("" with neither)."""
    shown = [
        f"{label}: {', '.join(names)}"
        for label, names in (("passed", _names(passed)), ("failed", _names(failed)))
        if names
    ]
    return f"Floors {'; '.join(shown)}" if shown else ""


def _status_text(status: str, reason: str | None) -> str:
    if status == STALE and reason:
        why = INVALIDATED_REASON_TEXT.get(reason, humanised(reason))
        return f"{QUALITY_TEXT[STALE]}{STALE_SEPARATOR}{why}"
    return QUALITY_TEXT.get(status, humanised(status) if status else "—")


def quality_line(status: QualityStatus | FloorsMet | str) -> QualityLine:
    """E13's wording for ``status``."""
    if isinstance(status, str):
        return QualityLine(_status_text(status, None), quality_class(status), status, None, "")
    if isinstance(status, FloorsMet):
        value = status.status
        return QualityLine(
            _status_text(value, None),
            quality_class(value),
            value,
            None,
            floors_text(status.floors_met, ()),
        )
    value = status.status
    certification_id = status.certification_id or ""
    return QualityLine(
        _status_text(value, status.invalidated_reason),
        quality_class(value),
        value,
        certification_url(certification_id),
        floors_text(status.floors_passed, status.failed_floors),
        certification_id,
    )


# --- E6: URL helpers ------------------------------------------------------------------------------


def _reversed(name: str, *ids: object) -> str | None:
    if not all(isinstance(i, str) and i for i in ids):
        return None
    try:
        return reverse(name, args=list(ids))
    except NoReverseMatch:
        return None


def certifications_url(
    *, fingerprint: str | None = None, profile_id: str | None = None
) -> str | None:
    """The Certifications list (``engine:certifications``, #313), filtered by whichever of
    ``fingerprint`` and ``profile_id`` is given."""
    path = _reversed("engine:certifications")
    if path is None:
        return None
    query = {k: v for k, v in (("fingerprint", fingerprint), ("profile_id", profile_id)) if v}
    return f"{path}?{urlencode(query)}" if query else path


def certification_url(certification_id: str | None) -> str | None:
    """One certification (``engine:certification``, #313)."""
    return _reversed("engine:certification", certification_id)


def certification_run_url(run_id: str | None) -> str | None:
    """One certification run (``engine:certification-run``, #313)."""
    return _reversed("engine:certification-run", run_id)


def eval_run_url(run_id: str | None) -> str | None:
    """One eval run (``engine:eval-run``, #316)."""
    return _reversed("engine:eval-run", run_id)
