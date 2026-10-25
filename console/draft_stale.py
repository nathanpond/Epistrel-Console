"""The 409 ``draft_stale`` banner (#64 convention 2; built by #66 for catalog characters, reused
by the scenario editors).

A save or publish names the ``draft_revision`` it is based on; when another save or publish came
first the Engine answers 409 ``draft_stale`` with ``details.current_draft_revision``. A character's
publish with its companion names both drafts, and its refusal says which side is stale
(``details.entity``: ``character`` or ``companion``); a companion that changed while the publish
read it has no current revision. The edit page keeps the operator's input under the banner.
"""

from console.engine.client import EngineError

DRAFT_STALE = "draft_stale"
COMPANION = "companion"
CHARACTER = "character"


def stale_banner(
    error: EngineError, *, draft_revision: int | None, companion_draft_revision: int | None = None
) -> str:
    """The banner for a 409 ``draft_stale``, naming which draft is stale: ``details.entity``
    when the Engine says, else the sent revision that differs from
    ``details.current_draft_revision``."""
    details = error.details
    current = details.get("current_draft_revision")
    entity = details.get("entity")
    if entity not in (CHARACTER, COMPANION):
        companion_stale = (
            companion_draft_revision is not None
            and current == draft_revision
            and current != companion_draft_revision
        )
        entity = COMPANION if companion_stale else CHARACTER
    if entity == COMPANION:
        if not isinstance(current, int):
            return (
                "The companion changed while this was being published — reload the page and "
                "retry; nothing was published."
            )
        return (
            f"The companion's draft was saved elsewhere (now revision {current}) — reload the "
            "page to publish its latest revision; nothing was published."
        )
    now = f" (now revision {current})" if isinstance(current, int) else ""
    return (
        f"This draft was saved elsewhere{now} — reload the page to continue from the latest; "
        "nothing was changed."
    )
