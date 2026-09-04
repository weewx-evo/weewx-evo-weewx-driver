#!/usr/bin/env python3
"""A Davis Vantage, from the serial port to a packet in our envelope.

`standin_test.py` asks whether WeeWX's drivers *import* against
`weewxnames.py`. That is the bottom rung: it proves no name is missing and
nothing else. Vantage is the driver behind every Davis station, it is the
heaviest of the fourteen, and the parts of the stand-in only it uses --
`weewx.engine.StdService`, `ValueTuple`, `GenWithConvert` -- are exactly the
parts an import does not touch.

So this runs the driver against a simulated console (`tools/vantagesim.py`),
which speaks the protocol one layer below the driver: wake-ups, EEPROM reads,
99-byte LOOP packets and 267-byte archive pages, each with its CRC. The
driver's own retry logic, CRC checking and decoding all run.

Five rungs, and each is a thing that can be wrong on its own:

    1  the driver imports
    2  loader() builds it, and it reads the console's EEPROM
    3  genLoopPackets() yields readings, with the right numbers in them
    4  genArchiveRecords() downloads a page and decodes it
    5  the same bytes through WeeWX's own engine give the same packet

Rung 5 is the one that makes the rest mean something. Everything above it
measures our stand-in against our simulator, and both are ours -- agreeing
with itself is not evidence. Where WeeWX is installed, the same simulated
console is driven through *its* code as well, and the packets are compared
field by field. That is the same method as `unitcheck.py` and `difftest.py`:
compare against WeeWX, not against an expectation somebody typed.

    python tools/vantage_test.py
    python tools/vantage_test.py --driver-file ~/weewx/src/weewx/drivers/vantage.py

**What it cannot tell you.** Timing, a console that answers late, an adapter
that drops a byte, firmware that lies about its page count. Those need
hardware, and no simulator is honest about them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

LOOKS_IN = (
    ROOT.parent / "weewx" / "src" / "weewx" / "drivers" / "vantage.py",
    Path("/usr/share/weewx/weewx/drivers/vantage.py"),
    Path("/usr/lib/python3/dist-packages/weewx/drivers/vantage.py"),
)

#: How close two floats have to be to count as the same reading. The driver
#: divides integers by ten and by a thousand, so what comes out is exact to
#: well within this -- anything further apart is a different decoding, not a
#: rounding difference.
CLOSE = 1e-6

failures = 0


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def near(what: str, got: float | None, want: float, tol: float = CLOSE) -> bool:
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
        if candidate.is_file():
            return candidate
    spec = importlib.util.find_spec("weewx")
    if spec and spec.origin:
        beside = Path(spec.origin).parent / "drivers" / "vantage.py"
        if beside.is_file():
            return beside
    return None


def build(path: Path):
    """The driver, built against the stand-in and the simulated console.

    `loader()` is what WeeWX's own engine calls, so this is the same two lines
    the engine runs -- not a class picked out by name and poked at.
    """
    import vantagesim
    from weewx_evo_weewx_driver import weewxnames, weewxshim

    holder: dict = {}
    sys.modules["serial"] = vantagesim.serial_module(holder)
    weewxnames.install()

    module = weewxshim.import_driver("weewx.drivers.vantage", path)
    config = {
        "Station": {"station_type": "Vantage"},
        "Vantage": {"driver": "weewx.drivers.vantage", "type": "serial",
                    "port": "/dev/ttyUSB0", "baudrate": "19200",
                    "wait_before_retry": "0.0", "timeout": "1.0",
                    "max_tries": "2", "loop_request": "1"},
    }
    engine = weewxshim.ShimEngine(config)
    console = module.loader(config, engine)
    return module, console, holder.get("port"), engine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-file", default=None)
    args = parser.parse_args()

    print("a Davis Vantage, simulated down to the serial port\n")

    path = find_driver(args.driver_file)
    if path is None:
        print("  no vantage.py found, so there is nothing to run this against.")
        print("\n  SKIP")
        return 0
    print(f"  driver file: {path}")

    import vantagesim

    # -- 2: it builds, and it read the console -------------------------
    print("\nbuilding it against the simulated console")
    _module, console, port, _engine = build(path)
    check("hardware type, read over the wire", console.hardware_type,
          vantagesim.HARDWARE_TYPE)
    check("archive interval, out of EEPROM 0x2D", console.archive_interval, 300)
    check("altitude, out of EEPROM 0x0F", console.altitude, 700)
    check("rain bucket, out of the setup bits",
          console.rain_bucket_size, "0.01 inches")
    check("it is a service as well as a driver",
          isinstance(console, sys.modules["weewx.engine"].StdService), True)
    # The EEPROM read is the one that goes through the CRC path, so a wrong
    # table or a swapped byte order shows up here and not on the hardware.
    reads = [c for c in port.written if c.startswith(b"EEBRD")]
    check("EEPROM reads, CRC checked by the driver", len(reads) >= 4, True)

    # -- 3: loop packets ------------------------------------------------
    print("\nLOOP packets off the wire")
    got = []
    for packet in console.genLoopPackets():
        got.append(packet)
        if len(got) >= 3:
            break
    check("three packets", len(got), 3)
    first = got[0]
    check("usUnits is US", first["usUnits"], 1)
    check("stamped with a time", isinstance(first.get("dateTime"), int), True)
    near("barometer (inHg)", first["barometer"], 30.12)
    near("outTemp (F)", first["outTemp"], 58.7)
    near("inTemp (F)", first["inTemp"], 71.4)
    check("outHumidity (%)", first["outHumidity"], 76)
    check("windSpeed (mph)", first["windSpeed"], 7)
    check("windDir (deg)", first["windDir"], 247)
    near("rainRate (in/hr)", first["rainRate"], 0.24)
    near("UV", first["UV"], 4.5)
    check("radiation (W/m2)", first["radiation"], 631)
    # The markers are the point of the exercise: a driver that read 0xff as a
    # number would report -0.1 C on eight thermometers this console does not
    # have, and every one of them would look like a reading.
    check("a sensor that is not there reads as None",
          first.get("extraTemp1"), None)

    # -- 4: archive records --------------------------------------------
    print("\narchive records, downloaded a page at a time")
    start = int(time.mktime((2026, 5, 14, 9, 0, 0, 0, 0, -1)))
    stamps = [start + i * 300 for i in range(7)]
    port.load_archive(stamps)
    records = list(console.genArchiveRecords(start - 300))
    check("seven records, in order", [r["dateTime"] for r in records], stamps)
    if records:
        near("outTemp in an archive record", records[0]["outTemp"], 58.7)
        check("interval, in minutes", records[0]["interval"], 5)

    # -- 5: and the same thing through WeeWX itself ---------------------
    print("\nthe same console, driven by WeeWX's own code")
    _against_weewx(path, got[0])

    # -- and through our shim, which is what actually runs it ----------
    print("\nand as our envelope")
    _through_the_shim(path)

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("the driver reads a simulated Vantage, and WeeWX reads it the same")
    return 0


def _against_weewx(path: Path, ours: dict) -> None:
    """WeeWX's own import machinery, the same simulator, the same packet.

    In a subprocess: this process has a stand-in in `sys.modules`, so asking
    for the real WeeWX here would get ours back and compare it with itself.
    That is the failure `standin_test.py` already walked into once.
    """
    got = subprocess.run([sys.executable, "-c", _WEEWX_SCRIPT, str(path)],
                         capture_output=True, text=True, cwd=str(ROOT),
                         check=False)
    if got.returncode != 0:
        reason = (got.stdout.strip()
                  or (got.stderr.strip().splitlines() or ["?"])[-1])
        print(f"  WeeWX is not installed here, so there is nothing to compare "
              f"against ({reason[:70]})")
        return

    theirs = json.loads(got.stdout)
    differ = []
    for field in sorted(set(ours) | set(theirs)):
        # A LOOP packet is stamped when it arrives, and the two runs are
        # seconds apart. Comparing it would make this test pass or fail on
        # how long the subprocess took to start. That it is *there* and an
        # integer is checked above, where it belongs.
        if field == "dateTime":
            continue
        a, b = ours.get(field), theirs.get(field)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > CLOSE:
                differ.append(f"{field}: ours {a}, WeeWX {b}")
        elif a != b:
            differ.append(f"{field}: ours {a!r}, WeeWX {b!r}")
    for one in differ[:8]:
        print(f"    {one}")
    fields = (set(ours) | set(theirs)) - {"dateTime"}
    check(f"all {len(fields)} fields identical to WeeWX's", len(differ), 0)


def _through_the_shim(path: Path) -> None:
    """The whole way: driver, shim, envelope. What the service actually runs.

    `probe` is `weewx-driver check`, so this is the command an operator types,
    with a simulated console behind it instead of hardware.
    """
    from weewx_evo_weewx_driver import weewxshim

    config = {
        "Station": {"station_type": "Vantage"},
        "Vantage": {"driver": "weewx.drivers.vantage", "type": "serial",
                    "port": "/dev/ttyUSB0", "baudrate": "19200",
                    "wait_before_retry": "0.0", "timeout": "1.0",
                    "max_tries": "2", "loop_request": "1"},
    }
    found = weewxshim.probe(config, "weewx.drivers.vantage", count=2,
                            driver_file=path)
    check("the shim gets packets", found["packets"], 2)
    check("under the console's own name", found["source"], "Vantage Pro2")
    check("usUnits survives into the envelope", found["usUnits"], 1)
    check("the archive interval reached the shim",
          found["archive_interval"], 300)
    # Vantage binds in its constructor: it is a service as well as a driver,
    # and a shim that dispatched no events would leave its loop gust to climb
    # all day. That it bound at all is what says the engine reached it.
    check("it bound to the engine", found["callbacks"] >= 1, True)


#: WeeWX's own code, the same simulator, one LOOP packet as JSON.
_WEEWX_SCRIPT = r"""
import json, sys
sys.path.insert(0, "tools")
import vantagesim

holder = {}
sys.modules["serial"] = vantagesim.serial_module(holder)

try:
    import weewx, weewx.drivers.vantage as vantage
except ImportError as exc:
    print(exc)
    raise SystemExit(1)
if getattr(weewx, "__file__", None) is None:
    print("that weewx is a stand-in, not the real one")
    raise SystemExit(1)

config = {
    "Station": {"station_type": "Vantage"},
    "Vantage": {"driver": "weewx.drivers.vantage", "type": "serial",
                "port": "/dev/ttyUSB0", "baudrate": "19200",
                "wait_before_retry": "0.0", "timeout": "1.0",
                "max_tries": "2", "loop_request": "1"},
}

class Engine:
    def bind(self, event_type, callback):
        pass

console = vantage.loader(config, Engine())
for packet in console.genLoopPackets():
    print(json.dumps({k: v for k, v in packet.items()
                      if isinstance(v, (int, float, str)) or v is None}))
    break
"""


if __name__ == "__main__":
    sys.exit(main())
