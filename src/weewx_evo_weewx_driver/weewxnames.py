"""Make `import weewx` work for a driver, with no WeeWX installed.

`weewxshim.py` runs a WeeWX driver unchanged. Until now that meant WeeWX had
to be on the disk, because the driver file says `import weewx` at the top --
so a station whose only reason to want WeeWX was one USB console had to
install the whole of it.

Measured across the drivers in WeeWX's own tree, that import is almost
entirely ceremony. What they actually reach for:

    weewx.drivers          13   the base classes, which a shim does not use
    weewx.WeeWxIOError     12   an exception, three lines
    weewx.wxformulas       11   one function of it, twelve lines
    weeutil.weeutil        10   to_bool, to_int, timestamp_to_string
    weeutil.logger         10   setup() and log_traceback()
    weewx.debug             9   the integer 0
    weewx.RetriesExceeded   8   another exception
    weewx.METRIC / US       7   the numbers 16 and 1, which are ours as well

fousb is the lightest of them at four names, and Vantage the heaviest at
twenty. **All thirteen import against this file** (`tools/standin_test.py`),
so it is not a fousb-shaped answer to a fousb-shaped question.

Three of the pieces were added for the heavy end, and each was one driver's
whole reason not to run:

    weewx.engine      Vantage is a driver *and* a service: it inherits
                      StdService and binds in its constructor. Without this
                      the driver behind every Davis station does not import.
    weewx.crc16       cc3000 and Vantage write `from weewx.crc16 import
                      crc16`, so it has to be a module and not a function
                      hung on `weewx`.
    weewx.units       ultimeter and ws1 want the conversion constants, and
                      Vantage wants ValueTuple, convert and GenWithConvert.

The constants come from our own `units.py` rather than being retyped. A
driver dividing by 0.0295299875 and one dividing by 0.02953 disagree in the
fourth decimal of every pressure reading, and nothing about the number looks
wrong.

**Where WeeWX is installed, WeeWX wins.** Same rule as pyephem in `sun.py`:
somebody who has the real one gets the real one's behaviour, down to the
edges nobody has thought to write down. This fills in what is missing rather
than replacing what is there -- `install()` on a process that already has
WeeWX does nothing at all.

**And it never overwrites another stand-in.** `skinkit.py` puts modules under
these same names so that a WeeWX *skin* renders, and `sys.modules` is one
table for the process. Whichever ran second would silently take the other's
`weewx.units` away. So each name is claimed only if free, and what is already
there is left alone: two stand-ins for different halves of the same program
can share a process without either of them noticing.

What this is not: an implementation of WeeWX. `weewx.manager` is a database
and `weewx.engine` here is one class; a driver that wants more gets an error
with the name in it, and that name is the thing to add -- not a polite answer
that turns into a wrong reading three layers down. Same reason `ShimEngine`
has no `__getattr__`.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any

log = logging.getLogger(__name__)

#: WeeWX's unit-system constants. Ours are the same numbers -- they are
#: WeeWX's, written into every archive ever made, which is the one rule.
US = 0x01
METRIC = 0x10
METRICWX = 0x11

#: The names this file can claim. Also what `describe()` reports on.
#:
#: `weewx.crc16` is a *module* holding a function of the same name, because
#: cc3000 and Vantage write `from weewx.crc16 import crc16`. As an attribute
#: on `weewx` it looks right, imports fine from anything that says
#: `weewx.crc16(...)`, and fails only on the two drivers that need it.
NAMES = ("weewx", "weewx.drivers", "weewx.wxformulas", "weewx.units",
         "weewx.engine", "weewx.crc16",
         "weeutil", "weeutil.weeutil", "weeutil.logger")


def installed() -> bool:
    """Is the real WeeWX importable? Then nothing here is needed."""
    import importlib.util

    if "weewx" in sys.modules:
        # Already imported, by us or by anybody. A stand-in has no file.
        return getattr(sys.modules["weewx"], "__file__", None) is not None
    try:
        return importlib.util.find_spec("weewx") is not None
    except (ImportError, ValueError):
        # A half-installed package, or one whose parent cannot be imported.
        # Either way there is nothing to find.
        return False


# -- the pieces ---------------------------------------------------------


def _weewx_module() -> types.ModuleType:
    weewx = types.ModuleType("weewx")
    # A package, so `import weewx.drivers` can work at all.
    weewx.__path__ = []  # type: ignore[attr-defined]
    weewx.__version__ = "5.1.0"
    weewx.debug = 0
    weewx.US, weewx.METRIC, weewx.METRICWX = US, METRIC, METRICWX

    class WeeWxIOError(IOError):
        """The console did not answer, or answered nonsense."""

    class WakeupError(WeeWxIOError):
        """It would not wake up."""

    class CRCError(WeeWxIOError):
        """It answered, and the answer did not check out."""

    class RetriesExceeded(WeeWxIOError):
        """It kept not answering."""

    class HardwareError(Exception):
        """The hardware is wrong for what was asked of it."""

    class UnknownArchiveType(HardwareError):
        """A record type this driver does not know."""

    class UnsupportedFeature(Exception):
        """This console cannot do that."""

    class ViolatedPrecondition(Exception):
        """Called in an order that cannot work."""

    class StopNow(Exception):
        """Stop the loop."""

    for cls in (WeeWxIOError, WakeupError, CRCError, RetriesExceeded,
                HardwareError, UnknownArchiveType, UnsupportedFeature,
                ViolatedPrecondition, StopNow):
        setattr(weewx, cls.__name__, cls)
    # ws1.py spells it `WeeWXIOError`. Upstream's own typo, and it is raised
    # on a read failure -- so with the name missing the driver would work
    # until the serial line hiccups, then fail on the line meant to report it.
    weewx.WeeWXIOError = WeeWxIOError

    def crc16(buf: bytes) -> int:
        """CCITT CRC-16. Only Vantage asks for it."""
        crc = 0
        for byte in buf:
            crc = ((crc << 8) & 0xFF00) ^ _CRC_TABLE[((crc >> 8) & 0xFF) ^ byte]
        return crc

    weewx.crc16 = crc16

    # The event types, and `Event` itself. Classes used as tokens, which is
    # how WeeWX does it -- what matters is that they compare by identity.
    #
    # Not found by importing anything: a driver binds these while it is being
    # *built*, so `import weewx.drivers.vantage` succeeds and `loader()`
    # raises AttributeError one line into the constructor. `Shim.open()`
    # dispatches STARTUP, so without these the shim would have failed on
    # every driver, not only the ones that bind.
    for event in ("STARTUP", "PRE_LOOP", "NEW_LOOP_PACKET", "CHECK_LOOP",
                  "END_ARCHIVE_PERIOD", "NEW_ARCHIVE_RECORD", "POST_LOOP",
                  "SHUTDOWN"):
        setattr(weewx, event, type(event, (), {}))

    class Event:
        """One event, with whatever the sender attached to it."""

        def __init__(self, event_type, **kwargs):
            self.event_type = event_type
            self.__dict__.update(kwargs)

        def __str__(self):
            fields = ", ".join(f"{k}: {v}" for k, v in self.__dict__.items()
                               if k != "event_type")
            return f"Event {getattr(self.event_type, '__name__', '?')}: {fields}"

    weewx.Event = Event
    return weewx


def _drivers_module() -> types.ModuleType:
    """`weewx.drivers`: three base classes and nothing else.

    They are nearly empty, and so are WeeWX's. What its own hold is
    `genLoopPackets` raising NotImplementedError and a `closePort` that does
    nothing. A driver leaning on more than that is one to hear about from a
    traceback rather than to guess at.
    """
    drivers = types.ModuleType("weewx.drivers")
    drivers.__path__ = []  # type: ignore[attr-defined]

    class AbstractDevice:
        def genLoopPackets(self):  # noqa: N802 - WeeWX's name
            raise NotImplementedError("genLoopPackets")

        def genArchiveRecords(self, lastgood_ts):  # noqa: N802 - WeeWX's name
            raise NotImplementedError("genArchiveRecords")

        def getTime(self):  # noqa: N802 - WeeWX's name
            raise NotImplementedError("getTime")

        def setTime(self):  # noqa: N802 - WeeWX's name
            raise NotImplementedError("setTime")

        @property
        def hardware_name(self):
            return "Unknown"

        @property
        def archive_interval(self):
            raise NotImplementedError("archive_interval")

        def closePort(self):  # noqa: N802 - WeeWX's name
            pass

    class AbstractConfEditor:
        """Only used by `weectl station`, which is not run here."""

        @property
        def default_stanza(self):
            return ""

        def get_conf(self, orig_stanza=None):
            return orig_stanza or self.default_stanza

        def prompt_for_settings(self):
            return {}

        def modify_config(self, config_dict):
            pass

    class AbstractConfigurator:
        """Likewise: a driver's own command line."""

        @property
        def description(self):
            return "Configuration utility for a weewx device."

        @property
        def usage(self):
            return "%prog [config_file] [options] [-y] [--debug] [--help]"

        def configure(self, config_dict):
            pass

    drivers.AbstractDevice = AbstractDevice
    drivers.AbstractConfEditor = AbstractConfEditor
    drivers.AbstractConfigurator = AbstractConfigurator
    return drivers


def _wxformulas_module() -> types.ModuleType:
    formulas = types.ModuleType("weewx.wxformulas")

    def calculate_delta(newtotal, oldtotal, delta_key="rain"):
        """The increment between two running totals, or None.

        Transcribed, like everything else that touches a reading. A counter
        that went backwards gives None and not zero: zero is a measurement
        saying no rain fell, None says we do not know, and a counter reset
        makes the second one true.
        """
        if newtotal is not None and oldtotal is not None:
            if newtotal >= oldtotal:
                return newtotal - oldtotal
            log.info("'%s' counter reset detected: new=%s old=%s",
                     delta_key, newtotal, oldtotal)
            return None
        return None

    formulas.calculate_delta = calculate_delta
    formulas.calculate_rain = calculate_delta   # upstream's older name
    return formulas


def _units_module() -> types.ModuleType:
    """`weewx.units`, the three names a driver reaches for.

    Not the skin layer's version of it, which is a great deal more --
    formatters, helpers, the whole of `units.Target`. A driver converts a
    reading and wraps a generator, and that is all. Built on our own
    `units.py`, which is a transcription of WeeWX's, so the arithmetic is the
    same arithmetic and not a second opinion about it.
    """
    from weewx_evo import units as ours

    module = types.ModuleType("weewx.units")
    module.obs_group_dict = ours.GROUPS
    module.USUnits = ours.SYSTEMS.get(US, {})
    module.MetricUnits = ours.SYSTEMS.get(METRIC, {})
    module.MetricWXUnits = ours.SYSTEMS.get(METRICWX, {})
    # The conversion constants, taken from our own units.py rather than
    # retyped. A driver that divides by 0.0295299875 and one that divides by
    # 0.02953 disagree in the fourth decimal of every pressure reading, and
    # nothing about the number looks wrong.
    for constant in ("INHG_PER_MBAR", "MM_PER_INCH", "CM_PER_INCH",
                     "METER_PER_MILE", "METER_PER_FOOT", "MILE_PER_KM",
                     "SECS_PER_DAY"):
        setattr(module, constant, getattr(ours, constant))
    for helper in ("CtoK", "KtoC", "CtoF", "FtoC", "KtoF", "FtoK",
                   "mps_to_mph", "kph_to_mph", "mps_to_knot", "kph_to_knot",
                   "mph_to_knot"):
        setattr(module, helper, getattr(ours, helper))

    class ValueTuple(tuple):
        """WeeWX's `(value, unit, group)`.

        Its own rather than the one in `skinkit.py`, which is the same idea
        with arithmetic on it -- importing that would pull the tag layer and
        the series reader into a process whose whole job is to read a serial
        port. Two classes of this name never meet: whichever stand-in claims
        `weewx.units` keeps it, and a collector does not render skins.
        """

        def __new__(cls, *args):
            return tuple.__new__(cls, args)

        @property
        def value(self):
            return self[0]

        @property
        def unit(self):
            return self[1]

        @property
        def group(self):
            return self[2]

    def convert(val_t, target_unit):
        """A ValueTuple in another unit. The group comes along unchanged."""
        return ValueTuple(ours.convert(val_t[0], val_t[1], target_unit),
                          target_unit, val_t[2])

    def to_std_system(datadict, unit_system):
        """A whole record in a target unit system.

        Transcribed from `weewx.units.to_std_system`, including the part that
        matters most: a record already in that system is returned *as it is*,
        not rebuilt. And `usUnits` is set on the way out -- a converted record
        that still claims its old system is worse than one not converted at
        all, because everything downstream believes the label.
        """
        if datadict.get("usUnits") == unit_system:
            return datadict
        out = dict(datadict)
        for field, value in datadict.items():
            if field in ("dateTime", "usUnits", "interval") or value is None:
                continue
            was, group = ours.unit_of(field, datadict["usUnits"])
            if group is None:
                # Nothing knows what it measures, so nothing can convert it.
                # Carried across unchanged rather than dropped: the driver
                # put it there on purpose.
                continue
            wanted = ours.SYSTEMS.get(unit_system, {}).get(group)
            if wanted and wanted != was:
                out[field] = ours.convert(value, was, wanted)
        out["usUnits"] = unit_system
        return out

    module.ValueTuple = ValueTuple
    module.convert = convert
    module.to_std_system = to_std_system

    class GenWithConvert:
        """A generator whose records come out in one unit system.

        Vantage wraps its archive generator in this. Transcribed rather than
        replaced by a comprehension: `target_unit_system=None` means leave
        the record alone, and that is the branch a rewrite drops.
        """

        def __init__(self, input_generator, target_unit_system=METRIC):
            self.input_generator = input_generator
            self.target_unit_system = target_unit_system

        def __iter__(self):
            return self

        def __next__(self):
            record = next(self.input_generator)
            if self.target_unit_system is None:
                return record
            return to_std_system(record, self.target_unit_system)

    module.GenWithConvert = GenWithConvert
    return module


def _engine_module() -> types.ModuleType:
    """`weewx.engine`, which for a driver is one class.

    Vantage is a driver *and* a service: it inherits from `StdService`, calls
    `bind()` in its constructor and writes into the packet from inside an
    event. Without this it does not import at all -- and it is the driver
    behind every Davis station there is.

    `StdService` here forwards to whatever engine it was given, which is
    `ShimEngine`. Nothing else of the engine is offered: a driver that wants
    the database or the report thread is asking for something a collector in
    another process does not have, and finding that out from an
    AttributeError beats finding it out from a wrong reading.
    """
    engine = types.ModuleType("weewx.engine")
    engine.__path__ = []  # type: ignore[attr-defined]

    class StdService:
        def __init__(self, engine, config_dict, **kwargs):
            self.engine = engine
            self.config_dict = config_dict

        def bind(self, event_type, callback):
            self.engine.bind(event_type, callback)

        def shutDown(self):  # noqa: N802 - WeeWX's name
            pass

    engine.StdService = StdService
    return engine


def _crc16_module() -> types.ModuleType:
    """`weewx.crc16`, a module of one function.

    A module and not an attribute, because both drivers that use it write
    `from weewx.crc16 import crc16`.
    """
    module = types.ModuleType("weewx.crc16")

    def crc16(buf) -> int:
        crc = 0
        for byte in buf:
            crc = ((crc << 8) & 0xFF00) ^ _CRC_TABLE[((crc >> 8) & 0xFF) ^ byte]
        return crc

    module.crc16 = crc16
    return module


def _weeutil_modules() -> tuple[types.ModuleType, ...]:
    import time

    weeutil = types.ModuleType("weeutil")
    weeutil.__path__ = []  # type: ignore[attr-defined]

    inner = types.ModuleType("weeutil.weeutil")

    def to_bool(value):
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "yes", "y", "1", "on"):
                return True
            if value.lower() in ("false", "no", "n", "0", "off", ""):
                return False
            raise ValueError(f"Unknown boolean specifier: '{value}'")
        return bool(value)

    def to_int(value):
        if value is None or (isinstance(value, str)
                             and value.strip().lower() == "none"):
            return None
        if isinstance(value, str) and "." in value:
            # WeeWX answers 5 to `to_int('5.0')`. A configuration written by
            # hand says 5.0 often enough, and `int('5.0')` raises.
            value = float(value)
        return int(value)

    def to_float(value):
        if value is None or (isinstance(value, str)
                             and value.strip().lower() == "none"):
            return None
        return float(value)

    def timestamp_to_string(ts, format_str="%Y-%m-%d %H:%M:%S %Z"):
        if ts is None:
            return "******* N/A *******     (    N/A   )"
        return f"{time.strftime(format_str, time.localtime(ts))} ({int(ts)})"

    def startOfDay(time_ts):  # noqa: N802 - WeeWX's name
        when = time.localtime(time_ts)
        return int(time.mktime((when.tm_year, when.tm_mon, when.tm_mday,
                                0, 0, 0, 0, 0, -1)))

    def y_or_n(prompt, noprompt=False, default=None):
        """A driver asking a terminal to be sure. There is none here."""
        raise RuntimeError(
            "the driver asked for confirmation on a terminal that is not "
            "there; run its own configurator under WeeWX for that")

    def to_sorted_string(d):
        """A record printed in a stable order. Vantage logs with it."""
        return ", ".join(f"{k}: {d[k]}" for k in sorted(d))

    for name, thing in (("to_bool", to_bool), ("tobool", to_bool),
                        ("to_int", to_int), ("to_float", to_float),
                        ("timestamp_to_string", timestamp_to_string),
                        ("to_sorted_string", to_sorted_string),
                        ("startOfDay", startOfDay), ("y_or_n", y_or_n)):
        setattr(inner, name, thing)

    logger = types.ModuleType("weeutil.logger")
    logger.setup = lambda *a, **k: None

    def log_traceback(log_fn, prefix=""):
        import traceback

        for line in traceback.format_exc().splitlines():
            log_fn(f"{prefix}{line}")

    logger.log_traceback = log_traceback
    return weeutil, inner, logger


# WeeWX's CRC-16 table, copied. Only Vantage needs it, and a table of
# constants is the one kind of code where retyping is worse than copying.
_CRC_TABLE = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
    0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
    0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c, 0xf3ff, 0xe3de,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64e6, 0x74c7, 0x44a4, 0x5485,
    0xa56a, 0xb54b, 0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6, 0x5695, 0x46b4,
    0xb75b, 0xa77a, 0x9719, 0x8738, 0xf7df, 0xe7fe, 0xd79d, 0xc7bc,
    0x48c4, 0x58e5, 0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969, 0xa90a, 0xb92b,
    0x5af5, 0x4ad4, 0x7ab7, 0x6a96, 0x1a71, 0x0a50, 0x3a33, 0x2a12,
    0xdbfd, 0xcbdc, 0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
    0x6ca6, 0x7c87, 0x4ce4, 0x5cc5, 0x2c22, 0x3c03, 0x0c60, 0x1c41,
    0xedae, 0xfd8f, 0xcdec, 0xddcd, 0xad2a, 0xbd0b, 0x8d68, 0x9d49,
    0x7e97, 0x6eb6, 0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
    0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a, 0x9f59, 0x8f78,
    0x9188, 0x81a9, 0xb1ca, 0xa1eb, 0xd10c, 0xc12d, 0xf14e, 0xe16f,
    0x1080, 0x00a1, 0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c, 0xe37f, 0xf35e,
    0x02b1, 0x1290, 0x22f3, 0x32d2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xb5ea, 0xa5cb, 0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
    0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xa7db, 0xb7fa, 0x8799, 0x97b8, 0xe75f, 0xf77e, 0xc71d, 0xd73c,
    0x26d3, 0x36f2, 0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9, 0xb98a, 0xa9ab,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18c0, 0x08e1, 0x3882, 0x28a3,
    0xcb7d, 0xdb5c, 0xeb3f, 0xfb1e, 0x8bf9, 0x9bd8, 0xabbb, 0xbb9a,
    0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0, 0x2ab3, 0x3a92,
    0xfd2e, 0xed0f, 0xdd6c, 0xcd4d, 0xbdaa, 0xad8b, 0x9de8, 0x8dc9,
    0x7c26, 0x6c07, 0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
    0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba, 0x8fd9, 0x9ff8,
    0x6e17, 0x7e36, 0x4e55, 0x5e74, 0x2e93, 0x3eb2, 0x0ed1, 0x1ef0,
]


# -- installing ---------------------------------------------------------


def install(force: bool = False) -> list[str]:
    """Claim the names a driver imports. Returns the ones this call claimed.

    Nothing is replaced. A name already in `sys.modules` belongs either to
    the real WeeWX or to `skinkit`, and in both cases what is there knows
    more than this does.
    """
    if not force and installed():
        log.debug("WeeWX is installed; the stand-in is not needed")
        return []

    weewx = _weewx_module()
    drivers = _drivers_module()
    formulas = _wxformulas_module()
    weeutil, weeutil_inner, weeutil_logger = _weeutil_modules()

    claimed = []
    for name, module in (("weewx", weewx),
                         ("weewx.drivers", drivers),
                         ("weewx.wxformulas", formulas),
                         ("weewx.units", _units_module()),
                         ("weewx.engine", _engine_module()),
                         ("weewx.crc16", _crc16_module()),
                         ("weeutil", weeutil),
                         ("weeutil.weeutil", weeutil_inner),
                         ("weeutil.logger", weeutil_logger)):
        if name in sys.modules:
            continue
        sys.modules[name] = module
        claimed.append(name)
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(sys.modules[parent], child, module)

    if claimed:
        log.info("no WeeWX installed; standing in for %s", ", ".join(claimed))
    return claimed


def standing_in() -> list[str]:
    """Which of the names are ours rather than a package on the disk."""
    return [name for name in NAMES
            if name in sys.modules
            and getattr(sys.modules[name], "__file__", None) is None]


def describe() -> dict[str, Any]:
    """What is standing in, for `weewx-driver check` to print."""
    return {"weewx_installed": installed(), "standing_in": standing_in()}
