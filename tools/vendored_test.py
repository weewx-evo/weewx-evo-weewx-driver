#!/usr/bin/env python3
"""The thirteen drivers shipped here are WeeWX', unchanged.

That is the whole claim of shipping them, and it is the sort of claim that
stops being true quietly. A file edited to fix something on one machine, a
checkout that rewrote its line endings, a refresh that took a release nobody
looked at -- none of those announce themselves, and all three end with this
package running a driver that WeeWX does not have.

So three questions, in the order they can go wrong:

  * do the files match the digests in `drivers/PROVENANCE`?
  * where WeeWX is installed, is what it has the same bytes?
  * is what this package would actually load the shipped file, and not
    whatever WeeWX happens to have beside it?

The second skips where WeeWX is absent, and says so: this package exists so
that WeeWX need not be installed, and a check that demanded it would be
claiming the opposite.

    python tools/vendored_test.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from weewx_evo_weewx_driver import weewxdrivers  # noqa: E402

failures = 0


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def recorded() -> dict[str, str]:
    """`name -> sha256`, out of PROVENANCE."""
    out = {}
    for line in (weewxdrivers.VENDORED / "PROVENANCE").read_text(
            encoding="utf-8").splitlines():
        bits = line.split()
        if len(bits) == 2 and len(bits[0]) == 64 and bits[1].endswith(".py"):
            out[bits[1]] = bits[0]
    return out


def tag() -> str:
    for line in (weewxdrivers.VENDORED / "PROVENANCE").read_text(
            encoding="utf-8").splitlines():
        if line.startswith("tag "):
            return line.split(None, 1)[1].strip()
    return ""


def the_files_are_what_provenance_says() -> None:
    print("\nwhat is here")
    said = recorded()
    check("thirteen of them", len(said), 13)

    wrong = []
    for name, digest in sorted(said.items()):
        path = weewxdrivers.VENDORED / name
        if not path.is_file():
            wrong.append(f"{name}: missing")
            continue
        # Bytes, not text. A checkout that turned LF into CRLF is exactly
        # what this is here to catch, and reading as text would hide it.
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != digest:
            wrong.append(f"{name}: {got[:12]} not {digest[:12]}")
    check("each matches its digest", wrong, [])

    # And nothing extra: a file in there that PROVENANCE does not name has
    # no origin anybody can check, which is the state this file prevents.
    extra = sorted(one.name for one in weewxdrivers.VENDORED.glob("*.py")
                   if one.name not in said)
    check("and nothing is in there unaccounted for", extra, [])


def they_are_weewx_own_bytes() -> None:
    """Where WeeWX is installed, the same file, byte for byte."""
    print("\nagainst WeeWX itself")
    try:
        import weewx.drivers
    except Exception as exc:
        print(f"  ..   no WeeWX to compare against ({exc.__class__.__name__});"
              f" that is the ordinary case for this package")
        return

    import weewx

    where = Path(next(iter(weewx.drivers.__path__)))
    if not (where / "vantage.py").is_file():
        # The stand-in is installed under that name and its path is empty.
        print("  ..   `weewx.drivers` here is the stand-in, not an installation")
        return

    wanted = tag().lstrip("v")
    if weewx.__version__ != wanted:
        # Named rather than compared anyway: WeeWX 5.2 beside a copy of 5.5.0
        # differs for a reason that is not a fault, and a check that goes red
        # for that is one nobody keeps.
        print(f"  ..   WeeWX here is {weewx.__version__}, these are from "
              f"{wanted}; nothing to compare")
        return

    differ = [one.name for one in sorted(weewxdrivers.VENDORED.glob("*.py"))
              if (where / one.name).is_file()
              and (where / one.name).read_bytes() != one.read_bytes()]
    check(f"identical to WeeWX {wanted}", differ, [])


def the_shipped_file_is_the_one_that_runs() -> None:
    """An installed WeeWX does not win, and that is on purpose.

    The opposite of the rule for `weewxnames.py`, and the two are different
    questions: there, WeeWX' own `weewx.units` beats a transcription of it.
    Here the file *is* the driver, so a machine that happens to carry WeeWX
    5.2 would run a driver this package's tests never measured, with nothing
    anywhere to say so.
    """
    print("\nwhich file wins")
    found = {one.module: one for one in weewxdrivers.available()}
    check("all thirteen are listed", len(found), 13)

    vantage = found.get("weewx.drivers.vantage")
    check("Vantage among them", vantage is not None, True)
    if vantage is not None:
        check("and it is the shipped file",
              Path(vantage.path).parent == weewxdrivers.VENDORED, True)

    # What somebody puts in the data directory still wins over the shipped
    # copy: a driver patched for their own hardware is the one case where
    # taking the decision away leaves no way to run the fix at all.
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        theirs = Path(raw) / "vantage.py"
        theirs.write_bytes(
            (weewxdrivers.VENDORED / "vantage.py").read_bytes())
        mine = {one.module: one for one in weewxdrivers.available(raw)}
        got = mine.get("weewx.drivers.vantage")
        check("a file in the data directory beats it",
              got is not None and Path(got.path) == theirs, True)


def main() -> int:
    the_files_are_what_provenance_says()
    they_are_weewx_own_bytes()
    the_shipped_file_is_the_one_that_runs()

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("the drivers shipped here are WeeWX', and they are the ones that run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
