"""What the IDE keeps between starts: the workspaces the operator chose recently, which open unfolded (#44)."""
import json
from pathlib import Path
from typing import List

from autoflow_ide.version import PACKAGE_DIR

# How many recently chosen workspaces are kept.
RECENT_WORKSPACES = 3
STATE_NAME = ".ide-state.json"


def state_path(package_dir: Path = PACKAGE_DIR) -> Path:
    """`.ide-state.json` at the top of the clone the package runs from (ignored by git), as the IDE is run
    from its clone with `pip install -e .`; otherwise `~/.config/autoflow-ide/state.json`."""
    root = package_dir.resolve().parent.parent
    if (root / "pyproject.toml").is_file() and (root / ".git").exists() and (root / "src" / "autoflow_ide").is_dir():
        return root / STATE_NAME
    return Path.home() / ".config" / "autoflow-ide" / "state.json"


def read_recent(path: Path) -> List[str]:
    """The recently chosen workspaces kept in `path`, most recent first; none when it is missing or unreadable."""
    try:
        recent = json.loads(path.read_text()).get("recent", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [name for name in recent if isinstance(name, str)][:RECENT_WORKSPACES] if isinstance(recent, list) else []


def write_recent(path: Path, recent: List[str]) -> None:
    """Keep `recent` in `path`; raises OSError when it cannot be written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"recent": recent}, indent=2) + "\n")


def chosen(recent: List[str], name: str) -> List[str]:
    """`recent` with `name` chosen now: first, the oldest dropped past RECENT_WORKSPACES."""
    return ([name] + [n for n in recent if n != name])[:RECENT_WORKSPACES]
