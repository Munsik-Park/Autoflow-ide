"""Whether a Claude Code screen waits for the operator's answer (#79).

The coordinator judges its work sessions so in tools/status.py: a prompt pattern in the last 12 lines of the
screen, looked for only while the session's turn has not ended. The IDE has no turn state for the coordination
session (it is not among status.py's sessions), so it uses what both kinds of prompt look like instead: the
dialog replaces Claude Code's input area, while a screen at turn end or at work shows that area, and may show
the same words above it in a report or a tool's output.
"""
import re

# A choice or permission prompt on a Claude Code screen, and a rule above or below its input area: copied from
# the coordinator's tools/terminals (INPUT_RE, INPUT_RULE_RE) of the version the IDE supports
# (SUPPORTED_COORDINATOR in runs.py), as the IDE uses the coordinator through its tools, not its code (#79).
INPUT_RE = re.compile(r'Do you want to|Enter to select|Esc to cancel|❯ \d+\.|Would you like to')
INPUT_RULE_RE = re.compile(r'^─{3,}.*$', re.M)
# The lines at the bottom of a screen a prompt is looked for in, as the coordinator's status.py reads them (#79).
PROMPT_LINES = 12
# The input area's prompt; a dialog's cursor (`❯ 1.`) starts the same way but matches INPUT_RE (#79).
INPUT_PROMPT = '❯'


def input_area(screen: str) -> bool:
    """Whether Claude Code's input area is on the screen: between its last two rules, a first line that starts
    with the prompt and is not a dialog's (#79). A draft counts as the input area too."""
    rules = list(INPUT_RULE_RE.finditer(screen))
    if len(rules) < 2:
        return False
    area = screen[rules[-2].end():rules[-1].start()].strip()
    first = area.splitlines()[0] if area else ''
    return first.startswith(INPUT_PROMPT) and not INPUT_RE.search(first)


def waiting(screen: str) -> bool:
    """Whether the screen of one pane (`tmux capture-pane -p`) holds a prompt for the operator: a pattern in its
    last PROMPT_LINES lines, blank lines below the content not counting, and no input area (#79)."""
    screen = screen.rstrip()
    bottom = '\n'.join(screen.splitlines()[-PROMPT_LINES:])
    return bool(INPUT_RE.search(bottom)) and not input_area(screen)
