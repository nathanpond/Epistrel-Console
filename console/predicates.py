"""The predicate registry's rows (#136): what the Predicates tab and the decision page show.

**Predicate names** (#135 convention 2): a page that shows predicate ids makes **one**
``GET /stories/{id}/predicates`` read and names each id from it. :func:`predicate_names` is that
read for the pages that only need names (Canon, Ledger, the fact form, beat traces): it answers
``{}`` when the read fails, so the ids show as short ids and the page still renders.

**Id references.** :class:`Refs` turns predicate ids into :class:`PredicateRef` (rendered by
``console/inspect/_predicate_ref.html``): the predicate's name linked to its row, or a short id.
On the tab (``on_page`` given) a row shown on the page is linked by its fragment (``#p-<id>``),
a row hidden by the layer filter by the unfiltered tab, and an id the registry doesn't hold is
an unlinked short id. Elsewhere (``on_page`` ``None``: the decision page) every id links the
unfiltered tab, ``inspect:predicates?layer=all#p-<id>``.

**Cells** follow the story's Discretion: cardinality "functional", "multi (max N)" or "multi";
qualifiers ``name: type`` per line, ``null`` "undeclared", ``{}`` "none declared"; flags as the
words "symmetric", "set object", "weak"; empty values "—" (convention 5); booleans in their JSON
spelling. A proposal's values are shown the same way (:func:`value_cell`).
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from django.http import HttpRequest
from django.urls import reverse

from console.engine.client import EngineClient, EngineClientError
from console.engine.models import Implication, Predicate, Proposal
from console.engine_errors import log_engine_error
from console.inspect import Tab

LAYERS = ("core", "author", "auto")
ALL = "all"
PENDING = "pending"
EMPTY = "—"
UNDECLARED = "undeclared"
NONE_DECLARED = "none declared"
NO_ATTRIBUTE = object()
# A proposal's fields whose values are predicate ids (rendered as names).
ID_FIELDS = frozenset({"exclusions", "containment_of", "inverse_of"})
FUNCTIONAL = "functional"
MAX_NOTE = "Also sets max cardinality to 1"


def predicate_names(request: HttpRequest, client: EngineClient, story_id: str) -> dict[str, str]:
    """Convention 2's one registry read for a page that names predicate ids: id → name, or
    ``{}`` when the read fails (logged; the page shows short ids)."""
    try:
        return client.list_predicates(story_id).names()
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return {}


def layer_filter(value: object) -> str:
    """The ``layer`` GET parameter: ``core``, ``author`` or ``auto`` exactly, else ``all``."""
    return value if isinstance(value, str) and value in LAYERS else ALL


def tab_url(story_id: str, layer: str = ALL) -> str:
    """The Predicates tab, with ``?layer=`` when filtered."""
    url = reverse(Tab.PREDICATES.url_name, args=[story_id])
    return f"{url}?layer={layer}" if layer != ALL else url


def row_url(story_id: str, predicate_id: str) -> str:
    """The predicate's row on the unfiltered tab: ``…/predicates/?layer=all#p-<id>``."""
    return f"{reverse(Tab.PREDICATES.url_name, args=[story_id])}?layer={ALL}#p-{predicate_id}"


# --- id references -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PredicateRef:
    """A predicate id as shown: ``name`` when known, linked to ``url`` when it has one."""

    id: str
    name: str | None
    url: str | None


@dataclass(frozen=True)
class Refs:
    """Builds :class:`PredicateRef` from ``names`` (id → name; see the module docstring)."""

    story_id: str
    names: Mapping[str, str]
    on_page: Collection[str] | None = None

    def ref(self, predicate_id: str) -> PredicateRef:
        name = self.names.get(predicate_id)
        if self.on_page is None:
            return PredicateRef(predicate_id, name, row_url(self.story_id, predicate_id))
        if name is None:
            return PredicateRef(predicate_id, None, None)
        if predicate_id in self.on_page:
            return PredicateRef(predicate_id, name, f"#p-{predicate_id}")
        return PredicateRef(predicate_id, name, row_url(self.story_id, predicate_id))

    def refs(self, value: object) -> tuple[PredicateRef, ...]:
        """A single id, a list of ids or ``None`` (``containment_of`` vs ``exclusions``); entries
        that aren't strings are left out."""
        if isinstance(value, str):
            return (self.ref(value),)
        if isinstance(value, list):
            return tuple(self.ref(v) for v in value if isinstance(v, str))
        return ()


@dataclass(frozen=True)
class RuleRow:
    """An implication: ``when ⇒ then`` (``via`` when it maps the object through a join)."""

    when: PredicateRef
    then: PredicateRef
    via: PredicateRef | None


def rule_row(rule: Implication, refs: Refs) -> RuleRow:
    return RuleRow(
        refs.ref(rule.when),
        refs.ref(rule.then),
        refs.ref(rule.via) if rule.via else None,
    )


# --- values --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueCell:
    """A shown value: ``refs`` (predicate ids), else ``lines`` (one per line), else ``text``."""

    text: str = EMPTY
    lines: tuple[str, ...] = ()
    refs: tuple[PredicateRef, ...] = ()


def scalar(value: object) -> str:
    """A scalar in the JSON spelling (``true``, ``false``); ``None`` and "" are "—"."""
    if value is None or value == "":
        return EMPTY
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def joined(values: Sequence[object]) -> str:
    """A list comma-joined, "—" when empty."""
    return ", ".join(scalar(v) for v in values) if values else EMPTY


def qualifier_cell(value: object) -> ValueCell:
    """Declared qualifiers: ``name: type`` per line; ``None`` "undeclared"; ``{}`` "none
    declared"."""
    if value is None:
        return ValueCell(UNDECLARED)
    if isinstance(value, dict):
        if not value:
            return ValueCell(NONE_DECLARED)
        return ValueCell(lines=tuple(f"{k}: {scalar(v)}" for k, v in value.items()))
    return ValueCell(scalar(value))


def value_cell(field: str, value: object, refs: Refs) -> ValueCell:
    """A proposal's (or an applied) value of ``field``: predicate ids as names, ``qualifiers`` as
    :func:`qualifier_cell`, other dicts as ``k: v`` lines, lists comma-joined, scalars verbatim.
    :data:`NO_ATTRIBUTE` (a field the predicate has no attribute for) is "—"."""
    if value is NO_ATTRIBUTE:
        return ValueCell()
    if field in ID_FIELDS:
        found = refs.refs(value)
        return ValueCell(refs=found) if found else ValueCell()
    if field == "qualifiers":
        return qualifier_cell(value)
    if isinstance(value, dict):
        if not value:
            return ValueCell()
        return ValueCell(lines=tuple(f"{k}: {scalar(v)}" for k, v in value.items()))
    if isinstance(value, list):
        return ValueCell(joined(value))
    return ValueCell(scalar(value))


def cardinality_text(predicate: Predicate) -> str:
    """ "functional", "multi (max N)", or "multi" when ``max_cardinality`` is null (another
    value verbatim)."""
    if predicate.cardinality == "multi" and predicate.max_cardinality is not None:
        return f"multi (max {predicate.max_cardinality})"
    return predicate.cardinality


def flags_text(predicate: Predicate) -> str:
    """ "symmetric, set object, weak" (those that are set), "—" when none."""
    flags = [
        word
        for word, on in (
            ("symmetric", predicate.symmetric),
            ("set object", predicate.set_object),
            ("weak", predicate.weak),
        )
        if on
    ]
    return ", ".join(flags) if flags else EMPTY


# --- rows ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalField:
    """One proposed change: the field, the predicate's current value and the proposed one."""

    name: str
    current: ValueCell
    proposed: ValueCell


@dataclass(frozen=True)
class ProposalView:
    """A pending proposal's sub-row: its fields in the proposal's order, the max-cardinality
    note, and whether **Approve selected** is offered (two or more fields)."""

    proposal: Proposal
    fields: tuple[ProposalField, ...]
    max_note: bool

    @property
    def offered(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def selectable(self) -> bool:
        return len(self.fields) >= 2


def proposal_view(predicate: Predicate, refs: Refs) -> ProposalView | None:
    """The sub-row of a ``pending`` proposal; ``None`` for no proposal or another status. The
    current value is the predicate's same-named attribute ("—" when it has none)."""
    proposal = predicate.pending_proposal
    if proposal is None or proposal.status != PENDING:
        return None
    fields = tuple(
        ProposalField(
            name,
            value_cell(name, getattr(predicate, name, NO_ATTRIBUTE), refs),
            value_cell(name, proposed, refs),
        )
        for name, proposed in proposal.changes.items()
    )
    return ProposalView(proposal, fields, proposal.changes.get("cardinality") == FUNCTIONAL)


@dataclass(frozen=True)
class PredicateRow:
    """One registry row (``_predicate_row.html``)."""

    predicate: Predicate
    cardinality: str
    key_qualifiers: str
    qualifiers: ValueCell
    flags: str
    inverse_of: tuple[PredicateRef, ...]
    containment_of: tuple[PredicateRef, ...]
    exclusions: tuple[PredicateRef, ...]
    implications: tuple[RuleRow, ...]
    proposal: ProposalView | None
    more: dict[str, Any]

    @property
    def layer_class(self) -> str:
        """The badge's class: ``layer-<layer>`` for a known layer, else the neutral badge."""
        layer = self.predicate.layer
        return f"layer-{layer}" if layer in LAYERS else "layer-other"


def predicate_row(predicate: Predicate, refs: Refs) -> PredicateRow:
    return PredicateRow(
        predicate=predicate,
        cardinality=cardinality_text(predicate),
        key_qualifiers=joined(predicate.key_qualifiers),
        qualifiers=qualifier_cell(predicate.qualifiers),
        flags=flags_text(predicate),
        inverse_of=refs.refs(predicate.inverse_of),
        containment_of=refs.refs(predicate.containment_of),
        exclusions=refs.refs(predicate.exclusions),
        implications=tuple(rule_row(i, refs) for i in predicate.implications),
        proposal=proposal_view(predicate, refs),
        more=asdict(predicate),  # convention 13: the id, story id and full proposal
    )
