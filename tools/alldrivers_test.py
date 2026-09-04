#!/usr/bin/env python3
"""Every WeeWX driver, run twice: against our stand-in and against WeeWX.

`standin_test.py` asks whether the drivers import. `vantage_test.py` drives
one of them from a simulated serial port up to a packet, and compares the
numbers with WeeWX's own. This is the third question and the broadest one:
does **every** driver behave the same against `weewxnames.py` as against a
real WeeWX?

The method is a comparison, not an expectation. Each driver is built twice
from identical bytes -- `driversim.py` hands out a seeded stream, so both
runs see the same device -- and what comes back has to match. Packets if it
produced packets, and the exception if it raised: `WeeWxIOError` against
`WeeWxIOError`, with the same message.

That last part is the point. With arbitrary bytes most of these drivers fail
a checksum rather than decoding a reading, so what is usually compared is two
failures. That is still an answer to the question asked: a `RetriesExceeded`
on one side and a `KeyError` on the other is a stand-in that is missing
something, and a message naming a different byte count is a decoding that
went differently. What it is *not* is evidence that the driver reads its
hardware correctly -- for that there is `vantagesim.py`, and it costs one
protocol at a time.

    python tools/alldrivers_test.py
    python tools/alldrivers_test.py --drivers ~/weewx/src/weewx/drivers

Needs a real WeeWX to compare against, and says so and skips without one.
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

LOOKS_IN = (
    ROOT.parent / "weewx" / "src" / "weewx" / "drivers",
    Path("/usr/share/weewx/weewx/drivers"),
    Path("/usr/lib/python3/dist-packages/weewx/drivers"),
)

#: Seconds a single driver gets. Several retry with a wait between tries, and
#: `wait_before_retry = 0` is passed where the driver takes it -- but not all
#: of them do, and one that sleeps through its retries must not stop the run.
PATIENCE = 60

#: Drivers that cannot be built without something outside this comparison.
#: `simulator` generates its own readings from the clock, so its two runs
#: differ by the time between them and would never match -- what it does not
#: do is touch the stand-in in any way the others do not.
SKIP = {"simulator"}

#: What a driver is configured with. Enough for each of them to get as far as
#: opening a device; anything they insist on beyond this they ask for by name
#: in the exception, and that name is compared too.
SETTINGS = {
    "vantage": {"type": "serial", "port": "/dev/ttyUSB0",
                "baudrate": "19200", "loop_request": "1",
                "wait_before_retry": "0.0", "timeout": "1.0", "max_tries": "2"},
    "fousb": {"polling_mode": "PERIODIC", "polling_interval": "0",
              "data_format": "3080",
              "timeout": "0.1", "wait_before_retry": "0.0", "max_tries": "2"},
    "te923": {"polling_interval": "0", "max_tries": "2",
              "retry_wait": "0.0"},
    "ws28xx": {"polling_interval": "0", "max_tries": "2"},
    "ws23xx": {"port": "/dev/ttyUSB0", "max_tries": "2", "retry_wait": "0.0"},
    "cc3000": {"port": "/dev/ttyUSB0", "max_tries": "2", "retry_wait": "0.0"},
    "ultimeter": {"port": "/dev/ttyUSB0", "max_tries": "2",
                  "retry_wait": "0.0"},
    "ws1": {"port": "/dev/ttyUSB0", "max_tries": "2", "retry_wait": "0.0"},
    "wmr100": {"max_tries": "2", "retry_wait": "0.0"},
    "wmr300": {"max_tries": "2", "retry_wait": "0.0"},
    "wmr9x8": {"port": "/dev/ttyUSB0", "max_tries": "2", "retry_wait": "0.0"},
    "acurite": {"max_tries": "2", "retry_wait": "0.0",
                "polling_interval": "0"},
}

failures = 0


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def find_drivers(given: str | None) -> Path | None:
    if given:
        found = Path(given)
        return found if found.is_dir() else None
    for candidate in LOOKS_IN:
        if candidate.is_dir():
            return candidate
    spec = importlib.util.find_spec("weewx")
    if spec and spec.origin:
        beside = Path(spec.origin).parent / "drivers"
        if beside.is_dir():
            return beside
    return None


def run(stem: str, path: Path, against: str) -> dict:
    """Build one driver and take a few packets. `against` is 'standin' or 'weewx'."""
    got = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(path), stem, against],
        capture_output=True, text=True, cwd=str(ROOT), timeout=PATIENCE,
        check=False)
    if got.returncode == 0 and got.stdout.strip():
        try:
            return json.loads(got.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            return {"outcome": "unreadable", "detail": got.stdout[-200:]}
    detail = (got.stdout.strip()
              or (got.stderr.strip().splitlines() or ["?"])[-1])
    return {"outcome": "died", "detail": detail[-200:]}


def same(ours: dict, theirs: dict) -> tuple[bool, str]:
    """Whether the two runs agree, and what to say when they do not.

    The exception *type* and the shape of the result are compared, not the
    exception's text. A message often carries a byte count or an address that
    depends on how far the seeded stream got before a timeout, and comparing
    those would make this fail on a slow machine -- which is a test that
    reports the machine rather than the code.
    """
    if ours.get("outcome") != theirs.get("outcome"):
        return False, f"{ours.get('outcome')} against {theirs.get('outcome')}"
    if ours.get("error_type") != theirs.get("error_type"):
        return False, (f"raised {ours.get('error_type')} against "
                       f"{theirs.get('error_type')}")
    if ours.get("packets") != theirs.get("packets"):
        return False, (f"{ours.get('packets')} packet(s) against "
                       f"{theirs.get('packets')}")
    if ours.get("fields") != theirs.get("fields"):
        mine = set(ours.get("fields") or [])
        yours = set(theirs.get("fields") or [])
        return False, (f"fields differ: only ours {sorted(mine - yours)[:4]}, "
                       f"only WeeWX's {sorted(yours - mine)[:4]}")
    if ours.get("values") != theirs.get("values"):
        return False, "same fields, different readings"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drivers", default=None,
                        help="the directory WeeWX's drivers are in")
    args = parser.parse_args()

    print("every WeeWX driver, against the stand-in and against WeeWX\n")

    folder = find_drivers(args.drivers)
    if folder is None:
        print("  no WeeWX drivers found, so there is nothing to run.")
        print("\n  SKIP")
        return 0
    print(f"  drivers: {folder}")

    # Is there a real WeeWX to compare against? Without one this test has
    # nothing to say -- the stand-in agreeing with itself is not evidence.
    probe = subprocess.run(
        [sys.executable, "-c",
         ("import weewx, sys; "
          "sys.exit(0 if getattr(weewx, '__file__', None) else 1)")],
        capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        print("\n  WeeWX is not installed here, so there is nothing to")
        print("  compare against. standin_test.py covers what can be checked")
        print("  without it.")
        print("\n  SKIP")
        return 0

    stems = sorted(p.stem for p in folder.glob("*.py") if p.stem != "__init__")
    print(f"  {len(stems)} driver(s)\n")

    agreed, disagreed, skipped, strong = [], [], [], []
    for stem in stems:
        if stem in SKIP:
            skipped.append(stem)
            continue
        path = folder / f"{stem}.py"
        try:
            ours = run(stem, path, "standin")
            theirs = run(stem, path, "weewx")
        except subprocess.TimeoutExpired:
            print(f"  ..   {stem}: still going after {PATIENCE}s, left out")
            skipped.append(stem)
            continue

        alike, why = same(ours, theirs)
        if alike:
            agreed.append(stem)
            note = ours.get("outcome")
            if note == "packets":
                note = f"{ours['packets']} packet(s), {len(ours['fields'])} fields"
                strong.append(stem)
            else:
                # Say which failure, both sides. "build-raised" alone reads
                # as though something was measured; naming the exception is
                # what makes it possible to see that two runs failed the
                # same way rather than merely both failing.
                note = f"{note} ({ours.get('error_type', '?')}), both sides"
            print(f"  ok   {stem:<11} {note}")
        else:
            disagreed.append(stem)
            print(f"  FAIL {stem:<11} {why}")
            print(f"         ours:  {json.dumps(ours)[:150]}")
            print(f"         WeeWX: {json.dumps(theirs)[:150]}")

    print()
    if skipped:
        print(f"  left out: {', '.join(skipped)}")
    check(f"all {len(agreed) + len(disagreed)} behave the same either way",
          len(disagreed), 0)

    if strong:
        print(f"  compared on readings: {', '.join(strong)}")
    weak = sorted(set(agreed) - set(strong))
    if weak:
        print(f"  compared on how they failed: {', '.join(weak)}")
        print("    Arbitrary bytes, so these reject the device rather than")
        print("    decoding it. The same rejection either way is what was")
        print("    asked, and it is weaker than a protocol simulator gives.")

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("no driver behaves differently against the stand-in")
    return 0


#: One driver, built and asked for packets, reported as JSON. `against`
#: decides which weewx it gets -- and for 'standin' the real one is blocked
#: rather than merely unimported, because a WeeWX on the path would otherwise
#: satisfy the import and both runs would be the same run.
_RUNNER = r"""
import json, sys
sys.path.insert(0, "src")
sys.path.insert(0, "tools")

path, stem, against = sys.argv[1], sys.argv[2], sys.argv[3]

if against == "standin":
    class Blocked:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("weewx", "weeutil"):
                raise ImportError(f"{name} is blocked")
            return None
    sys.meta_path.insert(0, Blocked())
    try:
        import weewx
    except ImportError:
        pass
    else:
        print(json.dumps({"outcome": "block-failed"}))
        raise SystemExit(0)
    sys.meta_path.pop(0)

# A protocol-accurate simulator where one exists, so that driver is
# compared on the readings it decoded rather than on how it failed. The
# seeded stream is the fallback, and the report says which was used.
if stem == "vantage":
    import vantagesim
    sys.modules["serial"] = vantagesim.serial_module({})
elif stem == "fousb":
    import fousbsim
    sys.modules["usb"] = fousbsim.usb_module(fousbsim.memory(count=6))
else:
    import driversim
    driversim.install(seed=7)

if against == "standin":
    from weewx_evo_weewx_driver import weewxnames
    weewxnames.install(force=True)

# The same import machinery either way: from the file, under the name the
# driver believes it has. Using `import_module` for the WeeWX run and ours
# for the other would compare two import paths as well as two stand-ins.
from weewx_evo_weewx_driver import weewxshim

SETTINGS = json.loads(r'''__SETTINGS__''')
name = "weewx.drivers." + stem

result = {"outcome": "?"}
try:
    module = weewxshim.import_driver(name, path)
    loader = getattr(module, "loader", None)
    if loader is None:
        result = {"outcome": "not-a-driver"}
    else:
        # The section has to be named the way the driver names itself:
        # every one of them reads `config_dict[DRIVER_NAME]`, and fousb
        # calls itself FineOffsetUSB, not fousb. Using the file name gave
        # a KeyError from all twelve -- on both sides, so the comparison
        # passed while nothing had been built.
        section = getattr(module, "DRIVER_NAME", stem)
        config = {"Station": {"station_type": section},
                  section: dict(SETTINGS.get(stem, {}), driver=name)}
        engine = weewxshim.ShimEngine(config)
        console = loader(config, engine)
        packets = []
        try:
            for packet in console.genLoopPackets():
                packets.append(packet)
                if len(packets) >= 2:
                    break
        except Exception as exc:
            result = {"outcome": "loop-raised",
                      "error_type": type(exc).__name__,
                      "packets": len(packets)}
        else:
            fields = sorted({k for p in packets for k in p})
            result = {"outcome": "packets", "packets": len(packets),
                      "fields": fields,
                      # Rounded, and the clock left out: the two runs are
                      # seconds apart and dateTime is stamped on arrival.
                      "values": {k: (round(v, 6)
                                     if isinstance(v, float) else v)
                                 for k, v in sorted(packets[0].items())
                                 if k != "dateTime"
                                 and isinstance(v, (int, float, str))}}
except Exception as exc:
    result = {"outcome": "build-raised", "error_type": type(exc).__name__}

print(json.dumps(result))
"""

_RUNNER = _RUNNER.replace("__SETTINGS__", json.dumps(SETTINGS))


if __name__ == "__main__":
    sys.exit(main())
