#!/usr/bin/env python3
"""weewx-sdr, run against a stand-in rtl_433.

The third driver, and the first from outside WeeWX's own tree:
[weewx-sdr](https://github.com/matthewwall/weewx-sdr) by Matthew Wall, GPLv3.
It reads a software-defined radio -- an RTL-SDR dongle -- and picks up
whatever 433 MHz sensors are within range, which is a lot of hardware that
has no other driver at all.

It matters here for two reasons. It is the one people reach for when their
console has no supported interface, and it is the proof that the stand-in is
not shaped around WeeWX's own fourteen: this driver was written by somebody
else, against the same `import weewx`, and it wants six names.

    weewx.METRIC / METRICWX / US    the unit-system constants
    weewx.units                     kph_to_mph and MILE_PER_KM
    weewx.drivers                   the base class
    weewx.WeeWxIOError              when the child process will not start
    weeutil.weeutil.tobool

The simulator is the honest one of the three. There is no bus to fake: the
driver starts `rtl_433` and reads its output, so `tools/sdrsim.py` writes a
small program that prints recorded rtl_433 lines, and the driver runs it. Its
reader threads, its queue and its parsers all run for real.

    python tools/sdr_test.py --driver-file ~/weewx-sdr/bin/user/sdr.py

Not vendored: the file is found where somebody installed it, and the test
says so and skips when there is none.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

#: $WEEWX_SDR first: that is what the test container sets, and it is the
#: honest way to say "the one I mean" rather than guessing at install paths.
LOOKS_IN = (
    Path(os.environ["WEEWX_SDR"]) if os.environ.get("WEEWX_SDR") else None,
    Path.home() / "weewx-sdr" / "bin" / "user" / "sdr.py",
    Path("/usr/share/weewx/user/sdr.py"),
    Path("/etc/weewx/bin/user/sdr.py"),
    ROOT.parent / "weewx-sdr" / "bin" / "user" / "sdr.py",
)

#: How long to wait for packets. The driver's reader threads are real
#: threads, so this is real waiting -- short, because the stub prints as fast
#: as it can and anything slower than this is a driver that is stuck.
PATIENCE = 20.0

failures = 0


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def near(what: str, got: float | None, want: float, tol: float = 1e-6) -> bool:
    global failures
    ok = got is not None and abs(got - want) <= tol
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def find_driver(given: str | None) -> Path | None:
    if given:
        found = Path(given)
        return found if found.is_file() else None
    for candidate in LOOKS_IN:
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def take_packets(console, count: int = 2) -> list[dict]:
    """Packets off the driver, or as many as arrive before PATIENCE.

    A generator over a queue fed by threads, so this cannot simply be
    `islice`: the driver yields nothing at all until the child has printed
    and a reader has picked it up. Bounded by the clock, and the count of
    what arrived is what gets checked.
    """
    got: list[dict] = []
    until = time.time() + PATIENCE
    for packet in console.genLoopPackets():
        if packet:
            got.append(packet)
        if len(got) >= count or time.time() > until:
            break
    return got


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-file", default=None,
                        help="the sdr.py to run")
    args = parser.parse_args()

    print("weewx-sdr, against a stand-in rtl_433\n")

    path = find_driver(args.driver_file)
    if path is None:
        print("  no sdr.py found, so there is nothing to run this against.")
        print("  It is not part of WeeWX; install weewx-sdr, or point at it:")
        print("    python tools/sdr_test.py --driver-file .../user/sdr.py")
        print("\n  SKIP")
        return 0
    print(f"  driver file: {path}")

    import sdrsim

    why_not = sdrsim.runnable()
    if why_not:
        print(f"\n  This cannot run here: {why_not}")
        print("  The driver splits its command on spaces before starting it.")
        print("\n  SKIP")
        return 0

    from weewx_evo_weewx_driver import weewxnames, weewxshim

    claimed = weewxnames.install()
    print(f"  stood in for: {', '.join(claimed) or 'nothing (WeeWX is here)'}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        folder = Path(raw)
        cmd = sdrsim.write_stub(folder, kind="json")

        print("\nbuilding it against the stand-in rtl_433")
        module = weewxshim.import_driver("user.sdr", path)
        config = {
            "Station": {"station_type": "SDR"},
            "SDR": {"driver": "user.sdr", "cmd": cmd,
                    "sensor_map": sdrsim.sensor_map()},
        }
        engine = weewxshim.ShimEngine(config)
        console = module.loader(config, engine)
        check("it built", console is not None, True)
        check("under its own name", console.hardware_name, "SDR")

        print("\npackets, off a real child process")
        got = take_packets(console, count=2)
        check("packets arrived", len(got) >= 1, True)
        merged: dict = {}
        for packet in got:
            merged.update(packet)
        print(f"  fields: {', '.join(sorted(merged))}")

        # The readings are rtl_433's own numbers from the recorded lines. A
        # stand-in whose METRIC constant were wrong would put these in the
        # wrong column with nothing looking odd.
        if "inTemp" in merged:
            near("inTemp (C), off an Acurite Tower", merged["inTemp"], 15.2)
        if "inHumidity" in merged:
            check("inHumidity (%)", merged["inHumidity"], 61)
        if "outHumidity" in merged:
            check("outHumidity (%)", merged["outHumidity"], 66)
        check("stamped with a time",
              all(isinstance(p.get("dateTime"), (int, float)) for p in got),
              True)
        check("carries a unit system",
              all(p.get("usUnits") is not None for p in got), True)

        _shut_down(console)

        print("\nthe same lines, driven by WeeWX's own code")
        _against_weewx(path, cmd, merged, sdrsim.sensor_map())

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("a driver from outside WeeWX's tree runs on the stand-in too")
    return 0


def _shut_down(console) -> None:
    """Close the driver, and keep its shutdown out of the report.

    `closePort` kills the child and then closes the pipes its reader threads
    are still blocked on, so each thread wakes up on a file object that has
    been taken from underneath it and raises. That is upstream's own
    behaviour, it happens against a real radio too, and it is not something
    this test can fix or should report as a failure -- but printed raw it is
    two tracebacks in the middle of a passing run, which reads like one.

    So the threads' exceptions are caught and counted. If a *different* one
    ever turns up, it is said out loud: this dampens a known noise, it does
    not silence the channel.
    """
    import threading

    seen: list[str] = []
    previous = threading.excepthook

    def quietly(args):
        # Both shapes it takes: the reader raises ValueError on a memoryview
        # over a closed pipe, or AttributeError when the object is gone
        # entirely. Anything else goes through, so this dampens a known noise
        # rather than silencing the channel.
        if args.exc_type in (ValueError, AttributeError):
            seen.append(args.exc_type.__name__)
            return
        previous(args)

    threading.excepthook = quietly
    try:
        console.closePort()
    except Exception as exc:
        print(f"  (closing it raised {type(exc).__name__}, which is "
              f"upstream's shutdown)")
    finally:
        time.sleep(0.3)          # let the readers finish waking up
        threading.excepthook = previous
    if seen:
        print(f"  (its {len(seen)} reader thread(s) raised on shutdown, which "
              f"they do against a radio too)")


def _against_weewx(path: Path, cmd: str, ours: dict, smap: dict) -> None:
    """WeeWX's own import machinery, the same stub, the same readings."""
    got = subprocess.run(
        [sys.executable, "-c", _WEEWX_SCRIPT, str(path), cmd, json.dumps(smap)],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
        timeout=PATIENCE * 3)
    if got.returncode != 0:
        reason = (got.stdout.strip()
                  or (got.stderr.strip().splitlines() or ["?"])[-1])
        print(f"  WeeWX is not installed here, so there is nothing to compare "
              f"against ({reason[:70]})")
        return

    theirs = json.loads(got.stdout.strip().splitlines()[-1])
    differ = []
    for field in sorted(set(ours) | set(theirs)):
        # dateTime is when the line was read, and the two runs are seconds
        # apart. Comparing it would fail on how fast the machine is.
        if field == "dateTime":
            continue
        a, b = ours.get(field), theirs.get(field)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > 1e-6:
                differ.append(f"{field}: ours {a}, WeeWX {b}")
        elif a != b:
            differ.append(f"{field}: ours {a!r}, WeeWX {b!r}")
    for one in differ[:8]:
        print(f"    {one}")
    fields = (set(ours) | set(theirs)) - {"dateTime"}
    check(f"all {len(fields)} fields identical to WeeWX's", len(differ), 0)


_WEEWX_SCRIPT = r"""
import json, sys, time
sys.path.insert(0, "src")

try:
    import weewx
except ImportError as exc:
    print(exc)
    raise SystemExit(1)
if getattr(weewx, "__file__", None) is None:
    print("that weewx is a stand-in, not the real one")
    raise SystemExit(1)

from weewx_evo_weewx_driver import weewxshim
module = weewxshim.import_driver("user.sdr", sys.argv[1])

config = {"Station": {"station_type": "SDR"},
          "SDR": {"driver": "user.sdr", "cmd": sys.argv[2],
                  "sensor_map": json.loads(sys.argv[3])}}

class Engine:
    def bind(self, event_type, callback):
        pass

console = module.loader(config, Engine())
merged, until = {}, time.time() + 20.0
count = 0
for packet in console.genLoopPackets():
    if packet:
        merged.update(packet)
        count += 1
    if count >= 2 or time.time() > until:
        break
try:
    console.closePort()
except Exception:
    pass
print(json.dumps({k: v for k, v in merged.items()
                  if isinstance(v, (int, float, str)) or v is None}))
"""


if __name__ == "__main__":
    sys.exit(main())
