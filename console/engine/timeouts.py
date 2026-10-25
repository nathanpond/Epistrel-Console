"""Engine call timeouts beyond #43's (#64, M3 convention 18), in seconds.

#43's own stay in ``console.engine.client``: reads ``DEFAULT_TIMEOUT_S`` (3), ordinary
mutations ``MUTATION_TIMEOUT_S`` (10), fork and rebuild ``LONG_MUTATION_TIMEOUT_S`` (30), turns
``turn_timeout(deadline)``. Story writes the Engine holds behind a running turn (a rating PUT,
reactivating edits) use ``turn_timeout`` too.

Console-side fan-out reads (convention 12: N+1 lookups such as ``story_labels``) run on
:data:`LOOKUP_WORKERS` threads under one shared :data:`LOOKUP_DEADLINE_S`.
"""

CATALOG_PUBLISH = 30.0  # publishing a catalog character or scenario
SCENARIO_CREATE = 30.0  # creating a story from a scenario
ERASURE = 60.0  # an erasure (or its dry run)
RECONCILE = 60.0  # re-applying the erasure ledger now (#87): it answers when the run has ended
UPLOAD = 60.0  # a media upload (each part, and completing it)
OFFBOARDING = 30.0  # an offboarding: its dry run and its start (#77)
STORY_CHARACTER_ADD = 30.0  # adding a character to a story (#72: input-gate model calls)
PROFILE_EDIT = 30.0  # an engine profile PUT: its impact over up to 1,000 stories (#84)
RATING_SUGGESTION_PER_RATING = 20.0
RATING_SUGGESTION_BASE = 10.0

LOOKUP_WORKERS = 8
LOOKUP_DEADLINE_S = 10.0


def rating_suggestion(ratings: int) -> float:
    """A rating suggestion's timeout: 20 s per content rating it weighs, plus 10 s."""
    return RATING_SUGGESTION_PER_RATING * max(ratings, 0) + RATING_SUGGESTION_BASE
