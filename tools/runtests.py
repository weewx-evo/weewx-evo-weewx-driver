#!/usr/bin/env python3
"""Every test in this repository, one after another, one exit code.

    python tools/runtests.py            # everything that can run here
    python tools/runtests.py --list     # what would run, and why not

Two things can be missing, and each is named rather than passed over:

  * **weewx-evo itself.** Everything here imports it -- the shim delivers
    packets to its listener and the form is read for its settings page. On a
    checkout, `PYTHONPATH=/path/to/weewx-evo/src`.
  * **WeeWX.** Five of these compare our stand-in against WeeWX's own code,
    field for field, which is the point of them. Without it they say so and
    skip rather than measure half of themselves.

The one that must never happen quietly is a driver behaving differently here
from how it behaves under WeeWX. That is `alldrivers_test.py`, and it is why
the image these run in has WeeWX in it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

#: What each needs before it can say anything. `weewx` is not listed for the
#: four that find their driver file or skip: the point of those is that WeeWX
#: does not have to be installed, and requiring it would be the opposite
#: claim.
TESTS = [
    ("shim", "shim_test.py",
     "a WeeWX driver, run in its own process, delivering to us", ("weewx",)),
    ("standin", "standin_test.py",
     "the same driver, with no WeeWX installed", ()),
    ("weewxdrivers", "weewxdrivers_test.py",
     "a driver's own form, and a console with no weewx.conf", ()),
    ("vantage", "vantage_test.py",
     "a Davis, simulated down to the serial port", ()),
    ("fousb", "fousb_test.py",
     "a Fine Offset, simulated down to the USB bus", ()),
    ("sdr", "sdr_test.py",
     "weewx-sdr, against an rtl_433 that is a child process", ()),
    ("alldrivers", "alldrivers_test.py",
     "all thirteen, twice, compared against WeeWX itself", ()),
]


def importable(module: str) -> bool:
    """Whether a fresh interpreter can import it.

    In a subprocess: `weewx` pulls in a great deal, and a runner holding half
    of WeeWX is a runner that can make a test pass for the wrong reason.
    """
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, cwd=ROOT, check=False).returncode == 0


def missing(needs: tuple[str, ...], have: dict[str, bool]) -> str:
    for module in needs:
        if not have.get(module, False):
            return {
                "weewx": "WeeWX is not importable, so there is nothing to "
                         "compare against. PYTHONPATH=/path/to/weewx/src",
                "weewx_evo": "weewx-evo is not importable. Everything here "
                             "imports it: PYTHONPATH=/path/to/weewx-evo/src",
            }.get(module, f"{module} is not importable")
    return ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("only", nargs="*", help="only tests matching these")
    parser.add_argument("--list", action="store_true",
                        help="what would run, and why not")
    args = parser.parse_args(argv)

    wanted = [one for one in TESTS
              if not args.only or any(bit in one[0] for bit in args.only)]
    have = {name: importable(name) for name in ("weewx", "weewx_evo")}

    # Before anything else: with the core missing every one of these fails
    # for the same reason, and seven identical tracebacks say it worse than
    # one line does.
    if not have["weewx_evo"]:
        print(missing(("weewx_evo",), have))
        return 2

    if args.list:
        for name, _file, why, needs in wanted:
            reason = missing(needs, have)
            print(f"  {'--' if reason else 'ok'}  {name:<14} {reason or why}")
        return 0

    print(f"{len(wanted)} test(s), in {ROOT}")
    failed, skipped = [], []
    for name, file, why, needs in wanted:
        reason = missing(needs, have)
        if reason:
            skipped.append((name, reason))
            continue
        started = time.time()
        done = subprocess.run([sys.executable, str(TOOLS / file)],
                              cwd=ROOT, capture_output=True, text=True,
                              check=False)
        took = time.time() - started
        mark = "ok  " if done.returncode == 0 else "FAIL"
        print(f"  {mark} {name:<14} {took:5.1f}s  {why}")
        if done.returncode != 0:
            failed.append(name)
            print(done.stdout[-2000:])
            print(done.stderr[-2000:])

    for name, reason in skipped:
        print(f"  --   {name:<14} {reason}")
    print("=" * 70)
    if failed:
        print(f"{len(wanted) - len(failed) - len(skipped)}/"
              f"{len(wanted) - len(skipped)} passed, {len(failed)} failed: "
              f"{', '.join(failed)}")
        return 1
    print(f"{len(wanted) - len(skipped)}/{len(wanted) - len(skipped)} passed"
          + (f", {len(skipped)} skipped" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
