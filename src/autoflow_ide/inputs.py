"""Input from the browser to a session's pane (#86): a message to a coordination session, entered as the
coordinator's submit enters it, and the keys that answer a prompt, each only where the pane would take it."""
import asyncio

from autoflow_ide.screens import input_holds, submit_refused, waiting

# The keys that answer a prompt: a choice's number, the arrows, Enter and Esc, as tmux names them (#86).
PROMPT_KEYS = tuple(str(n) for n in range(10)) + ("Up", "Down", "Left", "Right", "Enter", "Escape")
# How long Claude Code takes to show a pasted text, before the input area is read again (#86).
PASTE_WAIT = 0.3


class InputRefused(Exception):
    """Nothing was entered; the message says why (#86)."""


class NoPrompt(InputRefused):
    """The pane's screen shows no prompt for the keys to answer (#86)."""


async def keys_reach(windows, pane: str) -> None:
    if await windows.in_mode(pane):
        raise InputRefused(f"{pane} is in copy mode: leave it (q) and retry")


async def enter_message(windows, pane: str, text: str) -> None:
    """Enter `text` into the empty input area of `pane` and submit it; Enter is pressed only once the input area
    shows the text, since it would answer a dialog that opened meanwhile (#86)."""
    await keys_reach(windows, pane)
    refused = submit_refused((await windows.pane_screen(pane)).rstrip(), text)
    if refused:
        raise InputRefused(refused)
    await windows.paste(pane, text)
    await asyncio.sleep(PASTE_WAIT)
    if not input_holds((await windows.pane_screen(pane)).rstrip(), text):
        raise InputRefused("the input area does not show the text; Enter not sent")
    await windows.send_keys(pane, "Enter")


async def answer_prompt(windows, pane: str, key: str) -> None:
    """Send `key`, one of PROMPT_KEYS, to `pane` while its screen shows a prompt (#86)."""
    if key not in PROMPT_KEYS:
        raise InputRefused(f"{key!r} is not a key that answers a prompt")
    await keys_reach(windows, pane)
    if not waiting(await windows.pane_screen(pane)):
        raise NoPrompt("no prompt on the screen")
    await windows.send_keys(pane, key)
