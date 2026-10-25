"""``{% load principals %}``: every displayed principal renders through ``principal_link``
(#64, M3 convention 7).

``{{ value|principal_link }}`` links the principal to its page, ``/principals/<p>/``
(``principals:detail`` with ``quote(p, safe='')``; #76 builds the page). While that URL does not
resolve, the principal renders as ``<code>p</code>``, unlinked. Blank renders "—".
"""

from urllib.parse import quote

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()

DETAIL_URL_NAME = "principals:detail"


def principal_href(principal: str) -> str | None:
    """The principal's Console page, or ``None`` while ``principals:detail`` does not resolve.

    The argument is ``quote(principal, safe='')``; Django's ``reverse`` percent-encodes it once
    more, so the view receives the quoted form and unquotes it (#76)."""
    try:
        return reverse(DETAIL_URL_NAME, args=[quote(principal, safe="")])
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
