"""A Fine Offset console on a USB bus that is not there.

The counterpart to `vantagesim.py`, for the other console this was built for:
a WH1080 or one of its many clones (WS2080, WH1081, Ambient, Elecsa, Watson).

These do not speak a command protocol. The console is 64 KB of memory and the
driver reads it in 32-byte blocks over USB HID: a control message carrying the
address, then an interrupt read. Everything else -- what a reading is, where
the next one goes, how many there are -- is a matter of what is *at* those
addresses. So the simulator is the memory, and the protocol is two calls.

The layout, from the driver's own tables:

    0x0000  the fixed block: magic number, model, settings, and at 0x1E the
            address the console is currently writing to
    0x0100  the ring buffer of readings, 16 or 20 bytes each depending on
            whether the console has a light sensor

Why bother when `standin_test.py` already decodes a record: because that test
hands `_decode` a buffer directly and never touches USB. Everything between
the bus and the reading -- `_find_device`, `_read_block`, the fixed-block
caching, `get_raw_data` walking the ring backwards -- runs only against a
device. Those are also the parts where the stand-in's `WeeWxIOError` gets
raised and caught.

**What this is and is not asking.** Not whether the driver reads a WH1080:
that is WeeWX's code and pyusb's, both unchanged, and a hub that
re-enumerates or a console that stops answering mid-block does the same
thing to WeeWX. The question is whether the driver behaves *differently*
against our stand-in, and both sides see this same memory.

What stays out of reach is `sync()`, and for a reason worth naming: it waits
for the console to write its *next* reading in order to date the last one.
Simulating that means simulating the passage of time, and a test built on
faked time reports the fake.

Used by `tools/fousb_test.py`.
"""

from __future__ import annotations

import struct
import types

#: How the console describes itself. `magic` 0x55 0xAA is what the driver
#: recognises; anything else it logs as unknown and carries on, which would
#: make a broken simulator look like a working one.
MAGIC = (0x55, 0xAA)

#: Where the readings start, and how long one is. 20 bytes is the 3080
#: layout, which has the light sensor; 16 is the 1080, which does not.
DATA_START = 0x0100
READING_LEN = 20

#: Minutes between logged readings, at 0x10 of the fixed block.
READ_PERIOD = 5

#: The readings this console holds, in its own units: Celsius, mbar, m/s and
#: millimetres. Deliberately not round numbers -- a simulator built on zeros
#: cannot tell a driver that decodes from one that returns its input.
READINGS = {
    "hum_in": 48,
    "temp_in": 21.3,          # C
    "hum_out": 61,
    "temp_out": 15.2,         # C
    "abs_pressure": 1013.2,   # mbar
    "wind_ave": 3.2,          # m/s
    "wind_gust": 7.1,         # m/s
    "wind_dir": 11,           # 0-15, so WSW
    "rain": 426.0,            # mm, the running total
    "illuminance": 5000.0,    # lux
    "uv": 4,
}


def reading(values: dict | None = None, delay: int = READ_PERIOD) -> bytes:
    """One 20-byte reading in the 3080 layout.

    Byte for byte out of `reading_format['3080']`, and the fields this
    console does not have are left at the console's own markers rather than
    at zero: 0xFF for a byte, 0xFFFF for a short. A driver that read the
    marker as a number would report a sensor that is not there, and zeros
    here would never catch it.
    """
    r = dict(READINGS)
    if values:
        r.update(values)

    raw = bytearray(READING_LEN)
    raw[0] = delay                                        # minutes since last
    raw[1] = int(r["hum_in"])
    raw[2:4] = struct.pack("<h", round(r["temp_in"] * 10))
    raw[4] = int(r["hum_out"])
    raw[5:7] = struct.pack("<h", round(r["temp_out"] * 10))
    raw[7:9] = struct.pack("<H", round(r["abs_pressure"] * 10))
    # Wind is a byte each plus a shared byte of high bits, which is the
    # layout's one real oddity: 0.1 m/s in the low byte, and bits 4-7 of
    # byte 11 carry the top four bits of each.
    ave, gust = round(r["wind_ave"] * 10), round(r["wind_gust"] * 10)
    raw[9] = ave & 0xFF
    raw[10] = gust & 0xFF
    raw[11] = ((ave >> 8) & 0x0F) | (((gust >> 8) & 0x0F) << 4)
    raw[12] = int(r["wind_dir"])
    raw[13:15] = struct.pack("<H", round(r["rain"] / 0.3))   # in bucket tips
    raw[15] = 0                                              # status
    lux = round(r["illuminance"] * 10)
    raw[16:19] = bytes([lux & 0xFF, (lux >> 8) & 0xFF, (lux >> 16) & 0xFF])
    raw[19] = int(r["uv"])
    return bytes(raw)


def memory(count: int = 6, values: dict | None = None) -> bytearray:
    """The console's whole 64 KB, with `count` readings in the ring buffer."""
    mem = bytearray(b"\x00" * 0x10000)

    # -- the fixed block ------------------------------------------------
    mem[0], mem[1] = MAGIC
    mem[2:4] = struct.pack("<H", 0x1080)      # model
    mem[4] = 0x10                             # firmware version
    mem[5:7] = struct.pack("<H", 0x0001)      # id
    mem[7:9] = struct.pack("<H", 3)           # rain coefficient
    mem[9:11] = struct.pack("<H", 5)          # wind coefficient
    mem[0x10] = READ_PERIOD                   # minutes between readings
    mem[0x11] = 0x00                          # settings 1: metric throughout
    mem[0x12] = 0x00                          # settings 2: wind in m/s
    mem[27:29] = struct.pack("<H", count)     # data_count
    # Where the console is writing *now*, which is the last slot and not the
    # one after it. The driver reads this address directly for a live
    # reading -- point it past the data and it decodes a slot of zeros as
    # -0.1 C with no wind, and every check downstream fails somewhere else.
    mem[30:32] = struct.pack("<H", DATA_START + (count - 1) * READING_LEN)
    mem[32:34] = struct.pack("<H", 10132)     # relative pressure, 0.1 mbar
    mem[34:36] = struct.pack("<H", 10132)     # absolute pressure
    mem[36:38] = struct.pack("<H", 1)         # lux to W/m2 coefficient

    # -- the readings ---------------------------------------------------
    #
    # The one the console is still filling in carries a *smaller* delay than
    # the read period, because it has not been running for a whole one yet.
    # The driver checks this and throws away a live reading whose delay has
    # already reached the interval: at that point the console is about to
    # overwrite the slot, so what is in it may be half of two readings.
    # Giving every slot a full delay -- the obvious thing -- makes the driver
    # reject every live reading it takes, for ever.
    for index in range(count):
        at = DATA_START + index * READING_LEN
        last = index == count - 1
        mem[at:at + READING_LEN] = reading(
            values, delay=1 if last else READ_PERIOD)
    return mem


class FakeHandle:
    """The USB handle the driver gets back from `dev.open()`.

    It answers the two calls the driver makes and nothing else. The address
    arrives inside the control message the driver builds by hand -- bytes 1
    and 2 of an eight-byte buffer -- so reading it out here is the same
    parsing the console does, and a driver that packed it wrongly would be
    caught by the reading coming back from the wrong place.
    """

    def __init__(self, mem: bytearray, block: int = 32):
        self.mem = mem
        self.block = block
        self.address = 0
        self.reads = 0
        self.writes: list[bytes] = []

    def controlMsg(self, requestType, request, buffer=None, value=0,
                   index=0, timeout=1000):
        data = bytes(buffer or b"")
        self.writes.append(data)
        # 0xA1 is the read command: A1 hi lo 20, twice.
        if len(data) >= 3 and data[0] == 0xA1:
            self.address = (data[1] << 8) | data[2]
        return len(data)

    def interruptRead(self, endpoint, size, timeout=1000):
        self.reads += 1
        at = self.address
        return list(self.mem[at:at + size])

    def setConfiguration(self, config):
        pass

    def claimInterface(self, interface):
        pass

    def releaseInterface(self):
        pass

    def detachKernelDriver(self, interface):
        pass

    def reset(self):
        pass


def usb_module(mem: bytearray, holder: dict | None = None) -> types.ModuleType:
    """A `usb` whose one device holds that memory.

    The old pyusb API, `usb.busses()` and `dev.open()`, because that is what
    this driver was written against and what it still uses.
    """
    usb = types.ModuleType("usb")
    usb.__path__ = []

    handle = FakeHandle(mem)
    if holder is not None:
        holder["handle"] = handle

    class Device:
        # The Fine Offset USB identifiers. Wrong ones here would mean
        # `_find_device` returns None and the driver raises before reading
        # anything -- which is a fair test of the driver, but not of this.
        idVendor = 0x1941
        idProduct = 0x8021
        deviceClass = 0
        devnum = 1
        filename = "001"

        def open(self):
            return handle

    class Bus:
        dirname = "001"
        devices = (Device(),)

    usb.busses = lambda: (Bus(),)
    usb.Device = Device
    usb.USBError = type("USBError", (OSError,), {})
    usb.TYPE_CLASS, usb.RECIP_INTERFACE, usb.RECIP_OTHER = 0x20, 0x01, 0x03
    usb.CLASS_HUB = 9
    usb.ENDPOINT_IN, usb.ENDPOINT_OUT = 0x80, 0x00
    usb.REQ_SET_CONFIGURATION, usb.REQ_CLEAR_FEATURE = 0x09, 1
    usb.REQ_SET_FEATURE = 3

    core = types.ModuleType("usb.core")
    core.find = lambda **kwargs: Device()
    core.USBError = usb.USBError
    util = types.ModuleType("usb.util")
    util.dispose_resources = lambda *a, **k: None
    usb.core, usb.util = core, util
    return usb
