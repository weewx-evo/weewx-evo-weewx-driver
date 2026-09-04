#!/usr/bin/env python3
"""Fetch WeeWX' drivers again, from its latest release.

    python tools/refresh.py            # the latest release
    python tools/refresh.py --tag v5.5.0
    python tools/refresh.py --check    # say what would change, write nothing

Run weekly by `.github/workflows/refresh-drivers.yml`, which opens a pull
request when something moved. A pull request and not a commit: a driver
changing is a change to what this package *runs*, and it has to be looked at
and measured against that WeeWX release before it lands.

The release tarball rather than fourteen raw URLs, and rather than git: one
request, no clone, and it is the same way weewx-evo installs an add-on --
nothing here needs git to be on the machine.

Nothing is transformed on the way in. The whole claim of shipping these is
that the file here and the file in WeeWX are the same bytes, so this writes
what it got and records a digest; `tools/vendored_test.py` is what checks
that later.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHERE = ROOT / "src" / "weewx_evo_weewx_driver" / "drivers"

LATEST = "https://api.github.com/repos/weewx/weewx/releases/latest"
TARBALL = "https://github.com/weewx/weewx/archive/refs/tags/{tag}.tar.gz"

#: Inside the tarball. Anything else in that directory is not a driver:
#: `__init__.py` is the package, `test_*.py` are WeeWX' own tests.
INSIDE = "src/weewx/drivers/"

TIMEOUT = 60.0


def latest_tag() -> str:
    with urllib.request.urlopen(LATEST, timeout=TIMEOUT) as answer:
        return str(json.load(answer)["tag_name"])


def drivers_from(tag: str) -> dict[str, bytes]:
    """Every driver file in that release, by name."""
    url = TARBALL.format(tag=tag)
    with urllib.request.urlopen(url, timeout=TIMEOUT) as answer:
        raw = answer.read()
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive.getmembers():
            # The tarball has one top directory named for the tag, so the
            # path is matched from inside it rather than from the front.
            name = member.name.split("/", 1)[-1]
            if not member.isfile() or not name.startswith(INSIDE):
                continue
            stem = name[len(INSIDE):]
            if "/" in stem or not stem.endswith(".py"):
                continue
            if stem.startswith(("_", "test_")):
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                out[stem] = handle.read()
    return out


def provenance(tag: str, files: dict[str, bytes]) -> str:
    lines = [f"{hashlib.sha256(body).hexdigest()}  {name}"
             for name, body in sorted(files.items())]
    return (
        "WeeWX' own drivers, copied unchanged.\n"
        "\n"
        "source   https://github.com/weewx/weewx\n"
        f"tag      {tag}\n"
        f"path     {INSIDE}\n"
        "licence  GPL-3.0-or-later, the same as this package\n"
        "\n"
        "Byte for byte. `tools/vendored_test.py` checks these digests, and\n"
        "`tools/refresh.py` is what fetched them -- run weekly, opening a\n"
        "pull request when the release moves, so a copy that has drifted is\n"
        "a failing check rather than a surprise.\n"
        "\n" + "\n".join(lines) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=None,
                        help="the WeeWX release to take. Latest by default.")
    parser.add_argument("--check", action="store_true",
                        help="say what would change and write nothing")
    args = parser.parse_args(argv)

    tag = args.tag or latest_tag()
    print(f"WeeWX {tag}")
    files = drivers_from(tag)
    if not files:
        # Not an empty write. A tarball whose layout moved would otherwise
        # delete thirteen drivers and call it an update.
        print("  no driver files in that tarball; nothing was written")
        return 2
    print(f"  {len(files)} driver file(s)")

    changed, added = [], []
    for name, body in sorted(files.items()):
        here = WHERE / name
        if not here.is_file():
            added.append(name)
        elif here.read_bytes() != body:
            changed.append(name)
    gone = sorted(one.name for one in WHERE.glob("*.py")
                  if one.name not in files)

    for what, names in (("changed", changed), ("new", added),
                        ("no longer in WeeWX", gone)):
        if names:
            print(f"  {what}: {', '.join(names)}")
    if not (changed or added or gone):
        print("  unchanged")

    if args.check:
        # An exit code a workflow can branch on: 0 nothing to do, 1 there is.
        return 1 if (changed or added or gone) else 0

    WHERE.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (WHERE / name).write_bytes(body)
    for name in gone:
        # A driver WeeWX dropped goes too. Keeping it would mean shipping a
        # file no release contains, which is the one thing PROVENANCE cannot
        # describe.
        (WHERE / name).unlink()
    (WHERE / "PROVENANCE").write_text(provenance(tag, files), encoding="utf-8")
    print(f"  written to {WHERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
