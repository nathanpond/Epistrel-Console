"""``POST /acting-as/``: pick the principal every Engine call is made as (#64).

The nav's "Acting as" form posts ``principal`` (the select), ``other`` (free text; a non-blank
value wins over the select) and ``next``; "Back to me" posts ``action=reset``. A valid value is
saved and remembered and the operator returns to ``next``; an invalid one flashes the reason and
changes nothing. Nothing here calls the Engine, and nothing is written to the operation log (it
holds Engine calls only): changes are logged at INFO to ``console.acting``.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from console.acting import reset_acting_principal, set_acting_principal
from console.engine.client import principal_for
from console.forms.principal import clean_acting_principal
from console.operators import is_operator

RESET = "reset"


def _safe(request: HttpRequest, candidate: str) -> bool:
    return bool(candidate) and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )


def back_to(request: HttpRequest) -> str:
    """``next`` when it is a local URL, else a local ``Referer``, else ``/``."""
    for candidate in (request.POST.get("next", ""), request.headers.get("Referer", "")):
        if _safe(request, candidate):
            return candidate
    return "/"


@require_POST
def acting_as(request: HttpRequest) -> HttpResponse:
    if not is_operator(request.user):
        raise PermissionDenied("only a signed-in operator can pick an acting principal")
    destination = back_to(request)
    if request.POST.get("action") == RESET:
        own = reset_acting_principal(request)
        messages.success(request, f"Back to acting as {own}.")
        return HttpResponseRedirect(destination)
    other = request.POST.get("other", "")
    raw = other if other.strip(" \t") else request.POST.get("principal", "")
    try:
        principal = clean_acting_principal(raw)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return HttpResponseRedirect(destination)
    set_acting_principal(request, principal)
    if principal == principal_for(request.user):
        messages.success(request, f"Back to acting as {principal}.")
    else:
        messages.success(request, f"Now acting as {principal}.")
    return HttpResponseRedirect(destination)
