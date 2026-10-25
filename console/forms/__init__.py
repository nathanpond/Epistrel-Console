"""Console forms, one module per area. Each copies only the bounds the Engine's recorded schema
states, so input they reject makes no Engine call and everything else is left to the Engine's
422 (#43).

- :mod:`console.forms.stories`: the story list filter and the new-story form (#45);
- :mod:`console.forms.model_config`: model purpose configuration and API keys (#46);
- :mod:`console.forms.policy`: guardrail policy content, shared by the deployment's policies
  (#47) and a story's own policies (#54). Import it from its module.

The story and model-config names are re-exported here, so ``from console.forms import …`` keeps
working.
"""

from console.forms.model_config import (
    API_KEY_MAX,
    BASE_URL_MAX,
    MAX_TOKENS_MAX,
    MAX_TOKENS_MIN,
    MODEL_MAX,
    SEED_MAX,
    SEED_MIN,
    STOP_MAX,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    TIMEOUT_MAX,
    TOP_P_MAX,
    TOP_P_MIN,
    ApiKeyForm,
    ModelConfigForm,
)
from console.forms.stories import (
    CAST_MAX,
    CAST_MIN,
    CHARACTER_NAME_MAX,
    CHARACTERS_PREFIX,
    DEFINITION_MAX,
    DEFLECTION_STYLE_MAX,
    SAFE_FALLBACK_MAX,
    STATUS_CHOICES,
    STORY_NAME_MAX,
    TRUE_AGE_MAX,
    TRUE_AGE_MIN,
    VIEWPOINT_MAX,
    BaseCharacterFormSet,
    CharacterForm,
    CharacterFormSet,
    StoryFilterForm,
    StoryForm,
    story_spec,
)

__all__ = [
    "API_KEY_MAX",
    "BASE_URL_MAX",
    "CAST_MAX",
    "CAST_MIN",
    "CHARACTERS_PREFIX",
    "CHARACTER_NAME_MAX",
    "DEFINITION_MAX",
    "DEFLECTION_STYLE_MAX",
    "MAX_TOKENS_MAX",
    "MAX_TOKENS_MIN",
    "MODEL_MAX",
    "SAFE_FALLBACK_MAX",
    "SEED_MAX",
    "SEED_MIN",
    "STATUS_CHOICES",
    "STOP_MAX",
    "STORY_NAME_MAX",
    "TEMPERATURE_MAX",
    "TEMPERATURE_MIN",
    "TIMEOUT_MAX",
    "TOP_P_MAX",
    "TOP_P_MIN",
    "TRUE_AGE_MAX",
    "TRUE_AGE_MIN",
    "VIEWPOINT_MAX",
    "ApiKeyForm",
    "BaseCharacterFormSet",
    "CharacterForm",
    "CharacterFormSet",
    "ModelConfigForm",
    "StoryFilterForm",
    "StoryForm",
    "story_spec",
]
