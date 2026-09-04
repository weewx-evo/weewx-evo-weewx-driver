"""Run a WeeWX driver, and hand what it produces to the listener.

WeeWX has fifteen years of drivers: fourteen in its own tree and something
like a hundred outside it, for hardware nobody here owns and cannot test
against. Rewriting them is not the work; *having* them is. This runs them
unchanged.

    weewx-evo weewx-driver run --conf /etc/weewx/weewx.conf

What makes that safe is the process boundary. In WeeWX the driver lives
inside the engine, so a serial port that stops answering stops everything.
Here the driver is in its own process and delivers over the loopback like any
other collector: it can wedge, crash, leak or spin, and the listener, the
archiver and every other driver carry on. It does not even have to be on the
same machine.

**Where this taps in, and why there.** WeeWX's engine dispatches a loop packet
through four groups of services before it reaches the accumulator:

    genLoopPackets()
      -> prep     StdTimeSynch
      -> process  StdConvert, StdCalibrate, StdQC, StdWXCalculate
      -> xtype    StdWXXTypes, StdPressureCooker, StdRainRater, StdDelta
      -> archive  StdArchive

None of that is reproduced here, because we already have it and would only be
running it twice: `units.py` is StdConvert, `derive.py` is StdWXCalculate and
the four xtype services together, `archiver.py` is StdArchive. The raw packet
is the cleanest thing to take, and taking it is the whole job. (StdCalibrate
and StdQC have no counterpart here yet -- `weewxconf.py` says so where it
reports what an import left behind.)

**What a driver may ask of the engine.** One method: `bind`. Measured over
WeeWX's fourteen own drivers, exactly one -- Vantage -- touches the engine at
all, because it is a driver and a service at once. The events are really
dispatched rather than swallowed, and that is not politeness: Vantage puts
the loop gust into the packet from inside `NEW_LOOP_PACKET`, and zeroes it
only in `END_ARCHIVE_PERIOD`. A shim that skipped the second event would
report a gust that never falls -- by evening, the day's maximum, in every
packet, with nothing anywhere looking wrong.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

from weewx_evo.db.live import Packet

log = logging.getLogger(__name__)

#: How long to wait before building the driver again after it threw. The
#: hardware is usually the reason -- a USB adapter re-enumerating, a serial
#: converter pulled out -- and those come back on their own or not at all.
RETRY_FIRST = 5.0
RETRY_MAX = 300.0

#: Packets held while the listener cannot be reached. An hour at a two-second
#: loop interval, and then the oldest go: a collector that cannot deliver must
#: not grow until it is killed for the memory it holds.
MAX_HELD = 1800

#: Packets per request. The listener refuses a body over `MAX_BODY`, which is
#: 1 MB, and a packet with a console's full field list is about a kilobyte of
#: JSON -- so a full hold delivered in one piece would be around 1.7 MB and be
#: rejected. Held packets would then be held for ever: the delivery that is
#: meant to drain the backlog is the one the backlog makes too big.
PER_REQUEST = 250


class ShimEngine:
    """Everything a driver may ask of the engine, and nothing more.

    Deliberately not a `__getattr__` that answers anything. An object that
    replies to every name is how a driver ends up with a mock where it wanted
    a database, and the failure lands somewhere else entirely -- the same trap
    `Tags` has in the skin layer, where answering everything swallowed the
    template's own imports. Here an attribute that is missing raises, and the
    name in the traceback is the thing to add.
    """

    def __init__(self, config_dict: dict[str, Any]) -> None:
        self.config_dict = config_dict
        self.callbacks: dict[Any, list[Any]] = {}
        #: The engine sets this to the driver once it is built. Vantage reads
        #: `engine.console` from inside its own startup.
        self.console: Any = None
        #: Named so the failure is a clear one. A driver that wants the
        #: database is asking for something a collector in another process
        #: cannot have, and `None` lets it get further before saying so.
        self.db_binder = None
        self.stn_info = None

    def bind(self, event_type: Any, callback: Any) -> None:
        self.callbacks.setdefault(event_type, []).append(callback)

    def dispatchEvent(self, event: Any) -> None:  # noqa: N802 - WeeWX's name
        for callback in self.callbacks.get(event.event_type, []):
            callback(event)

    def bound(self) -> int:
        return sum(len(one) for one in self.callbacks.values())


def read_config(path: str | Path) -> dict[str, Any]:
    """A weewx.conf, in the shape its own drivers expect.

    ConfigObj when it is installed, which it is wherever WeeWX is: the drivers
    were written against it, and it decides things our reader does not have to
    -- that a comma makes a list, that `interpolation` expands `%(x)s`. A
    driver handed a plain dict where it expected a Section is a bug report
    about us, for a line we did not write.
    """
    try:
        import configobj
    except ImportError:
        from weewx_evo import weewxconf
        log.warning("configobj is not installed; reading %s with our own "
                    "parser, which does not expand interpolation", path)
        return weewxconf.read(path)
    return configobj.ConfigObj(str(path), file_error=True, encoding="utf-8")


def driver_module_name(config_dict: dict[str, Any]) -> str:
    """Which driver a weewx.conf asks for.

    `[Station] station_type` names a section, and that section names the
    module. Two hops rather than one, because the section is also where the
    driver's own settings live.
    """
    station = config_dict.get("Station") or {}
    station_type = station.get("station_type")
    if not station_type:
        raise ValueError("no [Station] station_type in the configuration")
    section = config_dict.get(station_type) or {}
    module = section.get("driver")
    if not module:
        raise ValueError(f"[{station_type}] has no driver = ...")
    return str(module)


def import_driver(module_name: str, path: str | Path | None = None) -> Any:
    """The driver module, from a package or from one file.

    A path rather than a package because a driver is a *file*. Somebody with
    one USB console had to install the whole of WeeWX to get at
    `weewx/drivers/fousb.py`, and with `weewxnames` standing in for the
    import at the top of it, there is nothing left that the installation was
    for. The module still goes into `sys.modules` under the name it thinks it
    has: several of them look themselves up there.
    """
    import importlib

    if path is None:
        return importlib.import_module(module_name)

    import importlib.util

    found = Path(path)
    if not found.is_file():
        raise FileNotFoundError(f"no driver file at {found}")
    spec = importlib.util.spec_from_file_location(module_name, found)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load a driver from {found}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A half-executed module left under its name is worse than none: the
        # next import finds it, believes it, and fails somewhere else.
        sys.modules.pop(module_name, None)
        raise

    # And onto the parent package as an attribute. `import_module` does this
    # and `exec_module` does not, so without it `weewx.drivers.vantage` is in
    # sys.modules while `weewx.drivers.vantage` as an *expression* raises
    # AttributeError -- which is how the driver refers to its own logger, and
    # how its tests reach it.
    if "." in module_name:
        parent, child = module_name.rsplit(".", 1)
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)
    return module


def load(module_name: str, config_dict: dict[str, Any],
         path: str | Path | None = None) -> tuple[Any, ShimEngine]:
    """Build the driver the way WeeWX's engine builds it.

    Same two lines as `StdEngine.setupStation`: import the module, call its
    `loader` with the configuration and the engine. Anything else -- a class
    picked out by name, a constructor called directly -- would work for some
    drivers and not for others, and the ones it failed for would be the
    unusual ones nobody here can test.

    The stand-in goes in first, and only where WeeWX is not installed. It has
    to be before the import rather than after a failure: `import weewx` at
    the top of a driver is the first line to run, so catching the error means
    catching it halfway through somebody else's module.
    """
    from . import weewxnames

    weewxnames.install()

    module = import_driver(module_name, path)
    loader = getattr(module, "loader", None)
    if loader is None:
        raise TypeError(f"{module_name} has no loader(); it is not a WeeWX driver")

    engine = ShimEngine(config_dict)
    console = loader(config_dict, engine)
    engine.console = console
    return console, engine


def archive_interval_of(console: Any, config_dict: dict[str, Any]) -> int:
    """Seconds between archive periods, for the event that ends one.

    The hardware's own interval wins where it has one -- that is the period
    the console is actually keeping records over. `archive_interval` is
    documented as raising when the driver does not support hardware records,
    so the exception is the answer rather than a fault.
    """
    try:
        found = console.archive_interval
        if found:
            return int(found)
    except (NotImplementedError, AttributeError):
        pass
    try:
        return int((config_dict.get("StdArchive") or {}).get("archive_interval", 300))
    except (TypeError, ValueError):
        return 300


def to_packet(record: dict[str, Any], source: str, kind: str = "loop",
              interval: float | None = None) -> Packet:
    """A WeeWX record, in our envelope.

    Two keys move to the outside and the rest is the reading. That the shapes
    line up this exactly is not luck: the field names and the `usUnits`
    constants are WeeWX's because keeping a WeeWX database readable meant
    adopting them, so this is the one rule paying out a second time.
    """
    data = dict(record)
    when = data.pop("dateTime", None)
    units = data.pop("usUnits", None)
    if when is None:
        # Timestamping it here would date the reading to when we noticed it,
        # which is a different measurement and looks identical afterwards.
        raise ValueError("the driver produced a record with no dateTime")
    if units is None:
        raise ValueError("the driver produced a record with no usUnits")
    return Packet(dateTime=int(when), usUnits=int(units), data=data,
                  identity=source, kind=kind, interval=interval)


class Shim:
    """One WeeWX driver, running, delivering to a listener.

    Built and torn down around the driver rather than holding it for the
    process's life: coming back from a driver that threw means building it
    again, and a driver that is half torn down is the thing that hangs on the
    port next time.
    """

    def __init__(self, config_dict: dict[str, Any], module_name: str | None = None,
                 source: str | None = None, host: str = "127.0.0.1",
                 port: int = 8000, token: str | None = None,
                 batch_seconds: float = 5.0, catchup_seconds: int = 0,
                 dry_run: bool = False,
                 driver_file: str | Path | None = None,
                 as_driver: str = "json") -> None:
        self.config_dict = config_dict
        self.module_name = module_name or driver_module_name(config_dict)
        self.driver_file = driver_file
        #: The endpoint these packets go in at, which is the
        #: driver name they are recorded under. A configured
        #: collector uses its own; an ad-hoc run is `json`.
        self.as_driver = as_driver
        self.source = source
        self.host = host
        self.port = port
        self.token = token
        self.batch_seconds = batch_seconds
        self.catchup_seconds = catchup_seconds
        self.dry_run = dry_run

        self.console: Any = None
        self.engine: ShimEngine | None = None
        self.interval = 300
        self.sent = 0
        self.delivered_batches = 0
        self.held: list[Packet] = []
        self.dropped = 0
        self.stopping = False

    # -- the driver ----------------------------------------------------

    def open(self) -> None:
        # `load` first: it installs the stand-in, and without that this
        # import is the one that fails on a machine with no WeeWX -- before
        # anything has had the chance to stand in for it.
        self.console, self.engine = load(self.module_name, self.config_dict,
                                         self.driver_file)
        import weewx

        self.interval = archive_interval_of(self.console, self.config_dict)
        if self.source is None:
            try:
                self.source = str(self.console.hardware_name)[:64]
            except (NotImplementedError, AttributeError):
                self.source = self.module_name.rsplit(".", 1)[-1][:64]
        # Services bind to this in their constructor and expect it before the
        # first packet; Vantage clears its gust here.
        self.engine.dispatchEvent(weewx.Event(weewx.STARTUP))
        log.info("driver %s (%s), archive interval %ss, %d callback(s) bound",
                 self.module_name, self.source, self.interval,
                 self.engine.bound())

    def close(self) -> None:
        console, self.console = self.console, None
        if console is None:
            return
        try:
            console.closePort()
        except Exception:
            # Nothing useful is left to do about it, and raising here would
            # replace whatever sent us into the shutdown.
            log.exception("closePort failed")

    # -- what comes out of it ------------------------------------------

    def startup_records(self) -> list[Packet]:
        """What the console logged while nothing was listening.

        This is the reason a Vantage is worth the trouble: the console keeps
        its own records, so an outage is a gap that can be filled rather than
        one that is simply lost. They go over as `kind="archive"`, which the
        archiver already treats as the better answer -- computed from readings
        we never saw, at a resolution we cannot match.

        Off unless asked for. How far back to reach is a decision with a cost
        at both ends, and guessing it would mean either re-sending a week or
        quietly filling nothing.
        """
        if not self.catchup_seconds:
            return []
        since = int(time.time()) - int(self.catchup_seconds)
        made: list[Packet] = []
        try:
            for record in self.console.genStartupRecords(since):
                made.append(to_packet(record, self.source or "weewx",
                                      kind="archive", interval=self.interval))
        except NotImplementedError:
            log.info("%s keeps no records of its own; nothing to catch up",
                     self.module_name)
        except Exception:
            log.exception("could not read the console's own records; "
                          "carrying on with live readings")
        if made:
            log.info("%d record(s) the console had logged since %s",
                     len(made), time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(since)))
        return made

    def generate(self):
        """Loop packets, with the events a driver-that-is-a-service needs.

        `END_ARCHIVE_PERIOD` is fired on the archive boundary and is not
        decoration: Vantage accumulates the loop gust across packets and zeroes
        it only there. Without it `windGust` never falls again -- by evening it
        is the day's maximum, in every packet, and nothing about the output
        looks wrong.
        """
        import weewx

        boundary = self._next_boundary()
        for record in self.console.genLoopPackets():
            if self.stopping:
                return
            now = time.time()
            if now >= boundary:
                self.engine.dispatchEvent(weewx.Event(weewx.END_ARCHIVE_PERIOD))
                boundary = self._next_boundary(now)
            self.engine.dispatchEvent(
                weewx.Event(weewx.NEW_LOOP_PACKET, packet=record))
            yield to_packet(record, self.source or "weewx")

    def _next_boundary(self, now: float | None = None) -> float:
        """The next wall-clock archive boundary.

        On the clock rather than `now + interval`, so the period the driver
        thinks it is in is the one the archiver is building. Drifting apart
        would put a gust in the record after the one it belongs to.
        """
        now = now if now is not None else time.time()
        return (int(now // self.interval) + 1) * self.interval

    # -- delivery ------------------------------------------------------

    def deliver(self, packets: list[Packet]) -> bool:
        """Hand a batch to the listener. False means none of it got through.

        Batched rather than sent one at a time: a loop packet every two
        seconds is a request every two seconds, all of them a round trip and a
        row, for readings that are aggregated at the end of the interval
        anyway.

        Split into requests of `PER_REQUEST`, because the listener refuses a
        body over a megabyte and a drained backlog is bigger than that. A
        chunk that got through is counted and not sent again; the rest is the
        caller's to hold.
        """
        from weewx_evo.ingest.listener import push

        if not packets:
            return True
        if self.dry_run:
            self.sent += len(packets)
            self.delivered_batches += 1
            return True

        for at in range(0, len(packets), PER_REQUEST):
            chunk = packets[at:at + PER_REQUEST]
            try:
                push(chunk, self.host, self.port, token=self.token,
                     as_driver=self.as_driver)
            except Exception as exc:
                log.warning("could not deliver %d packet(s) to %s:%s (%s); "
                            "holding %d", len(chunk), self.host, self.port,
                            exc, len(packets) - at)
                # What went before is delivered and must not go twice; what is
                # left from here is what the caller holds.
                del packets[:at]
                return False
            self.sent += len(chunk)
            self.delivered_batches += 1
        return True

    def hold(self, packets: list[Packet]) -> None:
        """Keep what could not be delivered, up to a point.

        A listener that is restarting is back in seconds and the readings
        should survive it. A listener that is gone for a day must not be paid
        for in memory here -- so the oldest go first, and how many went is
        said out loud rather than left to be inferred from a gap.
        """
        self.held.extend(packets)
        if len(self.held) > MAX_HELD:
            losing = len(self.held) - MAX_HELD
            del self.held[:losing]
            self.dropped += losing
            log.warning("holding %d packets, the limit; dropped the oldest %d "
                        "(%d in total)", MAX_HELD, losing, self.dropped)

    # -- the loop ------------------------------------------------------

    def run(self, once: bool = False, limit: int | None = None) -> int:
        """Until stopped. Returns how many packets were delivered.

        The driver is rebuilt after a failure, with a backoff, because that is
        what the failures are: hardware that went away. Giving up would mean a
        station that stops recording because a USB adapter re-enumerated at
        four in the morning.
        """
        wait = RETRY_FIRST
        while not self.stopping:
            try:
                self.open()
                wait = RETRY_FIRST          # it worked; the next fault starts short
                self._pump(once=once, limit=limit)
                if once or limit is not None:
                    return self.sent
            except KeyboardInterrupt:
                self.stopping = True
            except Exception:
                log.exception("the driver failed; retrying in %.0fs", wait)
            finally:
                self.close()
            if self.stopping or once or limit is not None:
                break
            self._sleep(wait)
            wait = min(wait * 2, RETRY_MAX)
        return self.sent

    def _pump(self, once: bool = False, limit: int | None = None) -> None:
        batch: list[Packet] = self.startup_records()
        last = time.time()

        for packet in self.generate():
            batch.append(packet)
            now = time.time()
            full = limit is not None and (self.sent + len(batch)) >= limit
            if full or once or now - last >= self.batch_seconds:
                batch = self._flush(batch)
                last = now
                if once or full:
                    return
        # The generator returned, which for a WeeWX driver means the hardware
        # stopped. Anything gathered still goes.
        self._flush(batch)

    def _flush(self, batch: list[Packet]) -> list[Packet]:
        """Everything held plus this batch, in the order it was measured."""
        going = self.held + batch
        # Cleared first either way: what was held is inside `going` now, and
        # holding it again would keep a second copy of every packet. On a
        # partial failure `deliver` has trimmed `going` to what did not get
        # through, so that is exactly what goes back.
        self.held = []
        if not self.deliver(going):
            self.hold(going)
        return []

    def _sleep(self, seconds: float) -> None:
        """Sleep, but wake for a stop. A shutdown must not wait five minutes."""
        until = time.time() + seconds
        while not self.stopping and time.time() < until:
            time.sleep(min(0.5, until - time.time()))

    def stop(self) -> None:
        self.stopping = True


def logs_its_own(console: Any) -> bool:
    """Whether this console keeps records a collector could fetch.

    Asked of the class rather than by calling anything: `genStartupRecords`
    on a console that has no log raises, and finding out by provoking it
    would mean waking the hardware to be told no.

    WeeWX's own base class defines the method and raises NotImplementedError,
    so its presence proves nothing -- what counts is whether this driver
    overrode it.
    """
    from weewx.drivers import AbstractDevice

    for name in ("genStartupRecords", "genArchiveRecords"):
        mine = getattr(type(console), name, None)
        theirs = getattr(AbstractDevice, name, None)
        if mine is not None and mine is not theirs:
            return True
    return False


def probe(config_dict: dict[str, Any], module_name: str | None = None,
          count: int = 3, source: str | None = None,
          driver_file: str | Path | None = None) -> dict[str, Any]:
    """Build the driver, take a few packets, send nothing.

    What `weewx-evo weewx-driver check` is: enough to say whether this driver,
    this configuration and this hardware work together, without a listener and
    without writing a reading anywhere.
    """
    from . import weewxnames

    shim = Shim(config_dict, module_name, source=source, dry_run=True,
                driver_file=driver_file)
    shim.open()
    try:
        got: list[Packet] = []
        for packet in shim.generate():
            got.append(packet)
            if len(got) >= count:
                break
        fields: set[str] = set()
        for one in got:
            fields.update(one.data)
        return {
            "driver": shim.module_name,
            "source": shim.source,
            "archive_interval": shim.interval,
            "callbacks": shim.engine.bound() if shim.engine else 0,
            # Said out loud, because it changes what a failure means: against
            # the stand-in, a missing name is ours to add, and against a real
            # WeeWX it is not.
            **weewxnames.describe(),
            # Whether this console keeps its own records, which is what
            # decides if `catchup` can do anything. WeeWX asks this as a
            # setting -- `record_generation = hardware | software` -- and it
            # is not a setting here: the archiver builds every record from
            # the live packets and takes the console's where there is one.
            # So the question is not which to use but whether there is one,
            # and that is a property of the hardware rather than a choice.
            #
            # Worth reporting because `record_generation = hardware` is in
            # every weewx.conf ever shipped, including on consoles with no
            # log at all, and WeeWX falls back to software without a word.
            "logs_its_own": logs_its_own(shim.console),
            "packets": len(got),
            "fields": sorted(fields),
            "usUnits": got[0].usUnits if got else None,
            "sample": dict(sorted(got[0].data.items())[:8]) if got else {},
        }
    finally:
        shim.close()
