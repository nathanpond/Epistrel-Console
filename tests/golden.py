"""E24's one golden normaliser (#296): "byte for byte as on main" in an AC means
``normalise_html(new) == normalise_html(golden)``, compared over the region the AC claims
unchanged (a partial, a link-line slice), captured from main before the change.

It collapses whitespace between tags, and replaces each CSRF token value (the
``csrfmiddlewaretoken`` input and ``base.html``'s ``X-CSRFToken`` header) and each ULID-shaped
idempotency key (an ``idempotency_key`` input's value) with a fixed placeholder, so two renders
that differ only in those compare equal. Everything else, text and attributes, is kept.
"""

import re

CSRF_PLACEHOLDER = "CSRF"
IDEMPOTENCY_PLACEHOLDER = "IDEMPOTENCY-KEY"

_BETWEEN_TAGS = re.compile(r">\s+<")
_INPUT = re.compile(r"<input\b[^>]*>")
_VALUE = re.compile(r'value="[^"]*"')
_ULID_VALUE = re.compile(r'value="[0-9A-HJKMNP-TV-Z]{26}"')
_CSRF_HEADER = re.compile(r'("X-CSRFToken":\s*")[^"]*(")')


def _input(match: re.Match[str]) -> str:
    tag = match.group(0)
    if 'name="csrfmiddlewaretoken"' in tag:
        return _VALUE.sub(f'value="{CSRF_PLACEHOLDER}"', tag)
    if 'name="idempotency_key"' in tag:
        return _ULID_VALUE.sub(f'value="{IDEMPOTENCY_PLACEHOLDER}"', tag)
    return tag


def normalise_html(html: str) -> str:
    """``html`` with inter-tag whitespace collapsed and CSRF tokens and ULID-shaped idempotency
    keys replaced by fixed placeholders."""
    html = _BETWEEN_TAGS.sub("><", html.strip())
    html = _CSRF_HEADER.sub(rf"\g<1>{CSRF_PLACEHOLDER}\g<2>", html)
    return _INPUT.sub(_input, html)
