"""Adding a rating-scoped policy to its rating (#49's second write after #47's create).

Creating a ``rating``-scoped policy does not make the rating apply it: the rating's own
``policy_ids`` must name it. :func:`attach_policy` reads the rating and, unless it already
names the policy, writes it back with the policy appended (a ``PUT``, recorded by #44 as its
own operation). The key is derived from the create's key (``<key>.rating``), so a replayed
create replays this write too.
"""

from console.engine.client import EngineClient
from console.engine.models import ContentRatingMutation
from console.forms.ratings import replacement_of

ATTACH_KEY_SUFFIX = ".rating"


def attach_key(create_key: str) -> str:
    """The second write's idempotency key, derived from the create's."""
    return f"{create_key}{ATTACH_KEY_SUFFIX}"


def attach_policy(
    client: EngineClient, rating_id: str, policy_id: str, *, idempotency_key: str
) -> ContentRatingMutation | None:
    """Add ``policy_id`` to the rating's ``policy_ids``; ``None`` when it is already there
    (nothing written). Engine errors propagate to the caller."""
    rating = client.get_content_rating(rating_id)
    if policy_id in rating.policy_ids:
        return None
    body = replacement_of(rating, policy_ids=[*rating.policy_ids, policy_id])
    return client.update_content_rating(rating_id, body, idempotency_key=idempotency_key)
