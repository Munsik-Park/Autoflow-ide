"""Move tmux focus to a session's pane.

The coordinator's tmux terminal names a session's surface by its pane id (%N). select-pane alone does not
change the current window, so the pane's window is selected first.
"""
import asyncio
import os


class FocusError(Exception):
    """Focus could not move; the message is shown to the operator."""


async def tmux(*args: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError as e:
        raise FocusError("tmux is not installed") from e
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise FocusError(err.decode(errors="replace").strip() or f"tmux {args[0]} exited with status {proc.returncode}")


async def focus_surface(surface: str) -> None:
    if not os.environ.get("TMUX"):
        raise FocusError("not inside tmux")
    if not surface.startswith("%"):
        raise FocusError(f"{surface} is not a tmux pane")
    await tmux("select-window", "-t", surface)
    await tmux("select-pane", "-t", surface)
