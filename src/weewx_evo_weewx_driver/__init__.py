#
#    Copyright (c) 2026 Manuel Hilgert
#
#    See the file LICENSE for your full rights.
#
"""Run a WeeWX driver as a weewx-evo collector.

Every driver written for WeeWX, unchanged: the fourteen in its own tree and
the hundred beside them, for hardware nobody here owns and nobody here can
test. A serial Vantage, a Fine Offset on the USB bus, an RTL-SDR listening to
whatever the neighbourhood transmits.

    weewx-evo-weewx-driver hardware          what is plugged in
    weewx-evo-weewx-driver check --conf ...  build it, take a few packets
    weewx-evo-weewx-driver run --collector shed

**WeeWX does not have to be installed.** A driver writes `import weewx` at
the top and that is nearly all ceremony -- three base classes, one exception,
one formula, three utility functions and two integers. `weewxnames.py` stands
in for them, measured across all thirteen in a process where `import weewx`
raises. Where WeeWX *is* installed it wins, the same rule `sun.py` has for
pyephem: whoever has the real thing gets its behaviour, edges included.

**Its own process, and that is the point.** In WeeWX the driver lives in the
engine, so a serial port that stops answering stops everything. Here it
delivers over the loopback like any other collector: it may hang, crash or
leak, and the listener and the archiver carry on. It need not even be on the
same machine.

## Why this is an add-on

2 100 lines of it, plus three simulated devices to test against. A station
with an Ecowitt on the wifi has no use for any of it, and the core ships no
driver at all -- so this is one more thing to install when it is what you
have, and absent when it is not.
"""

from __future__ import annotations

from weewx_evo.collectors import Kind

VERSION = "0.1.0"


def collector_kind() -> Kind:
    """What this contributes to the collector menu.

    The command is the whole command. It used to be a fragment the core put
    `weewx-evo` in front of, which was right while every kind was a
    subcommand of the core; an add-on brings its own console script, and a
    page that printed `weewx-evo weewx-driver run` would be telling somebody
    to type a command that does not exist.
    """
    from .options import options

    return Kind(
        "A WeeWX driver, running in its own process",
        "Any of the hundred-odd drivers written for WeeWX, unchanged -- a "
        "serial console, one on the USB bus, a radio. WeeWX itself does not "
        "have to be installed: point at the driver file and what it imports "
        "is stood in for.",
        "weewx-evo-weewx-driver run",
        options)


__all__ = ["VERSION", "collector_kind"]
