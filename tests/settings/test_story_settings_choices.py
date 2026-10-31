"""C7 (#197): the M5 choice-field helpers every Engine-defaulted select shares."""

from console.story_settings import (
    BOOLEAN_SELECT,
    ENGINE_DEFAULT,
    SETTABLE,
    SETTING_CHOICES,
    engine_default_choice,
    flash_name,
    kept_choices,
    scenario_mode,
    select_bool,
    tie_break_label,
)

MODES = [("sequential", "Sequential"), ("simultaneous", "Simultaneous")]


def test_engine_default_choice_names_the_default_and_sends_nothing() -> None:
    assert ENGINE_DEFAULT == ""
    assert engine_default_choice("request order") == ("", "Engine default (request order)")
    assert engine_default_choice("scenario: simultaneous") == (
        "",
        "Engine default (scenario: simultaneous)",
    )


def test_setting_choices_cover_the_six_and_hold_no_engine_default() -> None:
    assert list(SETTING_CHOICES) == list(SETTABLE[-6:])
    assert SETTING_CHOICES["reactive_interjections"] == BOOLEAN_SELECT
    for name, choices in SETTING_CHOICES.items():
        assert ENGINE_DEFAULT not in {value for value, _ in choices}, name


def test_kept_choices_appends_a_stored_string_the_list_lacks() -> None:
    assert kept_choices(MODES, "round_robin") == [*MODES, ("round_robin", "Round robin")]
    assert kept_choices(MODES, "sequential") == MODES
    assert kept_choices(MODES, "x", label=str.upper) == [*MODES, ("x", "X")]
    # Complement: None or a non-string adds nothing; the list itself is not changed.
    assert kept_choices(MODES, None) == MODES
    assert kept_choices(MODES, True) == MODES
    assert kept_choices(MODES, 3) == MODES
    assert len(MODES) == 2


def test_tie_break_label_matches_the_engines_state_pattern() -> None:
    assert tie_break_label("state:initiative") == "state:initiative (M6: request order applies)"
    assert tie_break_label("state:a_1") == "state:a_1 (M6: request order applies)"
    # Not the Engine's pattern: labelled like any unknown enum.
    assert tie_break_label("state:Initiative") == "State:initiative"
    assert tie_break_label("state:") == "State:"
    assert tie_break_label("coin_flip") == "Coin flip"
    assert tie_break_label("state:" + "a" * 65) == ("State:" + "a" * 65)


def test_select_bool() -> None:
    assert BOOLEAN_SELECT == [("false", "Off"), ("true", "On")]
    assert select_bool("true") is True
    assert select_bool("false") is False
    assert select_bool("") is None
    assert select_bool("True") is None
    assert select_bool("on") is None


def test_scenario_mode_reads_only_the_engines_map() -> None:
    assert scenario_mode({"interaction_mode": {"value": "simultaneous", "capability": "x"}}) == (
        "simultaneous"
    )
    assert scenario_mode({"interaction_mode": {"value": "sequential"}}) == "sequential"
    garbage: object
    for garbage in (None, [], "simultaneous", {}, {"interaction_mode": "simultaneous"}):
        assert scenario_mode(garbage) is None
    assert scenario_mode({"interaction_mode": {"value": "other"}}) is None


def test_flash_name_cuts_the_label() -> None:
    assert flash_name("Player view (where perception is on)") == "Player view"
    assert flash_name("Allow new arrivals — used by structured-pipeline stories") == (
        "Allow new arrivals"
    )
    assert flash_name("Tie-break (simultaneous turns)") == "Tie-break"
    assert flash_name("Queue size") == "Queue size"
