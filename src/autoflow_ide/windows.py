"""The coordination session windows that `s` opens, found by their name, and closing them.

`s` names the window `<workspace>#<issue>` (start.py). tmux turns automatic-rename off for a window created
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


def parse_panes(text: str, workspace: str) -> Dict[int, Window]:
    """The windows named `<workspace>#<issue>`, by issue, from `tmux list-panes -a -F PANES_FORMAT`."""
    windows: Dict[int, Window] = {}
    prefix = f"{workspace}#"
    for line in text.splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3:
            continue
        window_id, pane_id, name = parts
        number = name[len(prefix):]
        if not name.startswith(prefix) or not number.isdigit():
            continue
        window = windows.setdefault(int(number), Window(window_id, name))
        if window.id == window_id:
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

    async def find(self, workspace: str) -> Dict[int, Window]:
        """The coordination session windows of `workspace`, by issue; none outside tmux."""
        if not os.environ.get("TMUX"):
            return {}
        return parse_panes(await tmux("list-panes", "-a", "-F", PANES_FORMAT), workspace)

    async def screen(self, window: Window) -> str:
        """What the window's panes show."""
        return "\n".join([await tmux("capture-pane", "-p", "-t", pane) for pane in window.panes])

    async def close(self, window: Window) -> None:
        await tmux("kill-window", "-t", window.id)
