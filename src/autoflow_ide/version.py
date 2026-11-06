"""The IDE's own version, shown in a corner of the screen so an IDE running older code can be told apart."""
import subprocess
from importlib import metadata
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def ide_version(package_dir: Path = PACKAGE_DIR) -> str:
    """The short commit of the clone the package runs from, as it is when this is called; the IDE is run from
    its clone with `pip install -e .`, and the package version stays the same across commits. When the package
    does not run from its own clone (installed elsewhere, git missing), the package version."""
    try:
        out = subprocess.run(["git", "-C", str(package_dir), "rev-parse", "--show-toplevel", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=True).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    # The clone must be this package's own: a package installed into a site-packages inside another repository
    # would otherwise show that repository's commit.
    if len(out) == 2 and (Path(out[0]) / "src" / "autoflow_ide").resolve() == package_dir.resolve():
        return out[1]
    try:
        return metadata.version("autoflow-ide")
    except metadata.PackageNotFoundError:
        return "unknown"
