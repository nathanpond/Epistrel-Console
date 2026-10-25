"""Two-stage writes (#64, M3 convention 3): a report first, then a signed confirmation.

When the Engine answers ``applied: false`` (or a dry run previews), the view renders the report
with the shared partial ``console/_two_stage.html`` ("Apply anyway" / "Start" / "Erase now" and
Cancel). The confirming POST carries a signed payload (:func:`sign`) holding exactly what will be
sent; the confirm view reads it back with :func:`unsign` and sends that, never the form again::

    token = twostage.sign("profile-edit", profile_id, body, base={"revision": 7})
    # render: {% include "console/_two_stage.html" with token=token confirm_url=…
    #          confirm_label="Apply anyway" cancel_url=… idempotency_key=new_ulid() %}
    ...
    try:
        payload = twostage.unsign("profile-edit", request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        messages.error(request, twostage.EXPIRED_TEXT)  # and make zero Engine calls
        return redirect(...)

A payload is ``django.core.signing.dumps({kind, target_id, body, base},
salt="console.twostage.<kind>")`` and lives :data:`MAX_AGE_S` (an hour). A tampered, expired or
other-kind token raises :class:`ConfirmationExpired`. A real offboarding or erasure run is only
reachable through such a payload. The partial carries a fresh idempotency key minted when the
report renders (#43's per-render rule), never the first call's key.
"""

from dataclasses import dataclass
from typing import Any

from django.core import signing

MAX_AGE_S = 3600
SALT_PREFIX = "console.twostage."
CONFIRM_FIELD = "confirm_token"  # the partial's hidden field
EXPIRED_TEXT = "This confirmation expired — submit the form again"


class ConfirmationExpired(Exception):
    """The confirmation token is tampered with, too old, or for another kind of write."""


@dataclass(frozen=True)
class Payload:
    """What the first stage showed and the confirming POST sends: the write's ``kind``, the
    ``target_id`` it acts on, the request ``body`` and ``base`` (what it was computed against:
    a revision, a draft revision, a snapshot; whatever the view needs to send it again)."""

    kind: str
    target_id: str
    body: Any
    base: Any


def _salt(kind: str) -> str:
    return f"{SALT_PREFIX}{kind}"


def sign(kind: str, target_id: str, body: Any, base: Any = None) -> str:
    """The signed confirmation token for one write (JSON-serializable ``body`` and ``base``)."""
    raw = {"kind": kind, "target_id": target_id, "body": body, "base": base}
    return signing.dumps(raw, salt=_salt(kind), compress=True)


def unsign(kind: str, token: str, *, max_age: int = MAX_AGE_S) -> Payload:
    """The payload ``token`` carries, or :class:`ConfirmationExpired`."""
    try:
        raw: Any = signing.loads(token, salt=_salt(kind), max_age=max_age)
    except signing.BadSignature as exc:  # SignatureExpired is a BadSignature
        raise ConfirmationExpired(EXPIRED_TEXT) from exc
    if (
        not isinstance(raw, dict)
        or raw.get("kind") != kind
        or not isinstance(raw.get("target_id"), str)
        or "body" not in raw
    ):
        raise ConfirmationExpired(EXPIRED_TEXT)
    return Payload(kind, raw["target_id"], raw["body"], raw.get("base"))
