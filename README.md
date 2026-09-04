# weewx-evo-weewx-driver

Run any WeeWX driver as a weewx-evo collector. The fourteen in WeeWX's own
tree and the hundred beside them: a serial Vantage, a Fine Offset on the USB
bus, an RTL-SDR listening to whatever the neighbourhood transmits.

**WeeWX does not have to be installed.**

```bash
pip install git+https://github.com/weewx-evo/weewx-evo-weewx-driver

weewx-evo-weewx-driver hardware                    # what is plugged in
weewx-evo-weewx-driver check --driver-file ./vantage.py
weewx-evo-weewx-driver run --collector shed
```

## Why WeeWX need not be installed

A driver writes `import weewx` at the top, and counted across all thirteen in
the tree that import is nearly all ceremony:

| | |
|---|---|
| `weewx.drivers` | 13x, three base classes, practically empty |
| `weewx.WeeWxIOError` | 12x, one exception, three lines |
| `weewx.wxformulas` | 11x, of which **one** function, twelve lines |
| `weeutil.weeutil` | 10x, `to_bool`, `to_int`, `timestamp_to_string` |
| `weewx.METRIC` / `US` | 7x, the numbers 16 and 1 |

`weewxnames.py` stands in for them. fousb is the most frugal at four names,
Vantage the heaviest at twenty, and all thirteen import against that one
file -- measured in a process where `import weewx` raises.

Where WeeWX *is* installed, WeeWX wins. `install()` fills in what is missing
and takes nothing that is already there.

**pyusb and pyserial are still needed** where the hardware needs them. That
is the driver's own dependency, not this package's, and `hardware` names the
package rather than the import: the import is `usb` and the package is
`pyusb`, which nobody guesses.

## Its own process, and why

In WeeWX the driver lives in the engine, so a serial port that stops
answering stops everything. Here it delivers over the loopback like any other
collector: it may hang, crash or leak, and the listener and the archiver
carry on. It need not even be on the same machine as weewx-evo.

## The form comes out of the driver

Every WeeWX driver carries a `confeditor` whose `default_stanza` is exactly a
form: each option, a working default, and the author's own comment about what
it is. This reads that -- with an AST walk, never by importing -- so a driver
whose library is missing still has a page, which is the ordinary case on a
machine that has not installed pyusb yet.

Add a collector on the settings page, choose the hardware, and its own
settings appear below.

## What was measured

Three simulated devices, each a layer *under* the driver:

| | | |
|---|---|---|
| `vantagesim.py` | serial port | wake-up, EEPROM with CRC, 99-byte LOOP, 267-byte pages |
| `fousbsim.py` | USB HID | 64 KB of memory, address in the control message, ring buffer |
| `sdrsim.py` | a **program** | weewx-sdr reads `rtl_433`'s stdout, so the simulator is a real subprocess |

And the step that turns the rest into proof: the same bytes once through the
stand-in and once through WeeWX' own code, compared field by field. Vantage
42 of 42, fousb 14 of 14, weewx-sdr 4 of 4.

```bash
python tools/alldrivers_test.py   # all thirteen, twice, compared
python tools/standin_test.py      # the same driver, with no WeeWX installed
python tools/vantage_test.py      # a Davis, down to the serial port
```

The tests that compare against WeeWX skip where it is absent, and say so.

## Licence

GPL-3.0-or-later, the same as WeeWX and weewx-evo.

`weewxshim.py`, `weewxnames.py` and `weewxdrivers.py` were written for
weewx-evo and moved here unchanged. The drivers they run are WeeWX's,
downloaded as the files they are -- this package contains none of them.
