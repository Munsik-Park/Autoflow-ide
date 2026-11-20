"""The coordination session windows that `s` opens, found by their name, and closing them.

`s` and `S` name the window `<workspace>#<issue>` (start.py); an issue can have several. tmux turns automatic-rename off for a window created
with a name, so the name stays while Claude Code runs in it. The windows are found by that name rather than
remembered, so the ones opened before the IDE restarted are found too.
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Dict, List

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


async def tmux(*args: str) -> str:
    """The stdout of tmux with `args`; on failure, its stderr as a WindowError."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise WindowError("tmux is not installed") from e
    out, err = await proc.communicate()
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
