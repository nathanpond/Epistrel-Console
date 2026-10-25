"""Reactivation re-checks (#81): undo rewind, swap and fork held back behind a guardrail check.

Since Engine M3 those three edits can answer 202 (``RatingCheckStartedBody``) instead of applying:
the turns they would make active were last checked against a guardrail set that lacks a policy
now in force. The Engine pauses the story (``pause.cause: reactivation_check``) and runs a check
of ``kind: reactivation``, read with #80's endpoint. It ends ``reactivated`` with ``outcome:
{operation, result}`` (the edit's synchronous answer), ``refused`` (violations, nothing changed)
or ``failed`` (the edit could no longer be made).

This module holds what the timeline handlers (``console.views.timeline``), #80's check page
(``console.views.story_rating``) and the pause banner (``console.story_pause``) share: the op
vocabulary carried as ``?op=``, the outcome parse against #53's result models, the headings and
the #44 row's overrides once the check ends.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.urls import reverse

from console.engine.models import ForkResult, RatingCheck, SwapResult, UntruncateResult
from console.models import EngineOperation

REACTIVATION = "reactivation"  # RatingCheck.kind (Engine ``pause.REACTIVATION_KIND``)
REACTIVATION_CHECK = "reactivation_check"  # pause.cause (Engine ``pause.REACTIVATION_CHECK``)
OP_PARAM = "op"
UNTRUNCATE, SWAP, FORK = "untruncate", "swap", "fork"
# The op vocabulary (``?op=`` and ``outcome.operation``) and its display names.
OP_NAMES: dict[str, str] = {UNTRUNCATE: "Undo rewind", SWAP: "Swap", FORK: "Fork"}
CHECKING = "checking"
REACTIVATED = "reactivated"
ENDINGS = frozenset({REACTIVATED, "refused", "failed"})
UNEXPECTED_STATUS = "unexpected_status"  # the #44 error code of any other ending
GENERIC_WAITING = "A reactivated edit is waiting for a guardrail re-check"
GENERIC_SUBJECT = "The edit"
WAITING_LINE = "The story is paused until the check ends — turns can't be played meanwhile."
UNCHANGED = "The story is unchanged"
NO_FORK = "No fork was created"
FAILED_LINE = "The edit could no longer be made. The story is unchanged."
UNREADABLE = "Couldn't read the Engine's answer"

Result = UntruncateResult | SwapResult | ForkResult
_MODELS: dict[str, type[UntruncateResult] | type[SwapResult] | type[ForkResult]] = {
    UNTRUNCATE: UntruncateResult,
    SWAP: SwapResult,
    FORK: ForkResult,
}


def valid_op(value: object) -> str | None:
    """``value`` when it is one of the vocabulary's ops, else ``None``."""
    return value if isinstance(value, str) and value in OP_NAMES else None


def op_name(op: str | None) -> str:
    """ "Undo rewind" / "Swap" / "Fork"; "" for anything else."""
    return OP_NAMES.get(op or "", "")


def is_reactivation(check: RatingCheck) -> bool:
    return check.kind == REACTIVATION


def outcome_operation(check: RatingCheck | None) -> str | None:
    """``outcome.operation`` when it is in the vocabulary."""
    if check is None or not isinstance(check.outcome, dict):
        return None
    return valid_op(check.outcome.get("operation"))


def shown_op(check: RatingCheck | None, op: str | None) -> str | None:
    """The op a page names: ``outcome.operation`` once the check carries one in the vocabulary,
    else the ``?op=`` the page was opened with, else ``None`` (the generic wording)."""
    return outcome_operation(check) or valid_op(op)


def heading(status: str | None, op: str | None) -> str:
    """The reactivation page's heading for a check in ``status`` (``None``: not read yet).
    An untruncate that became a swap keeps "Undo rewind": the op names the request."""
    name = op_name(op)
    if status is None or status == CHECKING:
        return f"{name} is waiting for a guardrail re-check" if name else GENERIC_WAITING
    subject = name or GENERIC_SUBJECT
    if status == REACTIVATED:
        return f"{subject} applied after re-check"
    if status == "refused":
        return f"{subject} refused by re-check"
    if status == "failed":
        return f"{subject} could not be completed"
    return f"{subject}: re-check ended ({status})"


@dataclass(frozen=True)
class Outcome:
    """A reactivated check's ``outcome``, parsed. ``result`` is ``None`` when the outcome's
    operation is outside the vocabulary, disagrees with ``?op=``, or its result does not parse
    as that op's #53 model; the page then shows :data:`UNREADABLE` and ``raw``."""

    operation: str | None
    result: Result | None
    raw: str

    @property
    def readable(self) -> bool:
        return self.result is not None


def raw_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def parse_outcome(outcome: Mapping[str, Any] | None, op: str | None) -> Outcome:
    """``outcome`` (``{operation, result}``) against ``op`` (the page's ``?op=``, may be
    missing)."""
    raw = raw_json(outcome)
    if not isinstance(outcome, Mapping):
        return Outcome(None, None, raw)
    operation = valid_op(outcome.get("operation"))
    if operation is None or (valid_op(op) is not None and op != operation):
        return Outcome(operation, None, raw)
    return Outcome(operation, _MODELS[operation].from_json(outcome.get("result")), raw)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def job_overrides(outcome: Mapping[str, Any] | None) -> dict[str, Any]:
    """The #44 row's overrides for a ``reactivated`` check, from the raw ``outcome.result``
    (so an answer the Console cannot parse still fills what it can): ``engine_operation_id``
    and ``timeline_revision``, and for a fork ``story_id`` (the new story)."""
    if not isinstance(outcome, Mapping):
        return {}
    result = outcome.get("result")
    if not isinstance(result, Mapping):
        return {}
    found: dict[str, Any] = {}
    operation_id = _str(result.get("operation_id"))
    if operation_id is not None and len(operation_id) <= 26:
        found["engine_operation_id"] = operation_id
    revision = _int(result.get("timeline_revision"))
    if revision is not None:
        found["timeline_revision"] = revision
    story_id = _str(result.get("story_id"))
    if outcome.get("operation") == FORK and story_id is not None and len(story_id) <= 26:
        found["story_id"] = story_id
    return found


def job_row(job_id: str | None) -> EngineOperation | None:
    """The #44 row that started job ``job_id`` (the first one: a replay records no second
    ``queued`` row), from the Console's own database."""
    if not job_id:
        return None
    return EngineOperation.objects.filter(job_id=job_id).order_by("id").first()


def job_op(job_id: str | None) -> str | None:
    """The op whose 202 started job ``job_id``, from its #44 row's ``kind`` (no Engine call)."""
    row = job_row(job_id)
    return valid_op(row.kind) if row is not None else None


def requested_fork_name(row: EngineOperation | None) -> str:
    """The new story's name a held-back fork asked for (its #44 row's request body)."""
    if row is None or not isinstance(row.request, dict):
        return ""
    body = row.request.get("body")
    name = body.get("name") if isinstance(body, dict) else None
    return name if isinstance(name, str) else ""


def head_turn(result: UntruncateResult | SwapResult) -> int:
    return result.next_turn_index - 1


def transcript_url(story_id: str, result: UntruncateResult | SwapResult) -> str:
    """#52's transcript at the restored head turn, along ``active_chain`` when there is one."""
    head = head_turn(result)
    params = {"as_of": str(head)}
    if result.active_chain is not None:
        params["variant"] = f"{result.active_chain.turn}:{result.active_chain.variant}"
    return f"{reverse('stories:history', args=[story_id])}?{urlencode(params)}#turn-{head}"
