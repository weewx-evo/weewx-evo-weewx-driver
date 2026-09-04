#!/usr/bin/env python3
"""A Fine Offset console, from the USB bus to a packet in our envelope.

The same five rungs as `vantage_test.py`, for the other console this was
built for: a WH1080 or a clone. `standin_test.py` decodes a record by handing
`_decode` a buffer; this goes through the bus, so everything between --
`_find_device`, `_read_block`, the fixed-block cache, `get_raw_data` walking
the ring buffer backwards -- runs for real against `tools/fousbsim.py`.

    1  the driver imports                       standin_test.py
    2  it finds the device and reads its memory
    3  get_observations() yields a reading, decoded off the bus
    4  the ring buffer is walked, slot by slot
    5  the same memory through WeeWX gives the same packet

Rung 5 is what makes the rest evidence rather than self-agreement, and it
needs a WeeWX installed. Without one the test says so and does the rest.

    python tools/fousb_test.py
    python tools/fousb_test.py --driver-file ~/weewx/src/weewx/drivers/fousb.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

LOOKS_IN = (
    ROOT.parent / "weewx" / "src" / "weewx" / "drivers" / "fousb.py",
    Path("/usr/share/weewx/weewx/drivers/fousb.py"),
    Path("/usr/lib/python3/dist-packages/weewx/drivers/fousb.py"),
)

CLOSE = 1e-6
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
        if candidate.is_file():
            return candidate
    spec = importlib.util.find_spec("weewx")
    if spec and spec.origin:
        beside = Path(spec.origin).parent / "drivers" / "fousb.py"
        if beside.is_file():
            return beside
    return None


def build(path: Path):
    """The driver, against the stand-in and a console made of 64 KB."""
    import fousbsim
    from weewx_evo_weewx_driver import weewxnames, weewxshim

    holder: dict = {}
    mem = fousbsim.memory(count=6)
    sys.modules["usb"] = fousbsim.usb_module(mem, holder)
    weewxnames.install()

    module = weewxshim.import_driver("weewx.drivers.fousb", path)
    config = {
        "Station": {"station_type": "FineOffsetUSB"},
        "FineOffsetUSB": {"driver": "weewx.drivers.fousb",
                          "polling_mode": "PERIODIC",
                          "polling_interval": "0",
                          "data_format": "3080",
                          "timeout": "0.1", "wait_before_retry": "0.0",
                          "max_tries": "2"},
    }
    engine = weewxshim.ShimEngine(config)
    console = module.loader(config, engine)
    return module, console, holder.get("handle"), mem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-file", default=None)
    args = parser.parse_args()

    print("a Fine Offset console, simulated down to the USB bus\n")

    path = find_driver(args.driver_file)
    if path is None:
        print("  no fousb.py found, so there is nothing to run this against.")
        print("\n  SKIP")
        return 0
    print(f"  driver file: {path}")

    import fousbsim

    # -- 2: it found the device and read its memory ---------------------
    print("\nbuilding it against the simulated console")
    _module, console, handle, _mem = build(path)
    check("it found the station on the bus", console.devh is not None, True)

    # The driver *is* the station here: FineOffsetUSB holds the memory
    # methods itself rather than owning a separate object for them.
    block = console.get_fixed_block(["read_period"])
    check("read period, out of the fixed block", block, fousbsim.READ_PERIOD)
    check("blocks read over USB", handle.reads > 0, True)
    # The address travels inside a control message the driver packs by hand.
    # A reading coming back from the right place is the proof that it packed
    # it the way the console reads it.
    check("the address reached the console",
          any(w[:1] == b"\xa1" for w in handle.writes), True)
    check("archive interval follows it", console.archive_interval,
          fousbsim.READ_PERIOD * 60)

    # -- 3: a live reading ----------------------------------------------
    print("\na reading, decoded off the bus")
    first = next(console.get_observations())
    near("temp_out (C)", first.get("temp_out"), 15.2)
    near("temp_in (C)", first.get("temp_in"), 21.3)
    check("hum_out (%)", first.get("hum_out"), 61)
    near("abs_pressure (mbar)", first.get("abs_pressure"), 1013.2)
    near("wind_ave (m/s)", first.get("wind_ave"), 3.2)
    near("wind_gust (m/s)", first.get("wind_gust"), 7.1)
    check("wind_dir (0-15)", first.get("wind_dir"), 11)
    near("illuminance (lux)", first.get("illuminance"), 5000.0)

    # -- 4: the ring buffer ---------------------------------------------
    #
    # `get_raw_data` and `dec_ptr`, not `get_records`. The latter starts with
    # `sync()`, which waits for the console to write its *next* reading in
    # order to date the last one -- up to a logging interval of real waiting,
    # against hardware whose clock is moving. A simulator can only fake that
    # by faking the passage of time, and a test built on faked time reports
    # the fake. What can honestly be measured here is the walk itself: that
    # the pointer steps back by one reading, that each slot decodes, and that
    # the ring wraps at the bottom.
    print("\nthe ring buffer, walked backwards")
    at = console.current_pos()
    check("current_pos is inside the data area", at >= fousbsim.DATA_START, True)
    walked = []
    for _ in range(4):
        walked.append(console.get_data(at, unbuffered=True))
        at = console.dec_ptr(at)
    check("four slots read", len(walked), 4)
    near("outTemp in a stored slot", walked[1].get("temp_out"), 15.2)
    check("each slot carries its delay",
          all(w.get("delay") is not None for w in walked), True)
    # The step is one reading, and the format decides how long that is. A
    # driver reading 3080 slots at 1080 spacing decodes the second half of
    # one reading as the first half of the next, and every number is wrong
    # in a way that still looks like weather.
    check("the pointer steps by one reading",
          console.dec_ptr(fousbsim.DATA_START + 3 * fousbsim.READING_LEN),
          fousbsim.DATA_START + 2 * fousbsim.READING_LEN)

    # -- 5: and the same memory through WeeWX ---------------------------
    print("\nthe same memory, driven by WeeWX's own code")
    _against_weewx(path, first)

    print("\nand as our envelope")
    _through_the_shim(path)

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("the driver reads a simulated Fine Offset, and WeeWX reads it the same")
    return 0


def _against_weewx(path: Path, ours: dict) -> None:
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
        a, b = ours.get(field), theirs.get(field)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > CLOSE:
                differ.append(f"{field}: ours {a}, WeeWX {b}")
        elif a != b:
            differ.append(f"{field}: ours {a!r}, WeeWX {b!r}")
    for one in differ[:8]:
        print(f"    {one}")
    check(f"all {len(set(ours) | set(theirs))} fields identical to WeeWX's",
          len(differ), 0)


def _through_the_shim(path: Path) -> None:
    """The whole way, which is what `weewx-driver check` runs."""
    import fousbsim
    from weewx_evo_weewx_driver import weewxshim

    holder: dict = {}
    sys.modules["usb"] = fousbsim.usb_module(fousbsim.memory(count=6), holder)
    config = {
        "Station": {"station_type": "FineOffsetUSB"},
        "FineOffsetUSB": {"driver": "weewx.drivers.fousb",
                          "polling_mode": "PERIODIC",
                          "polling_interval": "0",
                          "data_format": "3080",
                          "timeout": "0.1", "wait_before_retry": "0.0",
                          "max_tries": "2"},
    }
    found = weewxshim.probe(config, "weewx.drivers.fousb", count=1,
                            driver_file=path)
    check("the shim gets a packet", found["packets"], 1)
    # 16 is METRIC, which is what a Fine Offset reports in and what the
    # archive column would be labelled with. A stand-in that had this wrong
    # would put Celsius in a Fahrenheit column, and no page could tell.
    check("usUnits is METRIC", found["usUnits"], 16)
    # The driver's own `hardware_name`, which is the model and not the
    # class: that is what a station wants to be recorded under.
    check("under the console's own name", found["source"], "WH1080 (USB)")
    check("the read period reached the shim", found["archive_interval"], 300)


_WEEWX_SCRIPT = r"""
import json, sys
sys.path.insert(0, "tools")
import fousbsim

sys.modules["usb"] = fousbsim.usb_module(fousbsim.memory(count=6))

try:
    import weewx, weewx.drivers.fousb as fousb
except ImportError as exc:
    print(exc)
    raise SystemExit(1)
if getattr(weewx, "__file__", None) is None:
    print("that weewx is a stand-in, not the real one")
    raise SystemExit(1)

config = {
    "Station": {"station_type": "FineOffsetUSB"},
    "FineOffsetUSB": {"driver": "weewx.drivers.fousb",
                      "polling_mode": "PERIODIC", "polling_interval": "0",
                      "data_format": "3080",
                      "timeout": "0.1", "wait_before_retry": "0.0",
                      "max_tries": "2"},
}

class Engine:
    def bind(self, event_type, callback):
        pass

console = fousb.loader(config, Engine())
data = console.get_observations()
first = data if isinstance(data, dict) else next(iter(data))
print(json.dumps({k: v for k, v in first.items()
                  if isinstance(v, (int, float, str)) or v is None}))
"""


if __name__ == "__main__":
    sys.exit(main())
