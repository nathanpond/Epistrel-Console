"""The operator's acting principal (#64): who the Engine is told every call is made for.

``acting_principal(request)`` is the operator's stored :class:`~console.models.ActingPrincipal`
when they picked someone else, else ``principal_for(request.user)`` (``console:<username>``). It
is read once per request (memoized on the request) and used by ``engine_client_for`` and by the
``acting`` context processor that feeds the nav selector and the banner.

``set_acting_principal`` / ``reset_acting_principal`` change it (the ``/acting-as/`` view). A
row exists only while acting as someone else.
"""

import logging
from typing import Any

from django.http import HttpRequest

from console.engine.client import principal_for
from console.forms.principal import remember_principal
from console.models import ActingPrincipal

log = logging.getLogger("console.acting")

MEMO_ATTR = "_console_acting_principal"


def acting_principal(request: HttpRequest) -> str:
    """The principal ``request.user`` acts as. One query per request (none for a user that is
    not saved); raises ``ValueError`` for an anonymous user, like ``principal_for``."""
    memo: str | None = getattr(request, MEMO_ATTR, None)
    if memo is not None:
        return memo
    user = request.user
    value = principal_for(user)
    pk: int | None = getattr(user, "pk", None)
    if pk is not None:
        stored = (
            ActingPrincipal.objects.filter(user_id=pk).values_list("principal", flat=True)
        ).first()
        if stored:
            value = stored
    setattr(request, MEMO_ATTR, value)
    return value


def is_acting_as_self(request: HttpRequest) -> bool:
    return acting_principal(request) == principal_for(request.user)


def set_acting_principal(request: HttpRequest, principal: str) -> None:
    """Act as ``principal`` (already cleaned by ``clean_acting_principal``) from now on.

    Selecting yourself deletes the row (and adds no ``KnownPrincipal``); anyone else is saved
    and remembered as a known principal (its ``last_used_at`` bumped). Logged at INFO.
    """
    user: Any = request.user
    old = acting_principal(request)
    own = principal_for(user)
    if principal == own:
        ActingPrincipal.objects.filter(user_id=user.pk).delete()
    else:
        ActingPrincipal.objects.update_or_create(user=user, defaults={"principal": principal})
        remember_principal(principal)
    setattr(request, MEMO_ATTR, principal)
    log.info(
        "acting principal changed: operator=%s old=%s new=%s", user.get_username(), old, principal
    )


def reset_acting_principal(request: HttpRequest) -> str:
    """ "Back to me": act as ``console:<username>`` again. Returns that principal."""
    own = principal_for(request.user)
    set_acting_principal(request, own)
    return own
