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


# Whether a screen takes a message from the browser, copied from the coordinator's tools/terminals (input_held,
# submit_refused, input_holds, MAX_SUBMIT_BYTES) of the version the IDE supports, so a message is entered only where
# the coordinator's submit would enter it (#86).
EMPTY_INPUT = '❯'
MAX_SUBMIT_BYTES = 700


def input_area_text(screen: str):
    """What Claude Code's input area shows, or None if the screen has none: between its last two rules (#86)."""
    rules = list(INPUT_RULE_RE.finditer(screen))
    return screen[rules[-2].end():rules[-1].start()].strip() if len(rules) >= 2 else None


def input_held(screen: str):
    """Why a Claude Code screen cannot take a message, or None: only an input area recognised as empty passes;
    a draft, a dialog and a screen this does not know all hold the input (#86)."""
    area = input_area_text(screen)
    if area is None:
        return 'no input area on the screen'
    if area == EMPTY_INPUT:
        return None
    return (f'the input area is not empty: {area.splitlines()[0] if area else ""!r} '
            '(if this is a prompt suggestion, the session needs promptSuggestionEnabled: false)')


def submit_refused(screen: str, text: str):
    """Why `text` cannot be entered on this screen, or None (#86)."""
    size = len(text.encode())
    return f'{size} bytes is over {MAX_SUBMIT_BYTES}: put it in a file' if size > MAX_SUBMIT_BYTES else input_held(screen)


def input_holds(screen: str, text: str) -> bool:
    """Whether the input area shows the end of `text`, so that Enter would submit it and nothing else; whitespace
    does not count, as the screen wraps and indents lines (#86)."""
    rules = list(INPUT_RULE_RE.finditer(screen))
    if not rules:
        return False
    area = screen[rules[-2].end() if len(rules) >= 2 else 0:rules[-1].start()].strip()
    if area.startswith(EMPTY_INPUT):
        area = area[len(EMPTY_INPUT):]
    shown = re.sub(r'\s+', '', area)
    return bool(shown) and re.sub(r'\s+', '', text).endswith(shown)
