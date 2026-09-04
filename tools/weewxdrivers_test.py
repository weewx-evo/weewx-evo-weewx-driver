#!/usr/bin/env python3
"""Reading a WeeWX driver's own form, and comparing it against the real thing.

The form comes out of the driver: its default stanza is every option it takes,
with a working default and a comment saying what it is, written by whoever
wrote the driver. Getting that wrong is not visible -- a missing option is an
empty section in a configuration nobody looks at, and a wrong default is a
serial port that is not there.

So it is compared, twice over, the way `unitcheck.py` compares against WeeWX:

    against configobj   the library the stanza is written for. Every option,
                        every value, and every comment.
    against `weectl`    the section it writes is the one a driver is built
                        from, so `config_dict_for` is compared against a
                        configuration read off disk.

Neither comparison can run without something to compare to, so both say what
they skipped rather than passing quietly.

    python tools/weewxdrivers_test.py
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from weewx_evo_weewx_driver import weewxdrivers as wd  # noqa: E402

failures = 0
skipped: list[str] = []


def check(what: str, got: object, want: object) -> bool:
    global failures
    ok = got == want
    tail = "" if ok else f"  (wanted {want!r})"
    print(f"  {'ok  ' if ok else 'FAIL'} {what}: {got!r}{tail}")
    failures += 0 if ok else 1
    return ok


def note(what: str) -> None:
    print(f"  --   {what}")
    skipped.append(what)


def driver_files() -> list[Path]:
    """Every driver WeeWX ships, wherever WeeWX is.

    Found on the path rather than imported: `weewx/drivers/__init__.py` holds
    three base classes, so importing the package costs nothing, and importing
    the drivers is the thing this module exists not to do.
    """
    try:
        import weewx.drivers
    except ImportError:
        return []
    out: list[Path] = []
    for one in getattr(weewx.drivers, "__path__", []) or []:
        out.extend(sorted(p for p in Path(one).glob("*.py")
                          if not p.stem.startswith("_")))
    return out


# -- the parser against configobj -----------------------------------------

def against_configobj(files: list[Path]) -> None:
    """Every option, value and comment, compared with the library itself.

    This is the one that would catch a parser that looks right on Vantage and
    drops the second half of somebody else's stanza.
    """
    print("\nthe stanza parser, against configobj")
    try:
        import configobj
    except ImportError:
        note("configobj is not installed; the parser is not compared")
        return

    import io

    options = comments = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        stanza = wd._stanza_source(tree)
        if stanza is None:
            continue
        section, settings, nested = wd.parse_stanza(stanza)
        theirs = configobj.ConfigObj(io.StringIO(stanza.strip()))

        want_sections = list(theirs.sections)
        if not check(f"{path.stem}: section", section,
                     want_sections[0] if want_sections else ""):
            continue
        held = theirs[section]
        # Everything, `driver` included. This is the parser, and it reads what
        # is there; leaving `driver` out is a decision `read` makes one layer
        # up, and a comparison that made it here would not be a comparison.
        ours = {one.name: one.value for one in settings}
        want = {key: held[key] for key in held.scalars}
        if not check(f"{path.stem}: options", ours, want):
            continue
        options += len(want)

        # The comment above each option, which is the field's help text. Read
        # the same way: everything after the last blank line, hashes off.
        for one in settings:
            check(f"{path.stem}.{one.name}: help", list(one.help),
                  their_help(held, one.name))
            comments += 1

        check(f"{path.stem}: sub-sections", sorted(nested),
              sorted(held.sections))

    print(f"  {options} options and {comments} comments compared")


def their_help(section: object, key: str) -> list[str]:
    """The comment configobj kept above an option, as this parser reads it."""
    lines = section.comments.get(key) or []  # type: ignore[attr-defined]
    start = 0
    for index, line in enumerate(lines):
        if not line.strip():
            start = index + 1
    kept = []
    for line in lines[start:]:
        text = line.strip().lstrip("#").strip()
        if text and set(line.strip()) != {"#"}:
            kept.append(text)
    return kept


# -- the form -------------------------------------------------------------

def every_driver_has_a_form(files: list[Path]) -> None:
    """All thirteen read, and each one offers something to fill in.

    A driver that yields nothing is the failure this cannot see otherwise: the
    page would show a heading and no fields, which reads as a driver that needs
    no configuring rather than as a parser that gave up.
    """
    print("\nevery driver WeeWX ships")
    for path in files:
        if not wd.is_a_driver(path):
            check(f"{path.stem}: is a driver", False, True)
            continue
        one = wd.read(path, f"weewx.drivers.{path.stem}")
        if one.problem:
            check(f"{path.stem}: reads", one.problem, "")
            continue
        # A driver with no options at all would be a parser failure, not a
        # simple driver: every one of the thirteen asks for at least a model.
        check(f"{one.name}: has fields", len(one.settings) >= 1, True)


def the_conditional_ones(files: list[Path]) -> None:
    """A Vantage takes a port or a host, and says so in its own editor.

    The measurement that matters: `host` is in the `else` branch, so it is
    conditional only because `CHOICES` says what the other value is. Without
    that the form offers a port and a host at once, which the driver says never
    happens -- and a station configured with both is one where the wrong one is
    silently ignored.
    """
    print("\nwhich options depend on which")
    by_name = {p.stem: p for p in files}
    if "vantage" not in by_name:
        note("no vantage.py to read")
        return

    vantage = wd.read(by_name["vantage"], "weewx.drivers.vantage")
    when = {one.name: one.when for one in vantage.settings if one.when}
    check("vantage: port", when.get("port"), ("type", ("serial",)))
    check("vantage: host", when.get("host"), ("type", ("ethernet",)))
    check("vantage: type is not conditional", "type" in when, False)
    check("vantage: nine below the rule are advanced",
          sum(1 for one in vantage.settings if one.advanced), 9)

    if "ws1" in by_name:
        # The other side of the same rule. A WS1 asks for a port under every
        # value `mode` takes, so it is not conditional -- it only changes what
        # it suggests. A reader that took any `if` as a condition would hide
        # the port for two of the three modes.
        ws1 = wd.read(by_name["ws1"], "weewx.drivers.ws1")
        conditional = [one.name for one in ws1.settings if one.when]
        check("ws1: nothing is conditional", conditional, [])


def the_choices(files: list[Path]) -> None:
    """CHOICES is a copy of something a driver says, so it is checked.

    Each named option still has to exist, and the driver's own default still
    has to be one of the values. A driver that gains a choice, or renames an
    option, makes this say so rather than quietly offering a list that is one
    short.
    """
    print("\nthe options with a fixed set of values")
    by_module = {}
    for path in files:
        module = f"weewx.drivers.{path.stem}"
        if module in wd.CHOICES:
            by_module[module] = wd.read(path, module)

    for module, wanted in wd.CHOICES.items():
        one = by_module.get(module)
        if one is None:
            note(f"{module} is not installed here")
            continue
        have = {setting.name: setting for setting in one.settings}
        for key, values in wanted.items():
            setting = have.get(key)
            if not check(f"{one.name}.{key}: still an option",
                         setting is not None, True):
                continue
            offered = [wd._value_of(each) for each in values]
            check(f"{one.name}.{key}: its own default is one of them",
                  setting.value in offered, True)


# -- what a driver is built from ------------------------------------------

def the_config_dict(files: list[Path]) -> None:
    """The section built here against the one read off a weewx.conf.

    This is the comparison the whole module is for. A driver cannot tell where
    its configuration came from, and the way to know that is true is to build
    it both ways and compare -- not to read the code and agree with it.
    """
    print("\nthe configuration a driver is built from")
    by_name = {p.stem: p for p in files}
    if "vantage" not in by_name:
        note("no vantage.py to read")
        return
    one = wd.read(by_name["vantage"], "weewx.drivers.vantage")

    built = wd.config_dict_for(one, {"port": "/dev/ttyS3"})
    check("station_type is the driver's own section name",
          built["Station"]["station_type"], "Vantage")
    check("the section is named the same", "Vantage" in built, True)
    check("what was chosen is in it", built["Vantage"]["port"], "/dev/ttyS3")
    check("what was not chosen keeps the driver's default",
          built["Vantage"]["baudrate"], "19200")
    check("the module is named", built["Vantage"]["driver"],
          "weewx.drivers.vantage")

    from weewx_evo_weewx_driver import weewxshim

    check("the shim finds the driver in it",
          weewxshim.driver_module_name(built), "weewx.drivers.vantage")

    # And the same section, written out and read back the way a weewx.conf is.
    # A stanza this cannot read back is one nobody can paste into a file.
    with tempfile.TemporaryDirectory() as where:
        path = Path(where) / "weewx.conf"
        path.write_text("[Station]\n    station_type = Vantage\n\n"
                        + wd.stanza_text(one, {"port": "/dev/ttyS3"}),
                        encoding="utf-8")
        read_back = weewxshim.read_config(path)
        check("written out and read back: the driver",
              weewxshim.driver_module_name(read_back), "weewx.drivers.vantage")
        check("written out and read back: the port",
              str(read_back["Vantage"]["port"]), "/dev/ttyS3")
        check("written out and read back: every option",
              sorted(key for key in read_back["Vantage"] if key != "driver"),
              sorted(setting.name for setting in one.settings))


def an_unknown_key_survives(files: list[Path]) -> None:
    """A value for an option this version of the driver does not list.

    Drivers gain options. One that was set when the driver had it must not be
    dropped by an upgrade that reads a stanza without it, because dropping it
    is silent and the symptom is a station that stops behaving as it did.
    """
    print("\na setting the stanza does not name")
    by_name = {p.stem: p for p in files}
    if "vantage" not in by_name:
        note("no vantage.py to read")
        return
    one = wd.read(by_name["vantage"], "weewx.drivers.vantage")
    built = wd.config_dict_for(one, {"something_new": "7"})
    check("it is passed through", built["Vantage"].get("something_new"), "7")


# -- the parser on its own ------------------------------------------------

def the_awkward_stanzas() -> None:
    """The shapes a stanza takes that are not a flat list of options."""
    print("\nstanzas that are not straightforward")

    section, settings, nested = wd.parse_stanza("""
[Thing]
    # The preamble, which belongs to no option.

    # What the port is,
    # on two lines.
    port = /dev/ttyUSB0

    ############################
    # Rarely needs attention.
    ############################

    timeout = 4

    # A hash inside quotes is not a comment.
    separator = " # "

    [[sensor_map]]
        outTemp = t_1
        [[[deeper]]]
            x = 1
        after = yes
""")
    check("the section", section, "Thing")
    check("the options", [one.name for one in settings],
          ["port", "timeout", "separator"])
    check("help is the block above it, not the preamble",
          list(settings[0].help), ["What the port is,", "on two lines."])
    check("below the rule is advanced", settings[1].advanced, True)
    check("above it is not", settings[0].advanced, False)
    check("a sub-section is kept", nested.get("sensor_map", {}).get("outTemp"),
          "t_1")
    check("and so is one below that",
          nested.get("sensor_map", {}).get("deeper", {}).get("x"), "1")
    check("a sub-section is not a field",
          [one.name for one in settings if one.name == "sensor_map"], [])
    check("a hash inside quotes", settings[2].value, " # ")
    # ConfigObj's own rule, and the reason `config_dict_for` writes the
    # sub-sections last: once a sub-section has opened, everything after it
    # belongs to the *deepest* one open, not to the one it was written under.
    # Measured rather than assumed -- asked of configobj, which puts `after`
    # in `deeper`. A parser that took it back to the top level would write a
    # stanza meaning something other than the one it read.
    check("what follows a sub-section belongs to the deepest one open",
          nested.get("sensor_map", {}).get("deeper", {}).get("after"), "yes")
    check("and is not a field either",
          [one.name for one in settings if one.name == "after"], [])

    # A stanza that stops in the middle. Half a form is worth more than none:
    # whatever was read still carries the driver's own defaults.
    _, half, _ = wd.parse_stanza("[Thing]\n    port = /dev/x\n    [[oops\n")
    check("a stanza that will not finish still yields what it had",
          [one.name for one in half], ["port"])


def a_collector_with_no_weewx_conf(files: list[Path]) -> None:
    """The whole point, run: a driver built from the configuration alone.

    Through the command line rather than by calling the pieces, because the
    pieces were never the doubt. What was missing was a path from a settings
    file to a running driver that did not go through a `weewx.conf`, and a
    test that assembles the config_dict itself would not have crossed it.

    The simulator, because it is the one driver that needs no hardware and no
    library -- so what this measures is the wiring and nothing else.
    """
    print("\na collector configured here, with no weewx.conf anywhere")
    by_name = {p.stem: p for p in files}
    if "simulator" not in by_name:
        note("no simulator.py to run")
        return

    import subprocess

    with tempfile.TemporaryDirectory() as where:
        home = Path(where)
        # The driver copied out of WeeWX and into the directory an
        # installation without WeeWX keeps them in. That is the case this
        # exists for, and copying it is what somebody does with one file.
        drivers = home / wd.DIRNAME
        drivers.mkdir()
        (drivers / "simulator.py").write_bytes(
            by_name["simulator"].read_bytes())

        config = home / "evo.toml"
        config.write_text(
            f'archive_db = "{(home / "weewx.sdb").as_posix()}"\n'
            '\n[collectors.shed]\n'
            'kind = "weewx-driver"\n'
            'driver = "weewx.drivers.simulator"\n'
            f'driver_file = "{(drivers / "simulator.py").as_posix()}"\n'
            'source = "shed-console"\n'
            '\n[collectors.shed.settings]\n'
            'loop_interval = "0.05"\n'
            'mode = "simulator"\n', encoding="utf-8")

        found = wd.available(drivers)
        check("the driver is found where it was put",
              "weewx.drivers.simulator" in [one.module for one in found], True)

        # A subprocess, and one in which `weewx` cannot be imported at all.
        # Two things at once, and both are the point:
        #
        #   it is the real entry point, so the settings are resolved the way
        #   they are in service rather than by calling the function under it,
        #
        #   and the block makes the claim mean something. Clearing PYTHONPATH
        #   is not enough: a machine that has WeeWX installed -- which this
        #   one does, because the rest of this file compares against it --
        #   imports it anyway, and the test would pass having measured
        #   nothing. `standin_test.py` learnt that one first.
        #
        # `find_spec` and not `find_module`: the older pair was removed in
        # 3.12, and a finder that has only them is skipped in silence.
        runner = home / "run.py"
        runner.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "class Blocked:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('weewx', 'weeutil'):\n"
            "            raise ImportError(name + ' is blocked for this test')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocked())\n"
            "try:\n"
            "    import weewx\n"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('weewx imported anyway; the block failed')\n"
            "from weewx_evo.cli import main\n"
            "sys.argv = ['weewx-evo'] + sys.argv[1:]\n"
            "raise SystemExit(main())\n", encoding="utf-8")

        env = {key: value for key, value in __import__("os").environ.items()
               if key != "PYTHONPATH"}
        run = subprocess.run(
            [sys.executable, str(runner), "weewx-driver", "check",
             "--config", str(config), "--collector", "shed", "--count", "2"],
            capture_output=True, text=True, timeout=120, check=False,
            env=env, cwd=str(home))
        out = run.stdout
        check("it builds and delivers", run.returncode, 0)
        if run.returncode:
            print(f"       {run.stderr.strip()[:400]}")
            return
        check("it ran the driver that was chosen",
              "weewx.drivers.simulator" in out, True)
        check("under the collector's own name, which is what a station "
              "is matched on", "delivers as:      shed" in out, True)
        check("and it produced readings", "packets:          2" in out, True)
        # The setting from the configuration reached the driver. Without it
        # the simulator sleeps its own default between packets, so this is
        # also why the check above returns in a moment rather than a minute.
        check("what the settings file said reached the driver",
              "outTemp" in out, True)


def the_serial_ports() -> None:
    """Whatever this machine has, without falling over where it has none."""
    print("\nthe serial devices on this machine")
    found = wd.serial_ports()
    check("a list of pairs",
          all(isinstance(one, tuple) and len(one) == 2 for one in found), True)
    print(f"  --   {len(found)} device(s) here")


def main() -> int:
    files = driver_files()
    if not files:
        print("WeeWX is not installed; this test reads the drivers it ships.")
        return 0
    print(f"reading {len(files)} driver(s) from {files[0].parent}")

    every_driver_has_a_form(files)
    against_configobj(files)
    the_conditional_ones(files)
    the_choices(files)
    the_config_dict(files)
    an_unknown_key_survives(files)
    a_collector_with_no_weewx_conf(files)
    the_awkward_stanzas()
    the_serial_ports()

    print()
    if skipped:
        print(f"{len(skipped)} check(s) skipped:")
        for one in skipped:
            print(f"  {one}")
    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("every driver's own form reads, and builds what it would have got")
    return 0


if __name__ == "__main__":
    sys.exit(main())
