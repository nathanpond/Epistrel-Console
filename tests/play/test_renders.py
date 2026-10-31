"""The turn inspector's Renders section (#201): ``console.renders.turn_renders`` over hand-built,
Engine-shaped beats (C15) — the Input group, one group per rendering character in ``order`` with
reactions above or under the character that narrated them, and Other beats. A player-narrated
beat (C16) is never a reaction."""

from typing import Any

from console.engine.models import HistoryItem
from console.inspect import short_id
from console.renders import NO_BEATS, RenderBeat, RenderGroup, turn_renders

PAT = "01JB2Z8Y5Q3W6K9M1N4P7R0T3A"  # the player
MARCUS = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
JENNI = "01JB2Z8Y5Q3W6K9M1N4P7R0T3C"
OUTSIDER = "01JB2Z8Y5Q3W6K9M1N4P7R0T3Z"
LABELS = {PAT: "Pat (player)", MARCUS: "Marcus", JENNI: "Jenni"}
OP = "01JB2Z8Y5Q3W6K9M1N4P7S0001"


def beat(
    number: int | None,
    actor: str | None,
    text: str,
    *,
    channel: str = "say",
    narrated_by: str | None = None,
    kind: str = "beat_say",
    **extra: Any,
) -> HistoryItem:
    """One respond beat as the Engine returns it (``HistoryItemModel``)."""
    raw: dict[str, Any] = {
        "turn": 3,
        "variant": 1,
        "beat": number,
        "kind": kind,
        "actor_character_id": actor,
        "render_character_ids": None,
        "channel": channel,
        "text": text,
        "operation_id": OP,
        "message_id": f"01JB2Z8Y5Q3W6K9M1N4P7M{number or 0:04d}",
        "origin": "generated",
        "propositions": [],
        "fidelity": "content",
        **extra,
    }
    if narrated_by is not None:
        raw["narrated_by"] = narrated_by
    item = HistoryItem.from_json(raw)
    assert item is not None
    return item


def reply(text: str = "Marcus and Jenni answer.") -> HistoryItem:
    return beat(
        None,
        None,
        text,
        channel="message",
        kind="message_rendered",
        render_character_ids=[MARCUS, JENNI],
        propositions=None,
        fidelity=None,
    )


def texts(beats: tuple[RenderBeat, ...]) -> list[str]:
    return [b.item.text for b in beats]


def headings(groups: tuple[RenderGroup, ...]) -> list[str]:
    return [g.heading for g in groups]


def test_groups_follow_order_with_reactions_under_narrator() -> None:
    entries = [
        beat(1, PAT, "Where is the ledger?"),
        beat(2, MARCUS, "Marcus shrugs.", channel="do"),
        beat(3, JENNI, "Jenni flinches.", channel="do", narrated_by=MARCUS),
        beat(4, MARCUS, "Ask Jenni."),
        beat(5, JENNI, "Not me."),
        reply(),
    ]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS, PAT)
    assert headings(groups) == ["Input — Pat (player)", "Marcus", "Jenni", "Other beats"]
    assert [g.anchor for g in groups] == ["render-input", "render-1", "render-2", "render-other"]
    marcus = groups[1]
    assert marcus.leading == ()
    assert texts(marcus.beats) == ["Marcus shrugs.", "Ask Jenni."]
    (reaction,) = marcus.beats[0].reactions
    assert reaction.item.text == "Jenni flinches."
    assert reaction.label == "Jenni"
    assert reaction.reaction
    assert reaction.css_class == "reaction"
    assert reaction.reactions == ()
    assert marcus.beats[1].reactions == ()
    assert [b.label for b in marcus.beats] == ["", ""]
    jenni = groups[2]
    assert texts(jenni.beats) == ["Not me."]  # the reaction is Marcus's, not in Jenni's group
    other = groups[3]
    assert texts(other.beats) == ["Marcus and Jenni answer."]
    assert other.beats[0].label == "Marcus, Jenni"  # a reply's render characters
    assert other.beats[0].position == 6  # "item 6": no beat number


def test_reaction_before_narrators_first_beat_leads_the_group() -> None:
    entries = [
        beat(1, JENNI, "Jenni gasps.", channel="do", narrated_by=MARCUS),
        beat(2, MARCUS, "Marcus turns."),
        beat(3, JENNI, "Jenni.", narrated_by=MARCUS),
    ]
    marcus = turn_renders(entries, [MARCUS], LABELS)[0]
    assert texts(marcus.leading) == ["Jenni gasps."]
    assert marcus.leading[0].label == "Jenni"
    assert texts(marcus.beats) == ["Marcus turns."]
    assert texts(marcus.beats[0].reactions) == ["Jenni."]


def test_reaction_with_narrator_outside_order_goes_to_other() -> None:
    entries = [
        beat(1, MARCUS, "Marcus speaks."),
        beat(2, JENNI, "Jenni sighs.", channel="do", narrated_by=OUTSIDER),
    ]
    groups = turn_renders(entries, [MARCUS], LABELS)
    assert headings(groups) == ["Marcus", "Other beats"]
    (other,) = groups[1].beats
    assert other.item.text == "Jenni sighs."
    assert other.reaction
    assert other.label == "Jenni"
    assert "reaction" in other.css_class


def test_input_and_other_beats_groups() -> None:
    entries = [
        beat(1, PAT, "Hello?"),
        beat(2, None, "Rain on the roof.", channel="narration", kind="beat_narration"),
        beat(3, MARCUS, "Marcus nods.", channel="do"),
        beat(4, MARCUS, "Evening."),
        beat(5, JENNI, "Jenni waves.", channel="do"),
        beat(6, PAT, "Pat sits down.", channel="do"),  # after the cut-off: Other
    ]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS, PAT)
    assert headings(groups) == ["Input — Pat (player)", "Marcus", "Jenni", "Other beats"]
    assert texts(groups[0].beats) == ["Hello?"]
    assert groups[0].beats[0].label == ""
    other = groups[3]
    assert texts(other.beats) == ["Rain on the roof.", "Pat sits down."]
    assert [b.label for b in other.beats] == ["", "Pat (player)"]


def test_input_takes_every_actor_beat_when_no_order_beat_exists() -> None:
    entries = [beat(1, PAT, "One."), beat(2, PAT, "Two.")]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS, PAT)
    assert headings(groups) == ["Input — Pat (player)", "Marcus", "Jenni"]
    assert texts(groups[0].beats) == ["One.", "Two."]


def test_no_actor_means_no_input_group() -> None:
    """A regeneration sends no actor, so its beats go to Other beats."""
    entries = [beat(1, PAT, "Hello?"), beat(2, MARCUS, "Hi."), beat(3, JENNI, "Hey.")]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS, "")
    assert headings(groups) == ["Marcus", "Jenni", "Other beats"]


def test_actor_in_order_has_no_input_group() -> None:
    entries = [beat(1, MARCUS, "I start."), beat(2, JENNI, "I follow.")]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS, MARCUS)
    assert headings(groups) == ["Marcus", "Jenni"]
    assert texts(groups[0].beats) == ["I start."]


def test_primary_without_beats_says_so() -> None:
    entries = [beat(1, MARCUS, "Only me.")]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS)
    jenni = groups[1]
    assert jenni.heading == "Jenni"
    assert jenni.leading == jenni.beats == ()
    assert jenni.empty_note == NO_BEATS == "No beats (see warnings)"
    assert groups[0].empty_note == ""


def test_group_with_only_reactions_has_no_note() -> None:
    entries = [
        beat(1, JENNI, "Jenni winces.", channel="do", narrated_by=MARCUS),
        beat(2, JENNI, "Jenni talks."),
    ]
    marcus = turn_renders(entries, [MARCUS, JENNI], LABELS)[0]
    assert texts(marcus.leading) == ["Jenni winces."]
    assert marcus.beats == ()
    assert marcus.empty_note == ""


def test_beats_keep_engine_order_within_a_group() -> None:
    entries = [
        beat(4, MARCUS, "Fourth, first in beats[]."),
        beat(2, MARCUS, "Second."),
        beat(None, MARCUS, "Unnumbered."),
        beat(1, JENNI, "Jenni."),
    ]
    marcus = turn_renders(entries, [MARCUS, JENNI], LABELS)[0]
    assert texts(marcus.beats) == ["Fourth, first in beats[].", "Second.", "Unnumbered."]
    assert marcus.beats[2].position == 3
    assert marcus.beats[2].anchor == ""


def test_shared_beat_number_anchors_once() -> None:
    entries = [
        beat(2, MARCUS, "Say part.", message_id="01JB2Z8Y5Q3W6K9M1N4P7M0SHR"),
        beat(2, MARCUS, "Do part.", channel="do", message_id="01JB2Z8Y5Q3W6K9M1N4P7M0SHR"),
        beat(3, JENNI, "Reacts.", narrated_by=MARCUS),
        beat(3, JENNI, "Own beat."),
    ]
    groups = turn_renders(entries, [MARCUS, JENNI], LABELS)
    marcus, jenni = groups
    assert [b.anchor for b in marcus.beats] == ["beat-2", ""]
    assert marcus.beats[1].reactions[0].anchor == "beat-3"  # the first item with beat 3
    assert jenni.beats[0].anchor == ""


def test_repeated_order_id_is_one_group() -> None:
    entries = [beat(1, MARCUS, "Once."), beat(2, JENNI, "Twice.")]
    groups = turn_renders(entries, [MARCUS, JENNI, MARCUS], LABELS)
    assert headings(groups) == ["Marcus", "Jenni"]
    assert [g.anchor for g in groups] == ["render-1", "render-2"]


def test_label_outside_the_cast_is_the_short_id() -> None:
    entries = [beat(1, OUTSIDER, "Who?"), beat(2, MARCUS, "Me.")]
    groups = turn_renders(entries, [OUTSIDER, MARCUS], {})
    assert groups[0].heading == short_id(OUTSIDER)
    assert groups[1].heading == short_id(MARCUS)


def test_player_narrated_beat_is_not_a_reaction() -> None:
    """C16 **complement**: ``narrated_by == "player"`` stays in its character's group, labelled
    "<character> (narrated by the player)", with no ``reaction`` class; on its own it doesn't
    show the section."""
    entries = [beat(1, MARCUS, "The ledger is gone.", narrated_by="player")]
    assert turn_renders(entries, [MARCUS], LABELS, PAT) == ()
    groups = turn_renders([*entries, beat(2, JENNI, "Gone?")], [MARCUS, JENNI], LABELS, PAT)
    (own,) = groups[0].beats
    assert own.label == "Marcus (narrated by the player)"
    assert not own.reaction
    assert own.css_class == ""
    assert own.reactions == ()


def test_single_primary_without_reactions_shows_no_section() -> None:
    entries = [beat(1, PAT, "Hi."), beat(2, MARCUS, "Hello.")]
    assert turn_renders(entries, [MARCUS], LABELS, PAT) == ()
    assert turn_renders(entries, [], LABELS, PAT) == ()


def test_a_reaction_alone_shows_the_section() -> None:
    entries = [beat(1, MARCUS, "Hm."), beat(2, JENNI, "Oh.", narrated_by=MARCUS)]
    groups = turn_renders(entries, [MARCUS], LABELS)
    assert headings(groups) == ["Marcus"]


def test_fidelity_class_beside_reaction() -> None:
    entries = [
        beat(1, MARCUS, "Marcus mutters."),
        beat(2, JENNI, "Jenni whispers.", narrated_by=MARCUS, fidelity="envelope", propositions=[]),
    ]
    (reaction,) = turn_renders(entries, [MARCUS], LABELS)[0].beats[0].reactions
    assert reaction.css_class == "reaction fidelity-envelope"
    assert reaction.fidelity is not None
    assert reaction.fidelity.text == "envelope"
