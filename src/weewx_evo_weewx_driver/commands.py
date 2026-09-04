#
#    Copyright (c) 2026 Manuel Hilgert
#
#    See the file LICENSE for your full rights.
#
"""The commands, exactly as they were in the core.

Copied rather than rewritten, and that is the point: what makes a fix able
to travel between these two repositories is that both sides are the same
code. The imports below are the whole adaptation.

`settings_for` and `Settings` stay the core's: they are how every command in
this program resolves a setting, and a second answer to "where does --live
come from" is two answers that disagree on the day somebody changes one.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from weewx_evo import collectors
from weewx_evo.cli import (
    _collector_settings,
    archive_path_of,
    read_archives,
    selected_archive,
    settings_for,
)

from . import weewxdrivers

log = logging.getLogger(__name__)


def _shim_config(args: argparse.Namespace) -> dict:
    """The configuration a WeeWX driver is built from. Two ways to one shape.

    **A file, when there is one.** A WeeWX driver reads its settings out of
    the section named after it, and on an installation that has been running
    those settings are the ones that already work. Nothing here writes that
    file or copies out of it.

    **Otherwise, built from what was chosen.** `weewxdrivers.py` reads the
    driver's own configuration editor, so the section is the driver's own
    defaults under the driver's own section name -- exactly what `weectl`
    would have written. The driver cannot tell the difference, because there
    is none.

    The second is what makes one USB console cost one USB console. Requiring
    the file meant writing a configuration for WeeWX in order not to install
    WeeWX, which is the thing `weewxnames.py` exists to avoid.
    """
    from . import weewxdrivers, weewxshim

    # An argument beats the collector's own setting. Same order as
    # everything else: what somebody just typed wins for this run.
    stored = _collector_settings(args)
    path = args.conf or stored.get("conf")
    if path:
        if not Path(path).exists():
            raise SystemExit(f"no weewx.conf at {path}. Say where it is "
                             f"with --conf.")
        return weewxshim.read_config(path)

    module = getattr(args, "driver", None) or stored.get("driver")
    from_file = getattr(args, "driver_file", None) or stored.get("driver_file")
    if module or from_file:
        # A file on its own is enough. It names itself -- that is what
        # `DRIVER_NAME` is for -- so making somebody type the module path as
        # well would be asking for a fact the file already carries, and one
        # they can get wrong.
        found = None
        if module:
            # `Settings.get` and not the raw file: it anchors a relative path
            # against the configuration file it was written in, so this looks
            # where the service looks. The settings page has to do that part
            # itself -- see `collectors._driver_directory`.
            cfg = settings_for(args)
            beside = archive_path_of(
                args, selected_archive(read_archives(args, cfg), None))
            where = weewxdrivers.directory(beside=beside)
            found = weewxdrivers.by_module(str(module), where)
        if found is None and from_file:
            found = weewxdrivers.read(from_file, str(module or ""))
        if found is not None and not found.problem:
            values = collectors.driver_settings(settings_for(args),
                                                getattr(args, "collector", "")
                                                or "")
            return weewxdrivers.config_dict_for(found, values)

    # Neither, so the usual place -- which is right on a machine that has
    # WeeWX, and is where somebody who typed nothing expects to be looked.
    usual = "/etc/weewx/weewx.conf"
    if Path(usual).exists():
        return weewxshim.read_config(usual)
    raise SystemExit(
        "nothing says what to run: choose the hardware on the collector's "
        "page, or name a weewx.conf with --conf, or a driver with --driver.")


def _shim_options(args: argparse.Namespace) -> dict:
    """What to build the shim with: the collector's settings under arguments.

    Gathered in one place because three commands take them and a collector
    whose `driver_file` reached `check` but not `run` would look configured
    and record nothing.
    """
    one = _collector_settings(args)
    name = getattr(args, "collector", None)

    def pick(argument: str, key: str, default=None):
        given = getattr(args, argument, None)
        return given if given not in (None, "", 0) else one.get(key, default)

    return {
        "module_name": pick("driver", "driver") or None,
        "driver_file": pick("driver_file", "driver_file") or None,
        "source": pick("source", "source") or None,
        "catchup_seconds": int(pick("catchup", "catchup", 0) or 0),
        "batch_seconds": float(pick("batch", "batch", 5.0) or 5.0),
        # The endpoint, and the reason a collector is named at all. Packets
        # arriving at `/<token>/shed/` are recorded as driver `shed`, which
        # is what a station is matched on together with its identity. An
        # ad-hoc run has no name and goes in as an envelope.
        "as_driver": name or "json",
    }


def cmd_weewx_driver_list(args: argparse.Namespace) -> int:
    """What this weewx.conf asks for, and whether it can be built."""
    from . import weewxnames, weewxshim

    if weewxnames.installed():
        import weewx
        version = f"WeeWX {getattr(weewx, '__version__', 'unknown')}"
    else:
        # Not an error any more. What a driver imports is a handful of
        # constants and two exceptions, and `weewxnames` has them -- so the
        # thing to say is which one it will run against, since that decides
        # what a later failure means.
        version = "no WeeWX installed; drivers run against the stand-in"

    config_dict = _shim_config(args)
    print(version)
    station = (config_dict.get("Station") or {}).get("station_type", "?")
    print(f"station_type: {station}")
    try:
        module = args.driver or weewxshim.driver_module_name(config_dict)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    print(f"driver:       {module}")

    section = config_dict.get(station) or {}
    settings = [k for k in section if k != "driver"]
    if settings:
        print(f"its settings: {', '.join(sorted(settings))}")
    return 0


def _weewx_driver_dir(args: argparse.Namespace):
    """Where this installation keeps WeeWX driver files."""
    cfg = settings_for(args)
    return weewxdrivers.directory(
        configured=getattr(args, "weewx_driver_dir", None),
        beside=archive_path_of(
            args, selected_archive(read_archives(args, cfg), None)))


def cmd_weewx_driver_hardware(args: argparse.Namespace) -> int:
    """Every WeeWX driver on this machine, and what each one asks for.

    The command behind the hardware list on the collector's page, and the
    answer to "will it read mine". Nothing is imported to produce it: the
    drivers are read, so a driver whose library is not installed is listed
    with what it needs rather than left out.
    """
    where = _weewx_driver_dir(args)
    found = weewxdrivers.available(where)
    if not found:
        print("No WeeWX drivers on this machine.")
        print(f"Put a driver file in {where}, or install WeeWX.")
        return 0

    wanted = getattr(args, "name", None)
    for one in found:
        if wanted and wanted.lower() not in (one.name.lower(),
                                             one.module.lower()):
            continue
        needs = f"  needs {one.needs}" if one.needs else ""
        print(f"{one.name:16s} {one.module}{needs}")
        if one.problem:
            print(f"                 {one.problem}")
            continue
        if one.about:
            print(f"                 {one.about}")
        if not wanted:
            continue
        # Named, so print the form: every option, its default, and what the
        # driver's own author says it is. This is what the page shows, in the
        # place somebody is when they have no page.
        for setting in one.settings:
            mark = " (rarely needed)" if setting.advanced else ""
            if setting.when:
                on, values = setting.when
                mark += f" (only when {on} is {' or '.join(values)})"
            print(f"    {setting.name:20s} = {setting.value}{mark}")
            for line in setting.help:
                print(f"      {line}")
            if setting.choices:
                print("      one of: "
                      + ", ".join(value for value, _ in setting.choices))
    if not wanted:
        print(f"\n{len(found)} driver(s). Name one to see its settings.")
    return 0


def _fetch_driver(url: str, into: Path) -> Path | None:
    """Download one driver file. Returns where it landed, or None on failure.

    http and https only. `urlopen` also speaks `file:` and `ftp:` and
    whatever else is registered, so a "driver URL" could read a local file
    and hand it back as a download -- which is not what the word means. The
    same reasoning as `userdrivers._download`, which is the other half of
    this and takes a zip rather than a file.
    """
    import urllib.parse
    import urllib.request

    if urllib.parse.urlparse(url).scheme.lower() not in ("http", "https"):
        print(f"{url}: only http and https can be downloaded", file=sys.stderr)
        return None
    name = Path(urllib.parse.urlparse(url).path).name or "driver.py"
    if not name.endswith(".py"):
        print(f"{url} does not name a .py file. A WeeWX driver is one "
              f"Python file.", file=sys.stderr)
        return None
    target = into / name
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
            target.write_bytes(response.read())
    except Exception as exc:
        print(f"could not download {url}: {exc}", file=sys.stderr)
        return None
    return target


def cmd_weewx_driver_install(args: argparse.Namespace) -> int:
    """Put a WeeWX driver file where this installation looks for one.

    A driver is one file, and this is the third place `available` looks --
    the one that matters on a machine with no WeeWX, where there is nothing
    to take a driver out of.

    Separate from `driver install`, which takes ours. The two meet different
    contracts: `load(registry)` there, `loader()` and `DRIVER_NAME` here. One
    command answering to both would have to guess which was meant, and the
    guess would be wrong the first time a file met neither.

    A URL is taken as well as a path, because that is where a driver comes
    from on the machine this exists for. Telling somebody to install WeeWX in
    order to copy one file out of it is the thing being avoided.
    """
    import shutil
    import tempfile

    where = _weewx_driver_dir(args)
    given = str(args.source)
    with tempfile.TemporaryDirectory() as staging:
        if given.lower().startswith(("http://", "https://")):
            # A URL, because that is where a driver is for the machine this
            # is for. Somebody with one USB console and no WeeWX has to get
            # the file from somewhere, and telling them to install WeeWX in
            # order to copy one file out of it is the thing this avoids.
            source = _fetch_driver(given, Path(staging))
            if source is None:
                return 1
        else:
            source = Path(given).expanduser()
            if not source.is_file():
                print(f"no file at {source}", file=sys.stderr)
                return 1

        if not weewxdrivers.is_a_driver(source):
            # Read, not imported, and that is the point: a file this cannot
            # make sense of must not be run to find out what it is. It is
            # also why a URL is safe to take -- nothing downloaded is
            # executed to decide whether to keep it.
            print(f"{given} is not a WeeWX driver: a driver has a `loader` "
                  f"function and a `DRIVER_NAME`.", file=sys.stderr)
            return 1

        where.mkdir(parents=True, exist_ok=True)
        target = where / source.name
        if target.exists() and not args.force:
            print(f"{target} is already there. --force to replace it.",
                  file=sys.stderr)
            return 1
        shutil.copy2(source, target)
    one = weewxdrivers.read(target, f"weewx.drivers.{target.stem}")
    print(f"{one.name} installed at {target}")
    if one.needs:
        print(f"It reaches its hardware through {one.needs}. Install it "
              f"where this collector runs.")
    print(f"Choose it on a collector's page, or run it with "
          f"--driver-file {target}")
    return 0


#: What a driver imports to reach hardware, and what to install for it. Read
#: from `weewxdrivers`, where the hardware list uses the same table to say
#: what a driver would need before anybody chooses it. Two copies of it
#: disagree the day a driver gains a dependency, and the half that is wrong
#: is whichever one nobody hit that day.
_HARDWARE_LIBS = weewxdrivers.NEEDS


def cmd_weewx_driver_check(args: argparse.Namespace) -> int:
    """Build the driver, take a few packets, deliver nothing.

    The question this answers is the one worth asking before a service is
    installed: does this driver, this configuration and this hardware work
    together at all. It writes nothing and needs no listener, so it is also
    what to run when something has stopped arriving.
    """
    from . import weewxshim

    config_dict = _shim_config(args)
    chosen = _shim_options(args)
    try:
        found = weewxshim.probe(config_dict, chosen["module_name"],
                                count=args.count, source=chosen["source"],
                                driver_file=chosen["driver_file"])
    except Exception as exc:
        print(f"the driver could not be run: {exc}", file=sys.stderr)
        # A driver talks to hardware, and the library for that is the one
        # thing no stand-in can supply. Naming the package is the difference
        # between a minute and an afternoon, and the module name is not it:
        # `import usb` is pyusb, and nobody guesses that.
        wanted = getattr(exc, "name", None)
        if isinstance(exc, ModuleNotFoundError) and wanted in _HARDWARE_LIBS:
            print(f"\nThat is the library it uses to reach the hardware. "
                  f"Install it with:\n\n    pip install "
                  f"{_HARDWARE_LIBS[wanted]}\n", file=sys.stderr)
        log.debug("driver probe failed", exc_info=True)
        return 1

    print(f"driver:           {found['driver']}")
    print(f"source name:      {found['source']}")
    print(f"archive interval: {found['archive_interval']}s")
    print(f"callbacks bound:  {found['callbacks']}")
    if found["standing_in"]:
        print(f"running against:  a stand-in for "
              f"{', '.join(found['standing_in'])}")
    if getattr(args, "collector", None):
        print(f"delivers as:      {args.collector}  "
              f"(announce a station with driver = {args.collector!r})")
    # WeeWX asks this as `record_generation`; here it is a fact about the
    # console rather than a choice, because the archiver builds every record
    # anyway and takes the console's where there is one.
    if found.get("logs_its_own"):
        print("keeps its own log: yes -- `catchup` can fill an outage")
    else:
        print("keeps its own log: no -- `catchup` has nothing to fetch")
    print(f"packets:          {found['packets']}")
    print(f"usUnits:          {found['usUnits']}")
    print(f"fields ({len(found['fields'])}): {', '.join(found['fields'])}")
    if found["sample"]:
        print("\nfirst packet, first few fields:")
        for name, value in found["sample"].items():
            print(f"  {name:<20} {value}")
    if not found["packets"]:
        print("\nThe driver built but produced nothing. For hardware that is "
              "usually\nthe wrong port or a console that is asleep.")
        return 1
    return 0


def cmd_weewx_driver_run(args: argparse.Namespace) -> int:
    """Run the driver and deliver what it produces to the listener.

    This is the long-running form, meant for a service file. It stays in its
    own process on purpose: that is the whole reason a WeeWX driver is safe to
    run here, and putting it inside `serve` would give back exactly what the
    arrangement buys.
    """
    import signal

    from . import weewxshim

    cfg = settings_for(args)
    config_dict = _shim_config(args)
    # From the settings, and deliberately not from a --token argument the way
    # `url` and `listen` take one. Those are typed and over in a second; this
    # is a service that runs for months, and an argument would put the token
    # in /proc/<pid>/cmdline, where `ps` prints it for every user on the
    # machine. WEEWX_EVO_TOKEN in the unit's EnvironmentFile, or the
    # configuration file.
    token = cfg.get("token")
    port = args.port or cfg.get("port") or 8000
    host = args.host or "127.0.0.1"

    if not token:
        print("No upload token is set, so the listener would refuse every",
              file=sys.stderr)
        print("packet and this would run and deliver nothing.", file=sys.stderr)
        print(file=sys.stderr)
        print("  WEEWX_EVO_TOKEN=...  in the environment, or `token` in the",
              file=sys.stderr)
        print("  configuration file. Not an argument: it would show up in ps.",
              file=sys.stderr)
        return 1

    chosen = _shim_options(args)
    shim = weewxshim.Shim(
        config_dict, chosen["module_name"], source=chosen["source"],
        host=host, port=port, token=token,
        batch_seconds=chosen["batch_seconds"],
        catchup_seconds=chosen["catchup_seconds"], dry_run=args.dry_run,
        driver_file=chosen["driver_file"], as_driver=chosen["as_driver"])

    def bye(_signum, _frame):
        # The generator blocks on the hardware, so the flag is read between
        # packets. A console that has gone quiet still needs the second
        # signal, which is the supervisor's job and not ours.
        shim.stop()

    signal.signal(signal.SIGINT, bye)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, bye)

    where = "nowhere (--dry-run)" if args.dry_run else f"{host}:{port}"
    print(f"running {shim.module_name}, delivering to {where}")
    sent = shim.run(limit=args.limit)
    print(f"{sent} packet(s) delivered in {shim.delivered_batches} batch(es)")
    if shim.dropped:
        print(f"{shim.dropped} packet(s) dropped while the listener was "
              f"unreachable", file=sys.stderr)
    return 0
