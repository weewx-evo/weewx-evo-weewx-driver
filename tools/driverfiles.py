"""Where these tests look for a WeeWX driver file, in one place.

Five of them had the same tuple of three paths, and with the drivers shipped
in the package there would have been six copies of a four-entry list. The
first entry is the one that matters: the shipped file is the one this package
actually runs, so a test measuring anything else is measuring a driver nobody
here will use.

The rest are for a machine that has WeeWX beside this one -- a checkout, a
distribution package -- and they stay because `alldrivers_test` compares the
two, which needs both.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Directories holding WeeWX driver files, best first.
def directories() -> list[Path]:
    """Every directory that may hold WeeWX' drivers, shipped copy first."""
    out = [ROOT / "src" / "weewx_evo_weewx_driver" / "drivers",
           ROOT.parent / "weewx" / "src" / "weewx" / "drivers",
           Path("/usr/share/weewx/weewx/drivers"),
           Path("/usr/lib/python3/dist-packages/weewx/drivers")]
    # And wherever an installed WeeWX put them, which is neither of the two
    # fixed paths in a venv.
    spec = importlib.util.find_spec("weewx")
    if spec and spec.origin:
        out.append(Path(spec.origin).parent / "drivers")
    return out


def a_driver(name: str, given: str | None = None) -> Path | None:
    """One driver file by name (`fousb.py`), or None if it is nowhere.

    `given` is a path somebody passed on the command line and it wins, so
    that a test can be pointed at a file being worked on.
    """
    if given:
        found = Path(given)
        return found if found.is_file() else None
    for where in directories():
        found = where / name
        if found.is_file():
            return found
    return None


def a_directory(given: str | None = None) -> Path | None:
    """The directory to take every driver from, or None if there is none."""
    if given:
        found = Path(given)
        return found if found.is_dir() else None
    for where in directories():
        if (where / "vantage.py").is_file():
            return where
    return None
