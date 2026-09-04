#!/usr/bin/env python3
"""A WeeWX driver, run with no WeeWX installed.

`weewxshim.py` has always run a WeeWX driver unchanged, but only where WeeWX
was on the disk -- the driver file says `import weewx` in its first lines. So
a station whose one reason to want WeeWX was a USB console had to install the
whole of it.

Measured across the fourteen drivers in WeeWX's own tree, that import asks
for very little: exceptions, three integers, one twelve-line function.
`ingest/weewxnames.py` is the whole of it, and this asks whether a real
driver runs against it.

The driver under test is **fousb**, WeeWX's Fine Offset USB driver, for the
WH1080 and its many clones. It is the lightest of the fourteen (four names)
and the one this was built for. It is not vendored here: the file is found in
a WeeWX checkout or an installation, and the test says so and skips when
there is none.

    python tools/standin_test.py
    python tools/standin_test.py --driver-file /usr/share/weewx/weewx/drivers/fousb.py

pyusb is stood in for as well. This asks about the module, not about a
console: nobody here has one, so what can be measured is that the file
imports, decodes a record and produces a packet in our envelope's shape.
Where a real WeeWX is installed, the same decode is run against *its*
wxformulas too, and the two are compared -- which is the only way to know the
stand-in is the same and not merely plausible.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
import driverfiles  # noqa: E402  (after the path is set up)

failures = 0


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def near(what: str, got: float | None, want: float, tol: float = 1e-9) -> bool:
    global failures
    ok = got is not None and abs(got - want) <= tol
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def find_driver(given: str | None) -> Path | None:
    """The file, from wherever this machine has one. See `driverfiles`."""
    return driverfiles.a_driver("fousb.py", given)


def fake_usb() -> types.ModuleType:
    """pyusb, enough for the import. The hardware is not what is under test.

    Kept here rather than in `weewxnames`: pyusb is a real dependency of a
    real driver, and a stand-in for it in the shipped code would turn "no
    USB library installed" into a driver that builds and reads nothing.
    """
    usb = types.ModuleType("usb")
    usb.TYPE_CLASS, usb.RECIP_OTHER, usb.CLASS_HUB = 0x20, 0x03, 9
    usb.REQ_CLEAR_FEATURE, usb.REQ_SET_FEATURE = 1, 3
    usb.busses = list
    return usb


def one_record() -> bytearray:
    """Twenty bytes in the layout a 3080 console writes.

    Five minutes since the last, 48% indoors at 21.3 C, 61% outside at
    15.2 C, 1013.2 mbar, 3.2 m/s averaged with a 7.1 m/s gust from the WSW,
    1420 tips of the rain gauge, and the light sensor a 3080 has.
    """
    raw = bytearray(32)
    raw[0] = 5                                     # delay, minutes
    raw[1] = 48                                    # hum_in
    raw[2], raw[3] = 213 & 0xFF, 213 >> 8          # temp_in, 0.1 C
    raw[4] = 61                                    # hum_out
    raw[5], raw[6] = 152 & 0xFF, 152 >> 8          # temp_out
    raw[7], raw[8] = 10132 & 0xFF, 10132 >> 8      # abs_pressure, 0.1 mbar
    raw[9] = 32                                    # wind_ave, 0.1 m/s
    raw[10] = 71                                   # wind_gust
    raw[11] = 0                                    # the high bits of both
    raw[12] = 11                                   # wind_dir, 0-15
    raw[13], raw[14] = 1420 & 0xFF, 1420 >> 8      # rain, 0.3 mm a tip
    raw[15] = 0                                    # status
    raw[16], raw[17], raw[18] = 0x50, 0xC3, 0x00   # illuminance, 0.1 lux
    raw[19] = 4                                    # uv
    return raw


def load_against_standin(path: Path) -> object:
    """Import the driver with the stand-in in place, and nothing else."""
    from weewx_evo_weewx_driver import weewxnames, weewxshim

    sys.modules.setdefault("usb", fake_usb())
    claimed = weewxnames.install()
    print(f"  stood in for: {', '.join(claimed) or 'nothing (WeeWX is here)'}")
    return weewxshim.import_driver("weewx.drivers.fousb", path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-file", default=None,
                        help="the fousb.py to run. Found automatically if a "
                             "WeeWX checkout or installation is around.")
    args = parser.parse_args()

    print("a WeeWX driver against the stand-in\n")

    path = find_driver(args.driver_file)
    if path is None:
        print("  no fousb.py found, so there is nothing to run this against.")
        print("  Looked in:")
        for one in driverfiles.directories():
            print(f"    {one}")
        print("\n  SKIP")
        return 0
    print(f"  driver file: {path}")

    # -- the point of the whole thing ---------------------------------
    #
    # In a subprocess with `weewx` blocked, so that a WeeWX installed on this
    # machine cannot make the test pass. That is exactly how this would look
    # on the machine it is for, and importing here would hide it.
    print("\nwith `import weewx` made impossible")
    blocked = subprocess.run(
        [sys.executable, "-c", _BLOCKED_SCRIPT, str(path)],
        capture_output=True, text=True, cwd=str(ROOT), check=False)
    for line in blocked.stdout.splitlines():
        print(f"  {line}")
    if blocked.returncode != 0:
        for line in blocked.stderr.strip().splitlines()[-6:]:
            print(f"    {line}")
    check("the driver runs with no weewx importable", blocked.returncode, 0)

    # -- and the numbers ----------------------------------------------
    print("\ndecoding one record")
    fousb = load_against_standin(path)
    decoded = fousb._decode(one_record(), fousb.reading_format["3080"])

    near("temp_out (C)", decoded["temp_out"], 15.2, 1e-6)
    near("abs_pressure (mbar)", decoded["abs_pressure"], 1013.2, 1e-6)
    near("wind_gust (m/s)", decoded["wind_gust"], 7.1, 1e-6)
    check("wind_dir (0-15)", decoded["wind_dir"], 11)
    near("rain (mm)", decoded["rain"], 426.0, 1e-6)
    check("delay (minutes)", decoded["delay"], 5)
    near("illuminance (lux)", decoded["illuminance"], 5000.0, 1e-6)

    print("\nand as a packet")
    packet = fousb.pywws2weewx(decoded, 1787734265, 141.9, 1787733965, 24.0)
    # 16 is METRIC, and it is WeeWX's number because it is written into every
    # archive ever made. A stand-in that got this wrong would put a Celsius
    # reading in a Fahrenheit column and nothing would look wrong.
    check("usUnits is METRIC", packet["usUnits"], 16)
    check("dateTime survives", packet["dateTime"], 1787734265)
    near("windDir (deg)", packet["windDir"], 247.5, 1e-6)
    near("windSpeed (km/h)", packet["windSpeed"], 11.52, 1e-6)
    # calculate_rain is the one function of wxformulas the driver uses. 42.6
    # cm total against 141.9 last time is a counter that went backwards, and
    # the right answer to that is None -- not zero, which would be a
    # measurement saying no rain fell.
    check("a counter that went backwards gives None", packet["rain"], None)
    near("rainTotal (cm)", packet["rainTotal"], 42.6, 1e-6)

    print("\nrain increments, against the real formula where it is here")
    _compare_rain()

    print("\nand every other driver in WeeWX's tree")
    _every_driver(path.parent)

    print("\nweewx.units, which is transcribed rather than handed through")
    _compare_units()

    print("\nwhat the stand-in claims to be")
    from weewx_evo_weewx_driver import weewxnames
    print(f"  {weewxnames.describe()}")
    # It must not be lying about which one is in force: a failure means
    # something different against each.
    check("standing_in is not empty when weewx is not installed",
          bool(weewxnames.standing_in()) or weewxnames.installed(), True)

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("a WeeWX driver decodes a record with no WeeWX on the machine")
    return 0


#: The cases the two formulas are asked. A counter that went backwards is the
#: one that matters -- it is a gauge reset, and the answer decides whether an
#: archive gets None or a wrong number.
RAIN_CASES = [(10.0, 8.0), (8.0, 10.0), (5.0, 5.0), (None, 3.0), (3.0, None),
              (None, None), (0.0, 0.0), (0.3, 0.0), (1e-9, 0.0)]


def _compare_rain() -> None:
    """Our calculate_rain against WeeWX's own, where WeeWX is installed.

    In a subprocess, and that is not caution: by the time the rest of this
    test has run, `weewx.wxformulas` in *this* process is the stand-in, so
    asking for the real one here compares it against itself and passes for
    the wrong reason. It did, on the first run.

    Being plausible is not being the same. Twelve lines are twelve chances to
    have written `>` where WeeWX wrote `>=`, and the difference shows up as a
    rain total that is quietly short one bucket-tip a year.
    """
    import json

    got = subprocess.run(
        [sys.executable, "-c", _RAIN_SCRIPT, json.dumps(RAIN_CASES)],
        capture_output=True, text=True, cwd=str(ROOT), check=False)
    if got.returncode != 0:
        reason = got.stdout.strip() or got.stderr.strip().splitlines()[-1:]
        print(f"  WeeWX is not installed here, so there is nothing to "
              f"compare against ({reason})")
        return

    theirs = json.loads(got.stdout)
    from weewx_evo_weewx_driver.weewxnames import _wxformulas_module

    ours = _wxformulas_module()
    same = 0
    for (new, old), want in zip(RAIN_CASES, theirs, strict=True):
        mine = ours.calculate_rain(new, old)
        if mine == want:
            same += 1
        else:
            print(f"    differs at new={new} old={old}: ours {mine}, "
                  f"WeeWX {want}")
    check(f"same answer for all {len(RAIN_CASES)} rain cases",
          same, len(RAIN_CASES))


#: Fields where our answer and WeeWX's differ on purpose. Same idea as the
#: `KNOWN` list in `unitcheck.py`: the point is that our drift is *their*
#: drift, not that there is none, and a difference nobody wrote down is
#: indistinguishable from a mistake.
#:
#: Both of these are outside a driver's reach -- no driver in WeeWX's tree
#: sends either field -- so they cannot change a reading that came off a
#: console. They are here because they showed up in the comparison and are
#: worth a name.
KNOWN_UNIT_DIFFERENCES = {
    # WeeWX has no group for these at all (`obs_group_dict` returns None), so
    # it leaves them as they are. We put them in `group_pressure`, and that
    # is right: `derive.py` computes them and converts to the record's own
    # unit on the way out (`* from_mbar`), so a US record really does hold
    # inHg. WeeWX simply never gave the two fields a group.
    "vaporPressure",
    "satVaporPressure",
}

#: And one where the *exception* differs. WeeWX's conversion table has no
#: `km_per_hour2 -> mile_per_hour2`, so it raises KeyError from the missing
#: key; ours raises ValueError naming the pair. Nothing sends a squared
#: speed, but a caller catching one and not the other would notice.
KNOWN_ERROR_DIFFERENCE = "km_per_hour2"

#: Drivers that cannot import here for a reason that is not ours. `ws23xx`
#: uses `fcntl`, which is POSIX and simply absent on Windows -- counting it
#: as a miss there would mean the number says which machine ran the test.
NOT_OURS = {"ws23xx"} if sys.platform == "win32" else set()


def _every_driver(folder: Path) -> None:
    """Each driver in WeeWX's tree, imported against the stand-in.

    fousb is the one this was built for, but the file is not fousb-shaped:
    what a driver imports is a short list, and the same list serves all of
    them. Measuring that is what keeps the next name somebody needs from
    being found by a station that has stopped recording.

    Only the import, not the hardware. A driver that imports is one whose
    every module-level name resolved -- which is exactly what the stand-in is
    responsible for, and where it fails it fails on the first line.
    """
    found = sorted(p for p in folder.glob("*.py") if p.stem != "__init__")
    if not found:
        print(f"  no drivers beside {folder}")
        return

    worked, broke = [], []
    for one in found:
        got = subprocess.run(
            [sys.executable, "-c", _ONE_DRIVER, str(one), one.stem],
            capture_output=True, text=True, cwd=str(ROOT), check=False)
        if got.returncode == 0:
            worked.append(one.stem)
        elif one.stem in NOT_OURS:
            print(f"    {one.stem}: skipped, needs a module this platform "
                  f"does not have")
        else:
            why = (got.stdout.strip()
                   or (got.stderr.strip().splitlines() or ["?"])[-1])
            broke.append(one.stem)
            print(f"    {one.stem}: {why[:70]}")

    print(f"  {', '.join(worked)}")
    check("every driver imports against the stand-in", len(broke), 0)


def _compare_units() -> None:
    """`to_std_system` on every field of the schema, against WeeWX's own.

    The rest of the stand-in hands something through or is three lines long.
    This one walks a record and converts every field in it, which makes it
    the piece where a difference can hide -- and the one a driver reaches
    through `GenWithConvert`.

    Every field the schema knows a group for, in every direction between the
    three unit systems. Values that are not round: 0.0 and 1.0 survive
    several wrong conversions unchanged.
    """
    from weewx_evo import units as ours

    fields = sorted(ours.GROUPS)
    systems = [1, 16, 17]

    def record(system):
        rec = {"dateTime": 1778742000, "usUnits": system}
        for index, field in enumerate(fields):
            rec[field] = 3.7 + index * 0.13
        return rec

    cases = [(record(s), t) for s in systems for t in systems]
    got = subprocess.run(
        [sys.executable, "-c", _UNITS_SCRIPT, json.dumps(cases)],
        capture_output=True, text=True, cwd=str(ROOT), check=False)
    if got.returncode != 0:
        print("  WeeWX is not installed here, so there is nothing to compare "
              "against")
        return

    theirs = json.loads(got.stdout)
    from weewx_evo_weewx_driver.weewxnames import _units_module

    mine_mod = _units_module()
    unexpected = []
    for (rec, target), want in zip(cases, theirs, strict=True):
        try:
            mine = mine_mod.to_std_system(dict(rec), target)
            mine = {k: (round(v, 8) if isinstance(v, float) else v)
                    for k, v in mine.items()}
        except Exception as exc:
            mine = {"__error__": f"{type(exc).__name__}: {exc}"}
        for field in sorted(set(mine) | set(want)):
            if mine.get(field) == want.get(field):
                continue
            if field in KNOWN_UNIT_DIFFERENCES:
                continue
            if field == "__error__" and KNOWN_ERROR_DIFFERENCE in str(
                    want.get(field, "")) + str(mine.get(field, "")):
                continue
            unexpected.append(
                f"{field} ({rec['usUnits']}->{target}): "
                f"ours {mine.get(field)!r}, WeeWX {want.get(field)!r}")

    for one in unexpected[:6]:
        print(f"    {one}")
    print(f"  {len(fields)} fields over {len(cases)} system pairs, "
          f"{len(KNOWN_UNIT_DIFFERENCES)} known difference(s) allowed")
    check("nothing else converts differently", len(unexpected), 0)


#: WeeWX's own `to_std_system`, in a process with nothing standing in.
_UNITS_SCRIPT = r"""
import json, sys
try:
    import weewx.units as u
except ImportError as exc:
    print(exc)
    raise SystemExit(1)
if getattr(u, "__file__", None) is None:
    print("that weewx.units is a stand-in, not the real one")
    raise SystemExit(1)

out = []
for rec, target in json.loads(sys.argv[1]):
    try:
        got = u.to_std_system(dict(rec), target)
        out.append({k: (round(v, 8) if isinstance(v, float) else v)
                    for k, v in got.items()})
    except Exception as exc:
        out.append({"__error__": f"{type(exc).__name__}: {exc}"})
print(json.dumps(out))
"""


#: One driver, in a process where weewx cannot be imported and every hardware
#: library is faked. The libraries are faked because whether pyusb is
#: installed is a different question from whether the stand-in is complete.
_ONE_DRIVER = r"""
import sys, types
sys.path.insert(0, "src")

class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("weewx", "weeutil"):
            raise ImportError(f"{name} is blocked for this test")
        return None

sys.meta_path.insert(0, Blocked())
try:
    import weewx
except ImportError:
    pass
else:
    raise SystemExit("weewx imported anyway; the block did not work")
sys.meta_path.pop(0)

for name in ("usb", "usb.core", "usb.util", "usb.backend",
             "usb.backend.libusb1", "serial", "hid", "ftdi", "pylibftdi"):
    mod = types.ModuleType(name)
    mod.__path__ = []
    for attr in ("TYPE_CLASS", "RECIP_OTHER", "CLASS_HUB", "REQ_CLEAR_FEATURE",
                 "REQ_SET_FEATURE", "PARITY_NONE", "STOPBITS_ONE", "EIGHTBITS"):
        setattr(mod, attr, 0)
    mod.busses = list
    mod.Serial = type("Serial", (), {"__init__": lambda self, *a, **k: None})
    mod.SerialException = type("SerialException", (Exception,), {})
    mod.core = mod
    mod.util = mod
    sys.modules.setdefault(name, mod)

from weewx_evo_weewx_driver import weewxnames, weewxshim
weewxnames.install(force=True)
weewxshim.import_driver("weewx.drivers." + sys.argv[2], sys.argv[1])
"""

#: The real `calculate_rain`, in a process where nothing has stood in for it.
#: Prints its answers as JSON, or exits non-zero if there is no WeeWX.
_RAIN_SCRIPT = r"""
import json, sys
try:
    import weewx.wxformulas as real
except ImportError as exc:
    print(exc)
    raise SystemExit(1)
if getattr(real, "__file__", None) is None:
    print("weewx.wxformulas has no file; something stood in for it")
    raise SystemExit(1)
print(json.dumps([real.calculate_rain(new, old)
                  for new, old in json.loads(sys.argv[1])]))
"""

#: Run in a subprocess with `weewx` and `weeutil` made unimportable, so that a
#: WeeWX on this machine cannot quietly satisfy the import being tested.
_BLOCKED_SCRIPT = r"""
import sys
sys.path.insert(0, "src")

# Refuse weewx and weeutil, however they are installed.
#
# `find_spec`, not `find_module`: the older pair was removed in Python 3.12,
# and a finder that only has them is skipped in silence. The first version of
# this had them, passed on a machine with no WeeWX, and measured nothing --
# which is what this whole test exists to catch.
class Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("weewx", "weeutil"):
            raise ImportError(f"{name} is blocked for this test")
        return None

sys.meta_path.insert(0, Blocked())

try:
    import weewx
except ImportError as exc:
    print(f"import weewx -> {exc}")
else:
    raise SystemExit("weewx imported anyway; the block did not work")

# Now the stand-in, and then the driver, which is the whole question.
sys.meta_path.pop(0)
import types
usb = types.ModuleType("usb")
usb.TYPE_CLASS, usb.RECIP_OTHER, usb.CLASS_HUB = 0x20, 0x03, 9
usb.REQ_CLEAR_FEATURE, usb.REQ_SET_FEATURE = 1, 3
usb.busses = lambda: []
sys.modules["usb"] = usb

from weewx_evo_weewx_driver import weewxnames, weewxshim
claimed = weewxnames.install(force=True)
print(f"stand-in claimed {len(claimed)} name(s)")

fousb = weewxshim.import_driver("weewx.drivers.fousb", sys.argv[1])
print(f"imported {fousb.DRIVER_NAME} {fousb.DRIVER_VERSION}")

raw = bytearray(32)
raw[0], raw[1] = 5, 48
raw[2], raw[3] = 213 & 0xFF, 213 >> 8
raw[4] = 61
raw[5], raw[6] = 152 & 0xFF, 152 >> 8
raw[7], raw[8] = 10132 & 0xFF, 10132 >> 8
raw[9], raw[10], raw[11], raw[12] = 32, 71, 0, 11
raw[13], raw[14] = 1420 & 0xFF, 1420 >> 8
raw[15] = 0
raw[16], raw[17], raw[18], raw[19] = 0x50, 0xC3, 0x00, 4

decoded = fousb._decode(raw, fousb.reading_format["3080"])
packet = fousb.pywws2weewx(decoded, 1787734265, 0.0, 1787733965, 24.0)
assert abs(decoded["temp_out"] - 15.2) < 1e-6, decoded["temp_out"]
assert packet["usUnits"] == 16, packet["usUnits"]
print(f"decoded outTemp {packet['outTemp']:.1f} C, usUnits {packet['usUnits']}")

# And that the driver class builds, which is what the shim does next. No
# hardware, so it gets as far as looking for the device and no further.
try:
    fousb.FineOffsetUSB()
except Exception as exc:
    print(f"building it reaches the hardware: {type(exc).__name__}")
else:
    print("built without hardware")
"""


if __name__ == "__main__":
    sys.exit(main())
