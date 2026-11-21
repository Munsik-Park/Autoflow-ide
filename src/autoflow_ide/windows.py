"""The coordination session windows that `s` opens, found by their name, and closing them.

`s` and `S` name the window `<workspace>#<issue>` (start.py); an issue can have several. tmux turns automatic-rename off for a window created
with a name, so the name stays while Claude Code runs in it. The windows are found by that name rather than
remembered, so the ones opened before the IDE restarted are found too.
"""
import asyncio
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Space-separated: tmux 3.4 prints a tab in a format as "_". The name goes last, as it may hold spaces.
PANES_FORMAT = "#{window_id} #{pane_id} #{window_name}"


class WindowError(Exception):
    """A window could not be read or closed; the message is shown to the operator."""


@dataclass
class Window:
    id: str
    name: str
    panes: List[str] = field(default_factory=list)


def parse_panes(text: str, workspace: str) -> Dict[int, List[Window]]:
    """Every window named `<workspace>#<issue>`, by issue, each with its own panes, in the order
    `tmux list-panes -a -F PANES_FORMAT` lists them: an issue can have several coordination sessions."""
    windows: Dict[int, List[Window]] = {}
    prefix = f"{workspace}#"
    for line in text.splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3:
            continue
        window_id, pane_id, name = parts
        number = name[len(prefix):]
        if not name.startswith(prefix) or not number.isdigit():
            continue
        found = windows.setdefault(int(number), [])
        window = next((w for w in found if w.id == window_id), None)
        if window is None:
            window = Window(window_id, name)
            found.append(window)
        window.panes.append(pane_id)
    return windows


async def tmux(*args: str, stdin: Optional[str] = None) -> str:
    """The stdout of tmux with `args`, given `stdin` when set; on failure, its stderr as a WindowError."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise WindowError("tmux is not installed") from e
    out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
    if proc.returncode != 0:
        raise WindowError(err.decode(errors="replace").strip() or f"tmux {args[0]} exited with status {proc.returncode}")
    return out.decode(errors="replace")


class TmuxWindows:
    """The windows of the tmux server the IDE runs in."""

    async def find(self, workspace: str) -> Dict[int, List[Window]]:
        """The coordination session windows of `workspace`, by issue; none outside tmux."""
        if not os.environ.get("TMUX"):
            return {}
        return parse_panes(await tmux("list-panes", "-a", "-F", PANES_FORMAT), workspace)

    async def screens(self, window: Window) -> List[str]:
        """What each of the window's panes shows, in order: a prompt is judged per pane (#79)."""
        return [await tmux("capture-pane", "-p", "-t", pane) for pane in window.panes]

    async def screen(self, window: Window) -> str:
        """What the window's panes show."""
        return "\n".join(await self.screens(window))

    async def close(self, window: Window) -> None:
        await tmux("kill-window", "-t", window.id)

    # A pane as browser control reads it and writes to it (#86). None of these attaches a client or takes the
    # pane's pipe, so no window's size changes. (#86)

    async def frame(self, pane: str) -> "Frame":
        """What `pane` shows now, with its colours, cursor and size. capture-pane goes first: it fails on a pane
        that is gone, where display-message prints an empty line (#86)."""
        capture = await tmux("capture-pane", "-p", "-e", "-t", pane)
        shape = await tmux("display-message", "-p", "-t", pane, SHAPE_FORMAT)
        return parse_frame(shape, capture)

    async def pane_screen(self, pane: str) -> str:
        """The text `pane` shows, as the window check reads it (#86)."""
        return await tmux("capture-pane", "-p", "-t", pane)

    async def session_id(self, pane: str) -> str:
        """The session id the IDE kept on `pane` when it started it; empty when it has none (#86)."""
        return (await tmux("display-message", "-p", "-t", pane, "#{@autoflow-session-id}")).strip()

    async def in_mode(self, pane: str) -> Optional[bool]:
        """Whether `pane` is in a mode (copy mode), where keys go to the mode and are lost; None when tmux prints
        nothing, as it does for a pane that is gone, so the pane's next read says why (#86)."""
        mode = (await tmux("display-message", "-p", "-t", pane, "#{pane_in_mode}")).strip()
        return None if not mode else mode != "0"

    async def paste(self, pane: str, text: str) -> None:
        """Enter `text` in `pane` as the coordinator's submit does: paste-buffer -r, so a line feed does not submit
        its line, and not -p, so the text is not pasted material. Each paste has a buffer of its own, deleted once
        pasted or when the paste fails (#86)."""
        buffer = f"autoflow-ide-{pane.lstrip('%')}-{secrets.token_hex(4)}"
        await tmux("load-buffer", "-b", buffer, "-", stdin=text.strip("\n"))
        try:
            await tmux("paste-buffer", "-r", "-d", "-b", buffer, "-t", pane)
        except WindowError:
            try:
                await tmux("delete-buffer", "-b", buffer)
            except WindowError:
                pass  # already gone (#86)
            raise

    async def send_keys(self, pane: str, *keys: str) -> None:
        await tmux("send-keys", "-t", pane, *keys)


# The cursor, the size and the modes of a pane, read with its screen for the browser's view (#86).
SHAPE_FORMAT = "#{cursor_x} #{cursor_y} #{cursor_flag} #{pane_width} #{pane_height} #{alternate_on} #{pane_in_mode}"


@dataclass
class Frame:
    """A pane's screen as the browser's view draws it: its rows with their colours, the cursor and the size (#86)."""
    rows: List[str]
    width: int
    height: int
    cursor: Tuple[int, int]
    cursor_visible: bool
    alternate: bool
    in_mode: bool


# In a row of capture-pane -e: SGR is kept; every other escape sequence and control character is dropped, so a
# link printed by a session (OSC 8) reaches the page as its text only (#86).
SGR_RE = re.compile(r"\x1b\[[0-9;:]*m")
ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;:]*m"                              # SGR (#86)
    r"|\x1b\[[0-?]*[ -/]*[@-~]"                      # any other CSI (#86)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)"           # OSC, ended by BEL or ST (#86)
    r"|\x1b[P^_X].*?(?:\x1b\\|$)"                     # DCS, SOS, PM, APC, ended by ST (#86)
    r"|\x1b.?"                                        # any other escape (#86)
    r"|[\x00-\x1f\x7f-\x9f]")                          # C0 and C1 controls (#86)


def clean_row(row: str) -> str:
    return ESCAPE_RE.sub(lambda m: m.group(0) if SGR_RE.fullmatch(m.group(0)) else "", row)


def parse_frame(shape: str, capture: str) -> Frame:
    """The frame of a pane from display-message's SHAPE_FORMAT line and capture-pane -p -e's output (#86)."""
    fields = shape.split()
    if len(fields) != 7 or not all(f.isdigit() for f in fields):
        raise WindowError(f"tmux display-message: unexpected output {shape.strip()!r}")
    x, y, flag, width, height, alternate, mode = (int(f) for f in fields)
    rows = capture.split("\n")
    if rows and rows[-1] == "":
        rows.pop()
    return Frame([clean_row(r) for r in rows], width, height, (x, y), bool(flag), bool(alternate), bool(mode))


# Where a pane is, as the tmux status line names it: session:window-index, then the window's name. The pane id
# goes first; the rest is shown as tmux prints it, so names with spaces need no splitting.
PLACES_FORMAT = "#{pane_id} #{session_name}:#{window_index} #{window_name}"


def parse_places(text: str) -> Dict[str, str]:
    """Each pane's place ("2:1 autoflow-coordinator#59"), by pane id, from `tmux list-panes -a -F PLACES_FORMAT`."""
    places: Dict[str, str] = {}
    for line in text.splitlines():
        pane, _, place = line.partition(" ")
        if pane.startswith("%") and place:
            places[pane] = place
    return places


async def pane_places() -> Dict[str, str]:
    """The place of every pane of the tmux server the IDE runs in; none outside tmux."""
    if not os.environ.get("TMUX"):
        return {}
    return parse_places(await tmux("list-panes", "-a", "-F", PLACES_FORMAT))
