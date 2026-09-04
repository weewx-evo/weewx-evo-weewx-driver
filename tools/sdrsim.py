"""An rtl_433 that is not there, for the SDR driver.

The third simulator, and the odd one out. `vantagesim.py` and `fousbsim.py`
stand in for hardware on a bus; weewx-sdr has no bus. It starts `rtl_433` as
a child process and reads its standard output, so what has to be stood in for
is a *program*.

Which makes this the most honest of the three: no fake port, no patched
module. The driver is told to run a command, and the command is a real Python
process printing real rtl_433 output. Everything the driver does with it --
the reader threads, the queue, the line buffering, the pattern matching, the
sensor map -- runs exactly as it does against a radio.

The lines are rtl_433's own, taken from the comments in the driver where its
author recorded what each sensor emits. Both formats:

    text  2026-05-14 09:00:04 Acurite 5n1 sensor 0x0BFA Ch C, Msg 31, ...
    json  {"time" : "...", "model" : "Acurite-5n1", "temperature_C" : 20.5}

**The one constraint, and it comes from the driver.** It runs
`cmd.split(' ')`, so neither the interpreter's path nor the script's may
contain a space. That rules the test out on a stock Windows Python, whose
executable lives under "Program Files" -- `runnable()` says so and the test
skips rather than reporting a failure that is about the path.

**What this is and is not asking.** Not whether weewx-sdr reads a radio:
whether rtl_433 is installed, whether the dongle is tuned, whether the sensor
it hears is two gardens away -- all of that is the same under WeeWX, because
it is the same driver reading the same program. The question is whether it
behaves *differently* on our stand-in, and both sides run this same child
process.

Used by `tools/sdr_test.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

#: rtl_433's text output, as the driver's author recorded it. An Acurite 5n1:
#: wind and rain in message 31, wind, temperature and humidity in message 38.
TEXT_LINES = [
    ("2026-05-14 09:00:04 Acurite 5n1 sensor 0x0BFA Ch C, Msg 31, "
     "Wind 15 kmph / 9.3 mph 270.0^ W (3), rain gauge 0.00 in"),
    ("2026-05-14 09:00:22 Acurite 5n1 sensor 0x0BFA Ch C, Msg 38, "
     "Wind 2 kmph / 1.2 mph, 21.3 C 70.3 F 70 % RH"),
]

#: And its JSON output, which newer builds emit and the driver prefers.
JSON_LINES = [
    {"time": "2026-05-14 09:00:04", "model": "Acurite-5n1", "subtype": 56,
     "id": 956, "channel": "A", "sequence_num": 2, "battery_ok": 1,
     "wind_avg_km_h": 3.483, "temperature_F": 31.3, "humidity": 66},
    {"time": "2026-05-14 09:00:22", "model": "Acurite-Tower", "id": 2179,
     "channel": "A", "battery_ok": 1, "temperature_C": 15.2, "humidity": 61},
]


#: The stand-in rtl_433 itself. A plain string with two placeholders rather
#: than an f-string: it is full of `%` formatting and `{}`, and every one of
#: them would have to be doubled -- which is how the first version of it came
#: out as a syntax error inside the child, reported as a driver that would
#: not start.
_STUB = """import re, sys, time

LINES = __LINES__
STAMP = re.compile(r"(\\d\\d):(\\d\\d):(\\d\\d)")


def later(line, by):
    def bump(m):
        s = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
             + int(m.group(3)) + by)
        return "%02d:%02d:%02d" % (s // 3600 % 24, s // 60 % 60, s % 60)
    return STAMP.sub(bump, line, count=1)


for round_ in range(__REPEAT__):
    for line in LINES:
        sys.stdout.write(later(line, round_ * 60) + chr(10))
        sys.stdout.flush()
    time.sleep(0.02)
time.sleep(120)
"""


def runnable() -> str | None:
    """Why this cannot run here, or None if it can.

    The driver splits its command on spaces before handing it to Popen, so a
    path with one in it becomes two arguments and the process never starts.
    Worth saying rather than discovering: the failure looks like a broken
    driver and is a Windows install path.
    """
    if " " in sys.executable:
        return f"the interpreter's path has a space in it: {sys.executable}"
    return None


def write_stub(folder: Path, kind: str = "json", repeat: int = 40) -> str:
    """Write a stand-in rtl_433 and return the command to run it.

    A real child process, because that is what the driver starts and what its
    reader threads read from. Patching `subprocess.Popen` would leave those
    threads out of the test, and they are where a driver of this shape goes
    wrong: a queue that fills, a line arriving in two pieces, a reader that
    will not stop.

    It repeats and then goes quiet rather than exiting, because a driver
    whose child has died takes a different path -- worth its own test, and
    not this one.
    """
    lines = [json.dumps(one) for one in JSON_LINES] if kind == "json" \
        else list(TEXT_LINES)

    # Every repeat carries a later timestamp, and that is not decoration. The
    # driver drops a packet identical to the last one -- same readings, same
    # time -- because a radio hears the same transmission more than once.
    # Printing one line over and over yields exactly one packet and then a
    # debug log per line for ever, which is what the first version did.
    script = folder / "rtl_433_stub.py"
    script.write_text(
        _STUB.replace("__LINES__", json.dumps(lines))
             .replace("__REPEAT__", str(repeat)),
        encoding="utf-8")

    if " " in str(script):
        raise ValueError(f"the stub's path has a space in it: {script}")
    return f"{sys.executable} {script}"


def sensor_map() -> dict:
    """A map from the simulated sensors onto schema names.

    The driver hands over nothing at all without one: every reading is keyed
    `<observation>.<hardware id>.<packet type>` and dropped unless a pattern
    here claims it. A test that left this out would see empty packets and
    read them as a broken stand-in.
    """
    return {
        "outTemp": "temperature.*.Acurite5n1PacketV2",
        "outHumidity": "humidity.*.Acurite5n1PacketV2",
        "windSpeed": "wind_speed.*.Acurite5n1PacketV2",
        "inTemp": "temperature.*.AcuriteTowerPacket",
        "inHumidity": "humidity.*.AcuriteTowerPacket",
    }
