#!/usr/bin/env python3
"""The WeeWX driver shim: a driver runs, and what it produces arrives.

Needs WeeWX installed, because a shim tested against a fake WeeWX tests the
fake. The real simulator is loaded through the real `loader`, and the packets
go over a real listener into a real live table.

The check that matters most here is the one about `END_ARCHIVE_PERIOD`.
Vantage accumulates the loop gust across packets and zeroes it only in that
event; a shim that never fires it reports a gust that never falls, and by the
evening every packet carries the day's maximum. Nothing about the output looks
wrong -- which is why it is tested with a driver built the same way, and with
a clock that can be moved rather than waited on.

    python tools/shim_test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    import weewx
    import weewx.engine
except ImportError:  # pragma: no cover - reported by the runner
    print("WeeWX is not installed; this test compares against it.")
    sys.exit(0)

from weewx_evo.db.live import LiveStore  # noqa: E402
from weewx_evo.ingest.listener import HttpListener, Ingest  # noqa: E402

from weewx_evo_weewx_driver import weewxshim  # noqa: E402

TOKEN = "s" * 32
failures = 0


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


class Clock:
    """A clock that is moved rather than waited for.

    The archive boundary is wall-clock, so testing it by sleeping would mean a
    test that takes an archive interval to run or one that is flaky at the
    edges. Neither is worth it for arithmetic.
    """

    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def localtime(self, when: float | None = None):
        import time as real
        return real.localtime(self.now if when is None else when)

    def strftime(self, fmt: str, *rest):
        import time as real
        return real.strftime(fmt, *rest)


class GustDriver(weewx.engine.StdService):
    """Built the way Vantage is: a driver and a service at once.

    The gust only ever grows until `END_ARCHIVE_PERIOD` clears it, which is
    exactly Vantage's arrangement and exactly what a shim can silently break.
    """

    def __init__(self, engine, config_dict):
        weewx.engine.StdService.__init__(self, engine, config_dict)
        self.max_gust = 0.0
        self.speeds = [3.0, 9.0, 5.0, 2.0, 4.0, 1.0]
        self.at = 0
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        self.bind(weewx.END_ARCHIVE_PERIOD, self.end_archive_period)

    def new_loop_packet(self, event):
        speed = event.packet.get("windSpeed")
        if speed is not None and speed > self.max_gust:
            self.max_gust = speed
        event.packet["windGust"] = self.max_gust

    def end_archive_period(self, event):
        self.max_gust = 0.0

    def genLoopPackets(self):  # noqa: N802 - WeeWX's name
        for speed in self.speeds:
            yield {"dateTime": 1787800000 + self.at, "usUnits": weewx.METRIC,
                   "outTemp": 20.0, "windSpeed": speed}
            self.at += 1

    @property
    def hardware_name(self):
        return "GustBox"

    @property
    def archive_interval(self):
        return 60

    def closePort(self):  # noqa: N802 - WeeWX's name
        pass


def the_envelope() -> None:
    print("\na WeeWX record becomes our envelope")
    packet = weewxshim.to_packet(
        {"dateTime": 1787800000, "usUnits": weewx.METRIC, "outTemp": 21.5},
        "somewhere")
    check("dateTime moved out", packet.dateTime, 1787800000)
    check("usUnits moved out", packet.usUnits, weewx.METRIC)
    check("the reading stayed in", packet.data, {"outTemp": 21.5})
    # As the identity, which is what a collector naming itself means: the
    # listener looks that up in the station register like any PASSKEY. The
    # name it answers to is not written into the packet, because a name is a
    # lookup and freezing one there is what splits a series on a rename.
    check("the collector names itself", packet.identity, "somewhere")
    check("and its names are already ours", packet.dialect, None)

    # A record with no time would otherwise be stamped with the moment we
    # noticed it, which is a different measurement that looks identical.
    for missing, record in (("dateTime", {"usUnits": 1, "outTemp": 1.0}),
                            ("usUnits", {"dateTime": 1, "outTemp": 1.0})):
        try:
            weewxshim.to_packet(record, "x")
            check(f"a record with no {missing} is refused", "accepted", "refused")
        except ValueError:
            check(f"a record with no {missing} is refused", "refused", "refused")


def the_gust(clock: Clock) -> None:
    print("\nthe archive boundary is fired, so an accumulating gust resets")
    config_dict = {"StdArchive": {"archive_interval": "60"}}
    engine = weewxshim.ShimEngine(config_dict)
    console = GustDriver(engine, config_dict)
    check("the driver bound its callbacks", engine.bound(), 2)

    # Three seconds, so six packets one second apart cross a boundary with
    # room on either side. The interval is short here and 300s in the field;
    # what is being tested is that the boundary is noticed, not its length.
    interval = 3
    shim = weewxshim.Shim(config_dict, "tests.fake", dry_run=True)
    shim.console, shim.engine, shim.interval = console, engine, interval
    shim.source = "gustbox"

    # Computed, not guessed. The first attempt started two seconds before a
    # timestamp that was not on a boundary at all, so the clock never crossed
    # one and the test failed for a reason that had nothing to do with the
    # shim.
    clock.now = float((int(clock.now) // interval) * interval)
    weewxshim.time = clock  # type: ignore[assignment]

    gusts = []
    for packet in shim.generate():
        gusts.append(packet.data["windGust"])
        clock.now += 1.0

    print(f"       speeds {console.speeds}")
    print(f"       gusts  {gusts}")
    check("the gust rose with the wind", gusts[1], 9.0)
    check("it held while the wind dropped", gusts[2], 9.0)
    # Without END_ARCHIVE_PERIOD every one of these stays 9.0 to the end of
    # the run, and nothing about the numbers looks wrong.
    check("the boundary cleared it", gusts[3], 2.0)
    check("and it rose again after", gusts[4], 4.0)


def the_whole_way(clock: Clock) -> None:
    print("\nthe real WeeWX simulator, over the wire, into the live table")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live = LiveStore(tmp / "live.sdb", interval_seconds=300)
        listener = HttpListener(Ingest(live, token=TOKEN), "127.0.0.1", 0)
        listener.start()
        try:
            config_dict = {
                "Station": {"station_type": "Simulator"},
                "Simulator": {"loop_interval": "1", "mode": "simulator",
                              "driver": "weewx.drivers.simulator"},
                "StdArchive": {"archive_interval": "300"},
            }
            check("the driver module is read from [Station]",
                  weewxshim.driver_module_name(config_dict),
                  "weewx.drivers.simulator")

            shim = weewxshim.Shim(config_dict, host="127.0.0.1",
                                  port=listener.port, token=TOKEN,
                                  batch_seconds=0.0)
            sent = shim.run(limit=4)
            check("packets delivered", sent >= 4, True)

            stored = list(live.packets(0, 2_000_000_000))
            check("packets stored", len(stored) >= 4, True)
            one = stored[0]
            check("usUnits is WeeWX's own constant", one.usUnits, weewx.US)
            check("the source is the hardware's name", one.source, "Simulator")
            check("readings carried through", "outTemp" in one.data, True)
            print(f"       {len(one.data)} fields, outTemp="
                  f"{one.data.get('outTemp'):.2f}")
        finally:
            listener.stop()
            live.close()


def when_the_listener_is_away(clock: Clock) -> None:
    print("\npackets are held while the listener cannot be reached")
    shim = weewxshim.Shim({"StdArchive": {}}, "tests.fake",
                          host="127.0.0.1", port=1, token=TOKEN)
    shim.source = "held"
    made = [weewxshim.to_packet(
        {"dateTime": 1787800000 + n, "usUnits": 1, "outTemp": 1.0}, "held")
        for n in range(3)]

    check("delivery failed", shim.deliver(made), False)
    shim.hold(made)
    check("they are held rather than lost", len(shim.held), 3)
    check("nothing counted as sent", shim.sent, 0)

    # A listener that is gone for a day must not be paid for in memory.
    shim.held = []
    shim.hold([made[0]] * (weewxshim.MAX_HELD + 5))
    check("the hold is capped", len(shim.held), weewxshim.MAX_HELD)
    check("and says how many it dropped", shim.dropped, 5)


def a_backlog_bigger_than_one_request() -> None:
    """A drained hold is delivered in pieces, not in one body.

    The listener refuses a body over a megabyte. A full hold of packets with a
    console's field list is nearly twice that, so delivering it whole would be
    rejected -- and the delivery meant to drain the backlog would be the one
    the backlog made too big. It would then never drain.
    """
    print("\na backlog is delivered in pieces")
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        live = LiveStore(tmp / "live.sdb", interval_seconds=300)
        listener = HttpListener(Ingest(live, token=TOKEN), "127.0.0.1", 0)
        listener.start()
        try:
            shim = weewxshim.Shim({"StdArchive": {}}, "tests.fake",
                                  host="127.0.0.1", port=listener.port,
                                  token=TOKEN)
            # Wider than a real console's, so the body is unambiguously over
            # the limit if it were sent in one piece.
            wide = {f"field{n:02d}": 12.3456 for n in range(45)}
            count = weewxshim.PER_REQUEST * 2 + 7
            backlog = [
                weewxshim.to_packet({"dateTime": 1787800000 + n, "usUnits": 1,
                                     **wide}, "backlog")
                for n in range(count)
            ]
            # Measured in the shape `push` actually sends, not just the
            # readings: the first attempt at this measured `data` alone and
            # reported a body a third of its real size.
            import json
            body = json.dumps([{
                "dateTime": one.dateTime, "usUnits": one.usUnits,
                "source": one.source, "kind": one.kind,
                "interval": one.interval, "data": one.data,
            } for one in backlog])
            each = len(body) / count
            full = each * weewxshim.MAX_HELD
            print(f"       {each:.0f} bytes a packet, so a full hold of "
                  f"{weewxshim.MAX_HELD} is {full / 1024 / 1024:.2f} MB")
            check("a full hold would be over the listener's 1 MB limit",
                  full > (1 << 20), True)

            check("delivered anyway", shim.deliver(backlog), True)
            check("all of them counted", shim.sent, count)
            check("in more than one request", shim.delivered_batches > 1, True)
            stored = list(live.packets(0, 2_000_000_000))
            check("and all of them arrived", len(stored), count)
        finally:
            listener.stop()
            live.close()


def when_the_driver_throws(clock: Clock) -> None:
    print("\na driver that throws is built again rather than given up on")
    attempts = []

    class Broken(weewxshim.Shim):
        def open(self):
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError("the serial port went away")
            self.console = None
            self.engine = weewxshim.ShimEngine(self.config_dict)
            self.source = "recovered"
            self.interval = 60

        def _pump(self, once=False, limit=None):
            self.sent = 7
            self.stopping = True

        def close(self):
            pass

    shim = Broken({"StdArchive": {}}, "tests.fake", dry_run=True)
    shim._sleep = lambda seconds: None      # the backoff, without the wait
    sent = shim.run()
    check("it kept trying", len(attempts), 3)
    check("and ran once the hardware came back", sent, 7)


def the_command_line() -> None:
    """The commands, run the way a service file runs them.

    In a subprocess, through `python -m weewx_evo.cli`, because that is the
    entry point and reaching into the module does not exercise it. This is
    here because of a real failure: `cmd_weewx_driver_run` read `args.token`,
    which `add_listen_args` sets and `add_common` does not, so the command
    died with an AttributeError the first time it was run for real. No test
    walked that branch, and ruff cannot see through a Namespace.
    """
    import os
    import subprocess

    print("\nthe commands, run as a service file runs them")
    with tempfile.TemporaryDirectory() as raw:
        conf = Path(raw) / "weewx.conf"
        conf.write_text(
            "[Station]\n"
            "    station_type = Simulator\n"
            "[Simulator]\n"
            "    driver = weewx.drivers.simulator\n"
            "    loop_interval = 1\n"
            "    mode = simulator\n"
            "[StdArchive]\n"
            "    archive_interval = 300\n", encoding="utf-8")

        def run(args, token=None):
            env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
            env.pop("WEEWX_EVO_TOKEN", None)
            if token:
                env["WEEWX_EVO_TOKEN"] = token
            return subprocess.run(
                [sys.executable, "-m", "weewx_evo.cli", "weewx-driver", *args,
                 "--conf", str(conf)],
                capture_output=True, text=True, timeout=120, check=False,
                cwd=raw, env=env)

        done = run(["list"])
        check("list works", done.returncode, 0)
        check("and names the driver",
              "weewx.drivers.simulator" in done.stdout, True)

        done = run(["check", "--count", "2"])
        check("check works", done.returncode, 0)
        check("and reports the fields", "fields (" in done.stdout, True)

        # No configuration file anywhere -- the collector on another machine.
        done = run(["run", "--dry-run", "--limit", "2"], token="t" * 32)
        check("run works with the token only in the environment",
              done.returncode, 0)
        check("and delivers", "packet(s) delivered" in done.stdout, True)

        done = run(["run", "--dry-run", "--limit", "2"])
        check("without a token it refuses rather than running silently",
              done.returncode, 1)
        check("and says where to put one",
              "WEEWX_EVO_TOKEN" in done.stderr, True)


def main() -> int:
    # Two of these checks work by making something fail -- a listener that is
    # not there, a driver that throws -- and the shim says so at WARNING and
    # ERROR because in the field that is exactly right. Here it buries the
    # result under tracebacks of faults the test caused on purpose.
    import logging
    logging.disable(logging.CRITICAL)

    clock = Clock(1787800000.0)
    real_time = weewxshim.time
    try:
        the_envelope()
        the_gust(clock)
        weewxshim.time = real_time
        the_whole_way(clock)
        when_the_listener_is_away(clock)
        a_backlog_bigger_than_one_request()
        when_the_driver_throws(clock)
        the_command_line()
    finally:
        weewxshim.time = real_time

    print()
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("the shim runs a WeeWX driver and delivers what it produces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
