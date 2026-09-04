"""A device that is not there, for any driver that wants one.

`vantagesim.py` speaks one console's protocol properly, and that costs real
protocol knowledge -- the transmitter table, the CRC, what the console says
for "no sensor here". Thirteen of those is weeks of work for hardware nobody
here owns.

This is the other half of the answer, and it is aimed at a different
question. What is under test is not the driver: that is WeeWX's code,
unchanged, and their tests cover it. What is under test is **our stand-in**
underneath it. For that, the device does not have to be right. It has to be
*the same on both sides*.

So this hands out bytes from a seeded generator: the same driver, given
byte-for-byte the same answers, is run twice -- once against
`weewxnames.py` and once against a real WeeWX -- and the two runs have to
agree. On what they produced, and equally on how they failed. A driver that
raises `WeeWxIOError: Expected to read 8 chars; got 2` on both sides has told
us what we asked: the stand-in behaves like the thing it stands in for.

Where this is weak, and it should be said plainly: with arbitrary bytes most
drivers fail a checksum and retry rather than decoding a reading, so what is
compared is often two failures. That is still evidence -- a `RetriesExceeded`
against a `KeyError` is a real difference, and so is a message that names a
different number. But it is not the evidence `vantagesim.py` gives, and the
two are not interchangeable.

Deterministic on purpose. `random.Random(seed)` and nothing from the clock:
a test whose two halves see different bytes compares nothing at all, and
would do it while looking green about half the time.
"""

from __future__ import annotations

import random
import types

#: Bytes handed out before the well runs dry and reads start coming back
#: short. A driver has to be able to reach the end of the data: one that
#: never does spins in its retry loop until the test times out.
SUPPLY = 65536


class FakeStream:
    """Something to read from and write to, the same every time.

    Modelled on `serial.Serial`, which is the shape most of these drivers
    want. The USB ones get the same bytes through a different door.
    """

    def __init__(self, seed: int = 1, port=None, baudrate=19200,
                 timeout=1.0, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.written: list[bytes] = []
        self.closed = False
        # Seeded and not cryptographic on purpose: what matters is that
        # both runs see the same bytes, and nothing here is a secret.
        rng = random.Random(seed)  # noqa: S311
        self._supply = bytes(rng.randrange(256) for _ in range(SUPPLY))
        self._at = 0

    # -- pyserial's surface ---------------------------------------------

    def read(self, count=1):
        got = self._supply[self._at:self._at + count]
        self._at += len(got)
        return got

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def readline(self):
        return self.read(64)

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    flushInput = reset_input_buffer
    flushOutput = reset_output_buffer

    @property
    def in_waiting(self):
        return max(0, len(self._supply) - self._at)

    inWaiting = lambda self: self.in_waiting  # noqa: E731 - pyserial's old name

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serial_module(seed: int = 1) -> types.ModuleType:
    """A `serial` whose ports are the stream above."""
    module = types.ModuleType("serial")

    class SerialException(Exception):
        pass

    class SerialTimeoutException(SerialException):
        pass

    def Serial(*args, **kwargs):  # pyserial's name
        return FakeStream(seed, *args, **kwargs)

    module.Serial = Serial
    module.SerialException = SerialException
    module.SerialTimeoutException = SerialTimeoutException
    module.serialutil = types.SimpleNamespace(
        SerialException=SerialException,
        SerialTimeoutException=SerialTimeoutException)
    module.PARITY_NONE, module.PARITY_EVEN, module.PARITY_ODD = "N", "E", "O"
    module.STOPBITS_ONE, module.STOPBITS_TWO = 1, 2
    module.EIGHTBITS, module.SEVENBITS = 8, 7
    module.VERSION = "3.5"
    return module


def usb_module(seed: int = 1) -> tuple[types.ModuleType, ...]:
    """`usb`, `usb.core` and `usb.util`, over the same stream.

    Both generations of the API: `usb.busses()` with `dev.open()`, which
    fousb and te923 use, and `usb.core.find()` with `dev.read()`, which the
    newer ones use. A driver written against one and handed the other fails
    on its first call, and that failure would look like a stand-in problem.
    """
    stream = FakeStream(seed)

    usb = types.ModuleType("usb")
    usb.__path__ = []
    core = types.ModuleType("usb.core")
    core.__path__ = []
    util = types.ModuleType("usb.util")

    class USBError(IOError):
        def __init__(self, message="usb error", errno=None):
            super().__init__(message)
            self.errno = errno
            self.backend_error_code = errno

    class Handle:
        def controlMsg(self, requestType, request, buffer=None, value=0,
                       index=0, timeout=1000):
            if isinstance(buffer, int):
                return stream.read(buffer)
            stream.written.append(bytes(buffer or b""))
            return len(buffer or b"")

        def interruptRead(self, endpoint, size, timeout=1000):
            return stream.read(size)

        def bulkRead(self, endpoint, size, timeout=1000):
            return stream.read(size)

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

    class Device:
        idVendor, idProduct = 0x1941, 0x8021
        deviceClass, devnum, filename = 0, 1, "001"
        bus = None

        def open(self):
            return Handle()

        def read(self, endpoint, size_or_buffer, timeout=1000):
            size = (size_or_buffer if isinstance(size_or_buffer, int)
                    else len(size_or_buffer))
            return stream.read(size)

        def write(self, endpoint, data, timeout=1000):
            stream.written.append(bytes(data))
            return len(data)

        def ctrl_transfer(self, bmRequestType, bRequest, wValue=0, wIndex=0,
                          data_or_wLength=None, timeout=1000):
            if isinstance(data_or_wLength, int):
                return stream.read(data_or_wLength)
            stream.written.append(bytes(data_or_wLength or b""))
            return len(data_or_wLength or b"")

        def set_configuration(self, config=None):
            pass

        def is_kernel_driver_active(self, interface):
            return False

        def detach_kernel_driver(self, interface):
            pass

        def reset(self):
            pass

        def get_active_configuration(self):
            return {}

    class Bus:
        dirname = "001"
        devices = (Device(),)

    usb.busses = lambda: (Bus(),)
    usb.USBError = core.USBError = USBError
    usb.Device = Device
    usb.TYPE_CLASS, usb.RECIP_OTHER, usb.CLASS_HUB = 0x20, 0x03, 9
    usb.REQ_CLEAR_FEATURE, usb.REQ_SET_FEATURE = 1, 3
    usb.ENDPOINT_IN, usb.ENDPOINT_OUT = 0x80, 0x00

    core.find = lambda **kwargs: Device()
    core.Device = Device
    util.find_descriptor = lambda *a, **k: None
    util.claim_interface = lambda *a, **k: None
    util.release_interface = lambda *a, **k: None
    util.dispose_resources = lambda *a, **k: None
    util.ENDPOINT_IN, util.ENDPOINT_OUT = 0x80, 0x00

    usb.core, usb.util = core, util
    return usb, core, util


def install(seed: int = 1) -> None:
    """Put every hardware library a driver might import into `sys.modules`.

    All of them, whether this driver wants them or not: the two runs being
    compared must differ in the stand-in and in nothing else, and "pyserial
    happened to be installed on the machine with WeeWX" is exactly the kind
    of difference that would be read as one.
    """
    import sys

    sys.modules["serial"] = serial_module(seed)
    usb, core, util = usb_module(seed)
    sys.modules["usb"] = usb
    sys.modules["usb.core"] = core
    sys.modules["usb.util"] = util

    for name in ("hid", "pylibftdi", "ftdi", "usb.backend",
                 "usb.backend.libusb1"):
        module = types.ModuleType(name)
        module.__path__ = []
        module.device = lambda *a, **k: FakeStream(seed)
        sys.modules.setdefault(name, module)
