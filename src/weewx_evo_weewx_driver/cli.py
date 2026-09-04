#
#    Copyright (c) 2026 Manuel Hilgert
#
#    See the file LICENSE for your full rights.
#
"""The command line: `weewx-evo-weewx-driver`.

Its own command rather than a subcommand of `weewx-evo`, because an add-on
cannot reach into another package's argument parser -- and should not: a core
that had to be told about every add-on's commands would be a core that knows
what add-ons exist.

The five subcommands are what they were, verbatim, and they take the same
arguments. `add_common` is imported from the core so that `--config`, `--live`
and the rest mean exactly what they mean everywhere else; a second copy of
that list would drift on the first setting added to it.

    weewx-evo-weewx-driver hardware
    weewx-evo-weewx-driver list --conf /etc/weewx/weewx.conf
    weewx-evo-weewx-driver check --driver-file ./vantage.py
    weewx-evo-weewx-driver run --collector shed
"""

from __future__ import annotations

import argparse
import sys

from weewx_evo.cli import add_common

from .commands import (
    cmd_weewx_driver_check,
    cmd_weewx_driver_hardware,
    cmd_weewx_driver_install,
    cmd_weewx_driver_list,
    cmd_weewx_driver_run,
)


def parser() -> argparse.ArgumentParser:
    """Every subcommand, with the arguments the core's version had."""
    parser = argparse.ArgumentParser(
        prog="weewx-evo-weewx-driver",
        description="Run a WeeWX driver and deliver what it produces to a "
                    "weewx-evo listener. WeeWX does not have to be installed.")
    shim_sub = parser.add_subparsers(dest="shim_command", required=True)

    def add_shim_common(q):
        add_common(q)
        q.add_argument("--collector", default=None,
                       help="a collector named in the configuration file. Its "
                            "settings are used, and -- the part that matters "
                            "-- its packets arrive under its own name, so a "
                            "station can be announced for it.")
        q.add_argument("--conf", default=None,
                       help="the weewx.conf the driver is configured by. "
                            "/etc/weewx/weewx.conf by default.")
        q.add_argument("--driver", default=None,
                       help="the driver module, if not the one [Station] "
                            "names. For example weewx.drivers.vantage.")
        q.add_argument("--driver-file", default=None,
                       help="load the driver from this file rather than from "
                            "an installed WeeWX. A driver is one file, and "
                            "what it imports is stood in for -- so one USB "
                            "console no longer means installing all of WeeWX.")
        q.add_argument("--source", default=None,
                       help="what to record these readings under. The "
                            "driver's own hardware name by default.")

    q = shim_sub.add_parser("list", help="what the weewx.conf asks for")
    add_shim_common(q)
    q.set_defaults(func=cmd_weewx_driver_list)

    q = shim_sub.add_parser("hardware",
                            help="every WeeWX driver on this machine")
    add_common(q)
    q.add_argument("name", nargs="?", default=None,
                   help="one driver, to see every setting it takes")
    # Not `--driver-dir`: that one is already taken, by the directory the
    # core's own drivers live in. Two directories behind one flag holds right
    # up until somebody puts a file in the wrong one, and then nothing says so.
    q.add_argument("--weewx-driver-dir", default=None,
                   help="where WeeWX driver files are kept. Beside the "
                        "archive database by default.")
    q.set_defaults(func=cmd_weewx_driver_hardware)

    q = shim_sub.add_parser("install",
                            help="put a WeeWX driver file where this looks")
    add_common(q)
    q.add_argument("source",
                   help="the driver file: a path, or an http(s) URL to one")
    q.add_argument("--weewx-driver-dir", default=None,
                   help="where to put it. Beside the archive database by "
                        "default.")
    q.add_argument("--force", action="store_true",
                   help="replace one that is already there")
    q.set_defaults(func=cmd_weewx_driver_install)

    q = shim_sub.add_parser("check", help="build it, take a few packets, "
                                          "deliver nothing")
    add_shim_common(q)
    q.add_argument("--count", type=int, default=3,
                   help="how many packets to wait for")
    q.set_defaults(func=cmd_weewx_driver_check)

    q = shim_sub.add_parser("run", help="deliver to the listener, until stopped")
    add_shim_common(q)
    q.add_argument("--host", default=None,
                   help="the listener's address. Loopback by default, which "
                        "is where it is when both run on one machine.")
    q.add_argument("--port", type=int, default=None,
                   help="the listener's port")
    q.add_argument("--batch", type=float, default=5.0,
                   help="seconds of packets per delivery. One request per "
                        "loop packet is a round trip every two seconds for "
                        "readings that are aggregated at the end of the "
                        "interval anyway.")
    q.add_argument("--catchup", type=int, default=None,
                   help="seconds of the console's own logged records to "
                        "fetch at startup, for hardware that keeps them. "
                        "This is how an outage becomes a filled gap instead "
                        "of a lost one.")
    q.add_argument("--limit", type=int, default=None,
                   help="stop after this many packets. For trying it out.")
    q.add_argument("--dry-run", action="store_true",
                   help="run the driver and deliver nothing.")
    q.set_defaults(func=cmd_weewx_driver_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
