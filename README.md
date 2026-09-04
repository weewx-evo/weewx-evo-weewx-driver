# weewx-evo-weewx-driver

Run any WeeWX driver from weewx-evo. The thirteen in WeeWX's own tree are
**shipped here**, and the hundred beside them are one file away: a serial
Vantage, a Fine Offset on the USB bus, an RTL-SDR listening to whatever the
neighbourhood transmits.

**WeeWX does not have to be installed.**

```bash
# pick the one your hardware needs: [serial], [usb], or [all]
pip install "weewx-evo-weewx-driver[serial] @ git+https://github.com/weewx-evo/weewx-evo-weewx-driver"

weewx-evo-weewx-driver hardware                    # what this machine can run
weewx-evo-weewx-driver check --driver-file ./vantage.py
weewx-evo-weewx-driver run --collector shed
```

## The thirteen are in here

| | |
|---|---|
| Davis | Vantage Pro, Pro2, Vue |
| Fine Offset | the USB consoles, badged Ambient, Watson, National Geographic |
| AcuRite | 01025, 01035, 02032C, 02064C |
| Oregon Scientific | WMR100, WMRS200, WMR300, WMR9x8, WM-918, WMR-968 |
| LaCrosse | WS-23xx, WS-28xx |
| Hideki TE923 | badged Honeywell, Meade, IROX Pro X, Mebus, TFA Nexus |
| Peet Bros | Ultimeter |
| RainWise | CC3000 |
| ADS | WS1 |

They are WeeWX' own files, copied byte for byte from a release --
`src/weewx_evo_weewx_driver/drivers/PROVENANCE` says which one and carries a
SHA-256 for each. **WeeWX' own `LICENSE.txt` is in that directory too**,
from the same release: each of the thirteen says "See the file LICENSE.txt
for your full rights" in its first five lines, and these are somebody else's
GPL sources. A workflow fetches them again every week and opens a pull
request when a release moves them, so the copy going stale is a diff to read
rather than something nobody notices.

**An installed WeeWX does not override them.** The file *is* the driver, so a
machine that happens to have WeeWX 5.2 beside this would otherwise run a
driver these tests never measured, with nothing anywhere to say so. What
*does* win is a file you put in the data directory yourself: a driver patched
for your own hardware has to be runnable.

Which libraries a driver needs is its own business, and it differs per
console. Six of the thirteen import `usb`, five import `serial`, and ws23xx
opens its own serial port with `fcntl` and `termios` -- so those are extras
rather than dependencies, and `hardware` prints `needs pyusb` beside a driver
whose library is missing.

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

Where WeeWX *is* installed, WeeWX' own names win. `install()` fills in what is
missing and takes nothing that is already there -- taking the real
`weewx.units` over a transcription of it is always right.

That is the opposite of the rule for the driver *files* above, and the two
are different questions: there the real thing is better, here the file is the
driver and which release it came from decides what the hardware does.

**pyusb and pyserial** are extras, not dependencies: `[usb]`, `[serial]` or
`[all]`. `hardware` names the package rather than the import when one is
missing -- the import is `usb` and the package is `pyusb`, which nobody
guesses.

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
43 of 43, fousb 19 of 19, weewx-sdr 4 of 4, and all thirteen agree either way.

Vantage is measured twice over, because it is two things: `loader()` returns
`VantageService`, which inherits from the driver *and* from `StdService` and
binds three events in its constructor. A Vantage LOOP packet carries no
`windGust` of its own -- the service computes it as the highest windSpeed
since the last archive boundary, and clears it in `END_ARCHIVE_PERIOD`. So a
shim that swallowed events would deliver packets with the gust missing, and
one that skipped the second event would report a gust that only ever climbs.
Both are checked against the real service.

```bash
PYTHONPATH=/path/to/weewx-evo/src python tools/runtests.py
```

Eight tests, and the path matters: everything here imports weewx-evo, and
the run says so rather than failing eight times for the same reason. What
compares against WeeWX skips where WeeWX is absent, and says which.

    8/8 passed

The image to run them in is weewx-evo's own `docker/Dockerfile`, which has
WeeWX in it -- a test that cannot compare against the thing it was
transcribed from is an opinion.

## Licence

GPL-3.0-or-later, the same as WeeWX and weewx-evo.

`weewxshim.py`, `weewxnames.py` and `weewxdrivers.py` were written for
weewx-evo and moved here unchanged.

`src/weewx_evo_weewx_driver/drivers/` is **WeeWX' code**, redistributed under
the same licence and not modified. WeeWX' own `LICENSE.txt` is in that
directory, taken from the release the drivers came from — which is the file
each of them points at. `PROVENANCE` names that release and carries a digest
per file; the copyright is Tom Keffer's and the contributors', as each file's
own header says.
