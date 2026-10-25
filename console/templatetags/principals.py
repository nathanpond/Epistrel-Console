"""``{% load principals %}``: every displayed principal renders through ``principal_link``
(#64, M3 convention 7).

``{{ value|principal_link }}`` links the principal to its page, ``/principals/<p>/``, built by
#76's :func:`console.views.principals.principal_url` (``system`` is linked too). While that URL
does not resolve, the principal renders as ``<code>p</code>``, unlinked. Blank renders "—".
"""

from django import template
from django.urls import NoReverseMatch
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()


def principal_href(principal: str) -> str | None:
    """The principal's Console page, or ``None`` while ``principals:detail`` does not resolve.

    :func:`~console.views.principals.principal_url` reverses ``quote(principal, safe='')``;
    Django's ``reverse`` percent-encodes it once more, so the view receives the quoted form and
    unquotes it."""
    # Imported lazily, to keep the template-tag module free of an import cycle with the views.
    from console.views.principals import principal_url

    try:
        return principal_url(principal)
    except NoReverseMatch:
        return None


@register.filter
def principal_link(principal: str | None) -> SafeString:
    if not principal:
        return format_html("{}", "—")
    href = principal_href(principal)
    if href is None:
        return format_html("<code>{}</code>", principal)
    return format_html('<a href="{}"><code>{}</code></a>', href, principal)
