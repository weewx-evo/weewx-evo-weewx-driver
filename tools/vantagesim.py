"""A Davis Vantage console on a serial port that is not there.

Not a mock of the driver: a stand-in for the *hardware*, one layer below it.
It speaks the console's side of the protocol -- wake-ups, EEPROM reads, LOOP
packets, archive pages, each with the CRC the real thing appends -- and
`weewx.drivers.vantage` runs against it unchanged, from `loader()` to the
packets it yields.

Why bother, when the driver is somebody else's and works: because the
*stand-in* under it is ours. `weewxnames.py` supplies `weewx.engine`,
`weewx.units` and the rest, and a driver that merely imports proves only that
none of the names is missing. It says nothing about whether `ValueTuple` holds
its three fields in the order Vantage expects, or whether `GenWithConvert`
converts. Both of those would fail on the hardware and nowhere else.

The console modelled here is a Vantage Pro2 (hardware type 16) with a Rev B
logger, US units, a 0.01 in rain bucket and a five minute archive interval.
The bytes come from the driver's own decoding tables rather than from a
recording, so what this measures is the round trip: encode with the table,
have the driver decode it, and get the number back.

**What this is and is not asking.** Not whether the driver works: that is
WeeWX's code, unchanged, and fifteen years of stations have answered it.
Timing, a console that answers late, an adapter that drops a byte, firmware
that lies about its page count -- all of those behave the same under WeeWX,
because the driver and pyserial are the same driver and the same pyserial.

The question is whether the driver behaves *differently* against our stand-in
than against a real WeeWX, and that one has no hardware in it: both sides see
the same device. What is left open is coverage, not equipment -- a branch
neither run reaches, in a driver's error handling, that calls something we
transcribed. `standin_test.py` closes the largest of those by comparing every
field of `weewx.units` against WeeWX's own.

Used by `tools/vantage_test.py`.
"""

from __future__ import annotations

import struct
import time

#: The CRC table Davis uses, the same one `weewx.crc16` holds. Here rather
#: than imported from the stand-in on purpose: a simulator that computes its
#: checksum with the code under test agrees with it by construction, and a
#: transposed pair in the table would then be invisible from both sides.
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

ACK = b"\x06"

#: What this console says it is. A Pro2 with a Rev B logger, which is the
#: overwhelmingly common one and the only shape whose archive record layout
#: the driver decodes into all its fields.
HARDWARE_TYPE = 16

#: The EEPROM bytes the driver reads at start-up, by address. Everything else
#: reads as zero, which the driver handles.
#:
#: 0x29 unit bits: barometer inHg, temperature F, altitude foot, rain in,
#:      wind mph -- the factory US setting. 0x2B setup bits: rain bucket type
#:      0, which is the 0.01 in bucket. 0x2D is the archive interval in
#:      minutes.
EEPROM = {
    0x29: bytes([0x00]),                 # unit bits: all US
    0x2B: bytes([0x00]),                 # setup bits: bucket 0, small cups
    0x2C: bytes([1]),                    # rain year starts in January
    0x2D: bytes([5]),                    # archive interval, minutes
    0x0F: struct.pack("<h", 700),        # altitude, feet
}

#: The transmitter table at EEPROM 0x19: eight channels of two bytes, the
#: type in the low nibble of the first. Channel 1 is the ISS (type 0), the
#: other seven are "none" (type 10).
#:
#: 0xFF here was the first attempt and it is *not* an empty channel: the low
#: nibble reads 15, which is not a type at all, and the driver raises KeyError
#: on it before it has read a single reading. The console's word for nothing
#: is 10, and a simulator has to know that -- which is the whole cost of this
#: kind of test, and the whole reason it finds things.
TRANSMITTERS = bytes([0x00, 0x00] + [0x0A, 0x00] * 7)


def crc16(buf) -> int:
    crc = 0
    for byte in buf:
        crc = ((crc << 8) & 0xFF00) ^ _CRC_TABLE[((crc >> 8) & 0xFF) ^ byte]
    return crc


def with_crc(payload: bytes) -> bytes:
    """A block as the console sends it: the bytes, then a big-endian CRC."""
    return payload + struct.pack(">H", crc16(payload))


#: The readings this console reports, in the units a US Vantage uses. Kept as
#: real numbers rather than raw counts so that a test can say what it expects
#: without decoding anything itself.
READINGS = {
    "barometer": 30.12,        # inHg
    "inTemp": 71.4,            # F
    "outTemp": 58.7,           # F
    "inHumidity": 41,          # %
    "outHumidity": 76,         # %
    "windSpeed": 7,            # mph
    "windDir": 247,            # degrees
    "windGust10": 12,          # mph, the ten-minute high
    "rainRate": 0.24,          # in/hr
    "dayRain": 0.37,           # in
    "UV": 4.5,
    "radiation": 631,          # W/m^2
}


def loop_packet(readings: dict | None = None) -> bytes:
    """One 99-byte LOOP1 packet, plus its CRC.

    Built by hand from the layout in the driver's `loop_fmt`, and the fields
    left out are set to the console's own "no reading" markers -- 0xFF for a
    byte, 0x7FFF for a short. That is not tidiness: a simulator that sends
    zeros teaches the driver that every sensor this console does not have is
    reading exactly zero, and a test built on it would pass with a driver that
    ignored the markers.
    """
    r = dict(READINGS)
    if readings:
        r.update(readings)

    packet = bytearray(99)
    packet[0:3] = b"LOO"
    packet[3] = 0                       # bar trend
    packet[4] = 0                       # packet type: LOOP1
    packet[5:7] = struct.pack("<H", 0)  # next record
    # Barometer, inHg in thousandths.
    packet[7:9] = struct.pack("<H", round(r["barometer"] * 1000))
    packet[9:11] = struct.pack("<h", round(r["inTemp"] * 10))
    packet[11] = int(r["inHumidity"])
    packet[12:14] = struct.pack("<h", round(r["outTemp"] * 10))
    packet[14] = int(r["windSpeed"])
    packet[15] = 0                      # ten-minute average wind
    packet[16:18] = struct.pack("<H", int(r["windDir"]))
    packet[18:25] = b"\xff" * 7         # extra temperatures: none
    packet[25:29] = b"\xff" * 4         # soil temperatures: none
    packet[29:33] = b"\xff" * 4         # leaf temperatures: none
    packet[33] = int(r["outHumidity"])
    packet[34:41] = b"\xff" * 7         # extra humidities: none
    packet[41:43] = struct.pack("<H", round(r["rainRate"] * 100))
    packet[43] = round(r["UV"] * 10)
    packet[44:46] = struct.pack("<H", int(r["radiation"]))
    packet[46:48] = struct.pack("<H", 0)        # storm rain
    packet[48:50] = struct.pack("<H", 0xFFFF)   # storm start date: none
    packet[50:52] = struct.pack("<H", round(r["dayRain"] * 100))
    packet[52:54] = struct.pack("<H", 0)        # month rain
    packet[54:56] = struct.pack("<H", 0)        # year rain
    packet[56:58] = struct.pack("<H", 0)        # day ET
    packet[58:60] = struct.pack("<H", 0)        # month ET
    packet[60:62] = struct.pack("<H", 0)        # year ET
    packet[62:66] = b"\xff" * 4                 # soil moisture: none
    packet[66:70] = b"\xff" * 4                 # leaf wetness: none
    packet[70:78] = b"\x00" * 8                 # alarms
    packet[78:81] = b"\x00" * 3
    packet[81] = 0                              # transmitter battery status
    packet[82:84] = struct.pack("<H", 0x01A0)   # console battery volts
    packet[84] = 0                              # forecast icon
    packet[85] = 0                              # forecast rule
    packet[86:88] = struct.pack("<H", 0)        # sunrise
    packet[88:90] = struct.pack("<H", 0)        # sunset
    packet[90:93] = b"\n\r\x00"
    return with_crc(bytes(packet[:97]))


def archive_record(when: float, readings: dict | None = None) -> bytes:
    """One 52-byte Rev B archive record for a moment in local time.

    The date and time are packed the way the console does it: the day, month
    and year since 2000 in one short, and hours*100 + minutes in the next.
    """
    r = dict(READINGS)
    if readings:
        r.update(readings)
    when_tt = time.localtime(when)
    date_stamp = (when_tt.tm_mday + (when_tt.tm_mon << 5)
                  + ((when_tt.tm_year - 2000) << 9))
    time_stamp = when_tt.tm_hour * 100 + when_tt.tm_min

    rec = bytearray(b"\xff" * 52)
    rec[0:2] = struct.pack("<H", date_stamp)
    rec[2:4] = struct.pack("<H", time_stamp)
    rec[4:6] = struct.pack("<h", round(r["outTemp"] * 10))     # outTemp
    rec[6:8] = struct.pack("<h", round(r["outTemp"] * 10))     # high outTemp
    rec[8:10] = struct.pack("<h", round(r["outTemp"] * 10))    # low outTemp
    rec[10:12] = struct.pack("<H", 0)                          # rain clicks
    rec[12:14] = struct.pack("<H", round(r["rainRate"] * 100))
    rec[14:16] = struct.pack("<H", round(r["barometer"] * 1000))
    rec[16:18] = struct.pack("<H", int(r["radiation"]))
    rec[18:20] = struct.pack("<H", 0)                          # wind samples
    rec[20:22] = struct.pack("<h", round(r["inTemp"] * 10))
    rec[22] = int(r["inHumidity"])
    rec[23] = int(r["outHumidity"])
    rec[24] = int(r["windSpeed"])
    rec[25] = int(r["windGust10"])
    rec[26] = int(r["windDir"] / 22.5)                         # gust direction
    rec[27] = int(r["windDir"] / 22.5)                         # wind direction
    rec[28:30] = struct.pack("<H", 0)                          # UV
    rec[30:32] = struct.pack("<H", 0)                          # ET
    rec[42] = 0x00                                             # Rev B
    return bytes(rec)


def archive_page(records: list[bytes], sequence: int = 0) -> bytes:
    """A 267-byte page: one sequence byte, five records, four unused, CRC."""
    page = bytearray(265)
    page[0] = sequence
    for index, record in enumerate(records[:5]):
        page[1 + 52 * index:53 + 52 * index] = record
    # Slots a cleared logger has never written read as 0xff, and the driver
    # tells that apart from a wrap-around. Zeros here would test neither.
    for index in range(len(records), 5):
        page[1 + 52 * index:53 + 52 * index] = b"\xff" * 52
    return with_crc(bytes(page))


class FakeSerial:
    """`serial.Serial`, answering as a console would.

    The driver's own `SerialWrapper` sits on top of this untouched, so what is
    exercised is its retry logic, its CRC checking and its decoding -- not a
    shortcut past them.
    """

    def __init__(self, port=None, baudrate=19200, timeout=4.0, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.out = bytearray()        # what the console is waiting to be read
        self.written = []             # every command, for a test to inspect
        self.loops_left = 0
        #: Wind speeds in mph, one per LOOP packet, cycled. Empty means the
        #: console reports the same thing every time, which is what every
        #: other test here wants.
        self.winds: list[int] = []
        self.archive_pages: list[bytes] = []
        self.dmpaft_pages = 0
        self.dmpaft_index = 0
        self.closed = False

    # -- what pyserial offers ------------------------------------------

    def write(self, data):
        self.written.append(bytes(data))
        self._respond(bytes(data))
        return len(data)

    def read(self, count=1):
        # A real port returns what it has and lets the caller complain; the
        # driver checks the length itself and raises WeeWxIOError. Behaving
        # differently here would hide that check.
        got, self.out = bytes(self.out[:count]), self.out[count:]
        return got

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.out = bytearray()

    def reset_output_buffer(self):
        pass

    # pyserial's older names, which the driver may use depending on version.
    flushInput = reset_input_buffer
    flushOutput = reset_output_buffer

    def close(self):
        self.closed = True

    # -- the console ---------------------------------------------------

    def _respond(self, data: bytes) -> None:
        """Queue what a console would send back for this command."""
        # A bare line feed is the wake-up, and it is also what cancels a LOOP
        # in progress. Both answer the same way.
        if data in (b"\n", b"\n\n\n"):
            self.loops_left = 0
            self.out += b"\n\r"
            return

        if data.startswith(b"WRD"):            # what hardware is this
            self.out += ACK + bytes([HARDWARE_TYPE])
            return

        if data.startswith(b"EEBRD "):
            _, offset, count = data.strip().split(b" ")
            at, size = int(offset, 16), int(count, 16)
            self.out += ACK + with_crc(self._eeprom(at, size))
            return

        if data.startswith((b"LOOP ", b"LPS ")):
            self.loops_left = int(data.strip().split(b" ")[-1])
            self.out += ACK
            for n in range(self.loops_left):
                # A wind that changes between packets, when a test asks for
                # one. A console whose readings never move cannot show what
                # `VantageService` is for: it keeps the highest gust seen
                # since the last archive boundary, so a constant wind and a
                # broken reset look exactly alike.
                readings = None
                if self.winds:
                    readings = {"windSpeed": self.winds[n % len(self.winds)]}
                self.out += loop_packet(readings)
            return

        if data.startswith(b"DMPAFT"):
            # Two exchanges: an ACK for the command, then the six byte reply
            # to the timestamp the driver sends next.
            self.out += ACK
            self._expect_dmpaft_stamp = True
            return

        if getattr(self, "_expect_dmpaft_stamp", False) and len(data) == 6:
            self._expect_dmpaft_stamp = False
            self.out += ACK + with_crc(
                struct.pack("<HH", self.dmpaft_pages, self.dmpaft_index))
            for page in self.archive_pages:
                self.out += page
            return

        if data in (b"\x06", b"\x1b"):         # ACK to send the next page
            return

        # Anything else gets the console's own "did not understand": a bare
        # ACK. A simulator that stayed silent would look like a dead port and
        # send the driver into its retry loop, which is a different failure
        # from the one being tested.
        self.out += ACK

    def _eeprom(self, at: int, size: int) -> bytes:
        if at == 0x19:                          # the transmitter table
            return TRANSMITTERS[:size].ljust(size, b"\x00")
        known = EEPROM.get(at)
        if known is not None:
            return known[:size].ljust(size, b"\x00")
        return b"\x00" * size

    # -- what a test sets up -------------------------------------------

    def load_archive(self, timestamps, per_page: int = 5) -> None:
        """Give the console some logged records to hand over."""
        records = [archive_record(ts) for ts in timestamps]
        pages = []
        for at in range(0, len(records), per_page):
            pages.append(archive_page(records[at:at + per_page],
                                      sequence=len(pages)))
        self.archive_pages = pages
        self.dmpaft_pages = len(pages)
        self.dmpaft_index = 0


def serial_module(port_holder: dict) -> object:
    """A stand-in for `serial`, whose `Serial` is the console above.

    `port_holder` is filled in with the port that gets opened, so a test can
    look at what the driver sent.
    """
    import types

    module = types.ModuleType("serial")

    class SerialException(Exception):
        pass

    def Serial(*args, **kwargs):
        port = FakeSerial(*args, **kwargs)
        port_holder["port"] = port
        return port

    module.Serial = Serial
    module.SerialException = SerialException
    module.serialutil = types.SimpleNamespace(SerialException=SerialException)
    module.PARITY_NONE, module.STOPBITS_ONE, module.EIGHTBITS = "N", 1, 8
    return module
