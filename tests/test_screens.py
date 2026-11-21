"""Whether a Claude Code screen waits for the operator, on screens recorded from Claude Code (#79).

These are what breaks when Claude Code changes how it draws a prompt or its input area. See
tests/fixtures/README.md for where each screen comes from.
"""
from pathlib import Path

import pytest

from autoflow_ide.screens import INPUT_RE, PROMPT_LINES, input_area, waiting

SCREENS = Path(__file__).parent / "fixtures" / "screens"
PROMPTS = ["choice-prompt.txt", "choice-prompt-80.txt", "permission-prompt.txt"]
INPUT_AREAS = ["working.txt", "turn-end.txt", "background-agent.txt"]
PATTERNS = ["Do you want to proceed?", "Enter to select · ↑/↓ to navigate", "Esc to cancel · Tab to amend",
            "❯ 1. Yes", "Would you like to keep these settings?"]
NBSP = "\xa0"


def screen(name):
    return (SCREENS / name).read_text()


def above_input_area(text, lines):
    """`text` with `lines` put just above its input area's upper rule (#79)."""
    rows = text.rstrip().splitlines()
    upper = [n for n, row in enumerate(rows) if row.startswith("───")][-2]
    return "\n".join(rows[:upper] + lines + rows[upper:]) + "\n"


@pytest.mark.parametrize("name", PROMPTS)
def test_a_recorded_choice_or_permission_prompt_waits(name):
    assert waiting(screen(name))
    assert not input_area(screen(name))        # the dialog takes the place of the input area


@pytest.mark.parametrize("name", INPUT_AREAS)
def test_a_recorded_working_screen_or_empty_input_area_does_not_wait(name):
    assert input_area(screen(name))
    assert not waiting(screen(name))


def test_the_recorded_input_area_holds_the_prompt_and_a_no_break_space():
    # As recorded: the empty input area is `❯` and a no-break space; the choice prompt's cursor is `❯` and a space.
    for name in INPUT_AREAS:
        assert f"\n❯{NBSP}\n" in screen(name)
    assert f"❯{NBSP}" not in screen("choice-prompt.txt") and "❯ 1. analysis-review" in screen("choice-prompt.txt")


def test_the_recorded_working_screen_quotes_every_pattern_above_the_bottom():
    # The tool output quotes the patterns' words more than PROMPT_LINES lines above the bottom.
    text = screen("working.txt")
    assert INPUT_RE.search(text) and not INPUT_RE.search("\n".join(text.rstrip().splitlines()[-PROMPT_LINES:]))


@pytest.mark.parametrize("name", INPUT_AREAS)
@pytest.mark.parametrize("words", PATTERNS)
def test_words_of_a_prompt_in_a_report_above_the_input_area_do_not_wait(name, words):
    assert not waiting(above_input_area(screen(name), ["", f"  {words}", ""]))


@pytest.mark.parametrize("words", PATTERNS)
def test_words_of_a_prompt_with_a_draft_in_the_input_area_do_not_wait(words):
    text = above_input_area(screen("turn-end.txt"), [f"  {words}"]).replace(f"\n❯{NBSP}\n", f"\n❯{NBSP}my draft\n")
    assert input_area(text) and not waiting(text)


@pytest.mark.parametrize("words", PATTERNS)
def test_each_pattern_alone_at_the_bottom_of_a_screen_without_an_input_area_waits(words):
    assert waiting(f"some output\n\n{words}\n")


@pytest.mark.parametrize("name", PROMPTS + INPUT_AREAS)
def test_blank_lines_below_the_content_do_not_count(name):
    text = screen(name).rstrip()
    assert waiting(text) == waiting(text + "\n" * 30) == waiting(screen(name))


def test_only_the_last_twelve_lines_are_read():
    lines = [f"line {n}" for n in range(PROMPT_LINES - 1)]
    assert waiting("\n".join(["Do you want to proceed?"] + lines))
    assert not waiting("\n".join(["Do you want to proceed?"] + lines + ["one more"]))
    # Blank lines inside the screen count.
    assert not waiting("\n".join(["Do you want to proceed?"] + [""] * PROMPT_LINES + ["end"]))


def test_a_dialog_between_the_last_two_rules_is_not_the_input_area():
    rule = "─" * 20
    assert waiting(f"{rule}\n ❯ 1. Yes\n   2. No\n{rule}\n Esc to cancel\n")
    assert not waiting(f"{rule}\n❯{NBSP}\n{rule}\n  status\n")


def test_a_recorded_prompt_scrolled_up_above_an_input_area_does_not_wait():
    # The choice prompt answered: its words are above the bottom and an input area is below (#79).
    rule = "─" * 20
    text = screen("choice-prompt.txt").rstrip() + "\n" + "\n" * PROMPT_LINES + f"{rule}\n❯{NBSP}\n{rule}\n  status\n"
    assert not waiting(text)


# Whether a screen takes a message, copied from the coordinator's tools/terminals (input_held, submit_refused, (#86)
# input_holds, MAX_SUBMIT_BYTES), so a message from the browser is entered only where the coordinator's submit (#86)
# would enter it (#86). Imported in each test: the rest of the file runs without them.

def test_only_an_empty_input_area_takes_a_message():
    from autoflow_ide.screens import input_held
    for name in INPUT_AREAS:                   # empty input areas, at work, at turn end, waiting for an agent (#86)
        assert input_held(screen(name)) is None
    assert input_held(screen("permission-prompt.txt")) == "no input area on the screen"
    for name in ("choice-prompt.txt", "choice-prompt-80.txt"):      # the dialog between the last two rules (#86)
        assert input_held(screen(name)).startswith("the input area is not empty: '☐ Workflow'")
    draft = screen("turn-end.txt").replace(f"\n❯{NBSP}\n", f"\n❯{NBSP}my draft\n")
    assert input_held(draft).startswith("the input area is not empty: '❯")
    assert input_held("some output\n") == "no input area on the screen"


def test_a_message_over_700_bytes_is_refused():
    from autoflow_ide.screens import MAX_SUBMIT_BYTES, submit_refused
    assert MAX_SUBMIT_BYTES == 700
    assert submit_refused(screen("turn-end.txt"), "x" * 700) is None
    assert submit_refused(screen("turn-end.txt"), "x" * 701) == "701 bytes is over 700: put it in a file"
    assert submit_refused(screen("turn-end.txt"), "가" * 234) == "702 bytes is over 700: put it in a file"   # bytes (#86)
    assert submit_refused(screen("permission-prompt.txt"), "hi") == "no input area on the screen"


def test_the_input_area_holds_the_text_entered():
    from autoflow_ide.screens import input_holds
    entered = screen("turn-end.txt").replace(f"\n❯{NBSP}\n", f"\n❯{NBSP}look at the second step\n")
    assert input_holds(entered, "look at the second step")
    assert input_holds(entered, "look at\nthe second   step")          # whitespace does not count (#86)
    assert not input_holds(screen("turn-end.txt"), "look at the second step")
    assert not input_holds(entered, "something else")
    assert not input_holds("no rules here\n", "no rules here")
