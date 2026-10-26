"""The entity profile's computed parts (#58, :mod:`console.scene`), so ``_entity_profile.html``
renders an :class:`~console.engine.models.EntityProfileBody` on its own: the Scene tab's entity
page includes it, and #152 includes it for retrieval's ``profiles``. The page's ``story_id`` (in
every Inspect context) builds the links."""

from typing import Any

from django import template

from console.engine.models import EntityProfileBody
from console.scene import ProfileView
from console.scene import profile_view as _profile_view

register = template.Library()


@register.simple_tag(takes_context=True)
def entity_profile_view(
    context: template.Context, profile: EntityProfileBody, link_as_of: Any, link_viewpoint: Any
) -> ProfileView:
    """``{% entity_profile_view profile link_as_of link_viewpoint as v %}``: every entity link
    carries ``link_as_of`` (the turn) and ``link_viewpoint``."""
    return _profile_view(
        str(context.get("story_id")),
        profile,
        int(link_as_of),
        str(link_viewpoint),
    )
