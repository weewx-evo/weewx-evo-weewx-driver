"""Which WeeWX drivers are on this machine, and how each one is configured.

`weewxshim.py` runs a WeeWX driver, and `weewxnames.py` means WeeWX itself
does not have to be installed for that. Between the two there was a hole: the
driver still had to be handed a `weewx.conf`, and somebody without WeeWX has
no such file. So the one thing that was supposed to be free -- plug in a USB
console, keep the rest of this program -- began by writing a configuration
file for software that is not there.

This module closes it. Every WeeWX driver carries a `confeditor_loader` whose
`default_stanza` is the block `weectl station reconfigure` writes: every option
that driver takes, a working default for each, and a comment above each saying
what it is, all written by whoever wrote the driver. That is the form, and
there is no reason to keep a second copy of it here that would be wrong the
moment a driver gained an option.

## Read, never imported

`userdrivers.py` already says why for its own kind, and here it matters twice
over.

    **Nothing runs.** Listing what is available must not execute thirteen
    modules written by other people.

    **A driver whose library is missing still has a form.** `import usb` at the
    top of `fousb.py` fails on a machine without pyusb -- which is the ordinary
    state of an installation that has neither WeeWX nor its dependencies.
    Imported, that driver would vanish from the list at exactly the moment
    somebody wants to know what to install. Read, it says both: here is how it
    is configured, and pyusb is what it needs.

So `default_stanza` is taken as the string literal it is, and the branches in
`prompt_for_settings` are read as the code they are. Neither needs the module
to exist as an object.

Measured against the thirteen WeeWX ships: ten write their stanza out as it
stands, and three build it with `%`. Two of those three put their default
serial port in that way, so the interpolation is read too -- from the literal
in the same file, never by running anything. A driver that builds its stanza
out of something unreadable gets no form and says so, which is better than an
empty one that looks like a driver with no settings.

## Why not configobj

The stanza is ConfigObj's format, and upstream parses it with the library --
which is right there, in a WeeWX installation. Here it would be a dependency,
against the one rule this project has about them, and a form whose help text
depended on whether it was installed would be two different forms on two
machines.

Measured across the thirteen: one section, flat keys, no list values, at most
thirteen options. `tools/weewxdrivers_test.py` compares this parser against
configobj wherever configobj is there, the same way `unitcheck.py` compares
against WeeWX.

Sub-sections are the exception worth naming: weewx-sdr carries a `sensor_map`
of two hundred lines. It is parsed and passed through to the driver unchanged,
and it is *not* offered as a field. A text box holding two hundred lines is not
a form, and dropping it would be worse -- it is the whole configuration of that
driver.
"""

from __future__ import annotations

import ast
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Where a WeeWX driver file goes when there is no WeeWX to take it from.
#: Beside `drivers/`, which holds ours, rather than in it: the two meet
#: different contracts -- `load(registry)` there, `loader()` and `DRIVER_NAME`
#: here -- and one directory answering to both is the kind of economy that
#: costs an afternoon the first time a file meets neither.
DIRNAME = "weewx-drivers"

#: The environment variable that overrides where they live.
ENV_VAR = "WEEWX_EVO_WEEWX_DRIVER_DIR"

#: Modules under `user` that are never hardware. An installation that has had
#: weewx-ultimate-push installed carries these, and they are a listener and a
#: driver of drivers rather than a console.
NOT_HARDWARE = frozenset({"user.listener", "user.ultimatepush"})


# -- how a driver reaches its hardware ------------------------------------
#
# Worked out from what the module needs rather than from what its comments say.
# A driver that imports pyusb talks to a USB device and has no port to set; one
# whose port defaults to a device node is on a cable. This is the sentence a
# form otherwise cannot answer: a WMR100 offers nothing but a name, and
# somebody looking at that has no way to tell whether they missed a step.

BY_USB = "usb"
BY_CABLE = "cable"
BY_EITHER = "either"
BY_NOTHING = "nothing"

CONNECTS = {
    BY_USB: ("It is found over USB. Nothing below has to be changed: plug it "
             "in, and the driver looks for it."),
    BY_CABLE: ("It is read over a cable, which may be a USB-to-serial adapter. "
               "The port is the one setting that has to be right."),
    BY_EITHER: ("It is read over a cable or over the network, and which of the "
                "two decides which of the settings below apply."),
    BY_NOTHING: "",
}

#: What a driver imports to reach hardware, and what to install for it. The
#: import name and the package name differ for every one of them, which is the
#: whole reason this exists rather than a sentence saying to install it.
#:
#: `cli.py` reports the same fact after a build that failed. It reads it from
#: here: the answer to "what would this need" and "what did this need" is one
#: table, and two copies of it disagree the day a driver gains a dependency.
NEEDS = {
    "usb": "pyusb",         # fousb, te923, ws28xx, wmr300
    "serial": "pyserial",   # vantage, ws23xx, ultimeter, ws1, cc3000
    "hid": "hidapi",
    "ftdi": "pylibftdi",
}

_IMPORTS = re.compile(r"^\s*(?:import|from)\s+(usb|serial|hid|ftdi)\b", re.MULTILINE)


# -- options that take one of a fixed set of values -----------------------
#
# This is a copy of something the drivers say, which is the thing to avoid, and
# it is here because they do not say it in any other way. Two of the thirteen
# declare their choices to `_prompt` and eleven state them in a comment, in
# prose, mixed in with comments that read the same and are not choices at all:
# `port` is "such as /dev/ttyS0, /dev/ttyUSB0, or /dev/cuaU0" and `model` is
# "e.g., 'LaCrosse WS2317' or 'TFA Primus'". Both are examples of a free value.
# Reading choices out of that sentence would offer three serial ports as if
# they were the only ones.
#
# So the four real ones are named here, and `tools/weewxdrivers_test.py` checks
# each against the driver it belongs to: that the option still exists, and that
# the driver's own default is one of the values. A driver that gains a choice
# makes that test say so. A field built from this always keeps a way to type
# something else, so that a copy which has fallen behind costs a convenience
# rather than the setting.
#
# A value on its own is its own label. A pair is (value, label), for the ones
# whose values mean nothing on their own: '2' is not an answer to "which
# Vantage is this", and 'Vantage Pro2' is.
CHOICES: dict[str, dict[str, list]] = {
    "weewx.drivers.simulator": {"mode": ["simulator", "generator"]},
    "weewx.drivers.vantage": {
        # vantage.py: connection_type is compared against these two, and
        # anything else raises UnsupportedFeature.
        "type": ["serial", "ethernet"],
        # "loop_request: Requested packet type. 1=LOOP; 2=LOOP2; 3=both."
        "loop_request": [("1", "LOOP1"), ("2", "LOOP2"), ("3", "both")],
        # `if self.model_type not in (1, 2): raise UnsupportedFeature`.
        "model_type": [("1", "Vantage Pro"), ("2", "Vantage Pro2")],
    },
    # ws1.py compares con_mode against these three and raises ValueError on
    # anything else.
    "weewx.drivers.ws1": {"mode": ["serial", "tcp", "udp"]},
    # wmr9x8.py: `if connection_type == "serial"`, and the else raises
    # UnsupportedFeature. One value, so there is nothing to choose and the
    # field says so rather than inviting somebody to type what cannot work.
    "weewx.drivers.wmr9x8": {"type": ["serial"]},
    "weewx.drivers.ws28xx": {"transceiver_frequency": ["US", "EU"]},
}

#: Where a serial device shows up. The first is the one to offer: a name under
#: by-id is made from the adapter's own manufacturer and serial number, so it
#: is the same after a reboot and after something else is plugged in, which
#: /dev/ttyUSB0 is not.
SERIAL_DIRS = ("/dev/serial/by-id", "/dev/serial/by-path")
SERIAL_NAMES = ("ttyUSB", "ttyACM", "ttyS", "cuaU", "cu.usbserial")


@dataclass(frozen=True, slots=True)
class Setting:
    """One option out of a driver's own default stanza."""

    name: str
    #: The default, as the driver wrote it. A string, always: it goes back into
    #: a stanza, and the driver converts it the way it always has.
    value: str
    #: The comment above it, a line each, in the order it was written. Kept as
    #: lines rather than joined, because these are laid out as lines: a Vantage
    #: says what the connection type is on one line and what each of the two
    #: means on the next two, and run together they read as one sentence that
    #: does not parse.
    help: tuple[str, ...] = ()
    #: Below the row of hashes the author drew, which is that author saying
    #: these rarely need attention. Worth more than any guess this could make
    #: about which options matter.
    advanced: bool = False
    #: `(option, values)` when this one only applies for certain values of
    #: another, from the driver's own `prompt_for_settings`. None otherwise.
    when: tuple[str, tuple[str, ...]] | None = None
    #: What it takes, when the driver takes exactly a few. Empty means free
    #: text, and a single entry means there is nothing to choose.
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Driver:
    """A WeeWX driver on this machine, and the form it describes."""

    #: The import path, e.g. `weewx.drivers.vantage`.
    module: str
    #: The section it wants in a configuration, e.g. `Vantage`. This is
    #: `DRIVER_NAME`, and the section has to carry that name and not the file
    #: name -- twelve drivers failed on the same KeyError once for getting that
    #: the other way round.
    name: str
    #: The file it was read from, for `--driver-file` and for the AST.
    path: Path = field(default_factory=Path)
    #: Its options, in the order the driver wrote them.
    settings: tuple[Setting, ...] = ()
    #: Sub-sections, parsed and passed to the driver, not offered as fields.
    nested: dict[str, Any] = field(default_factory=dict)
    #: One of BY_USB, BY_CABLE, BY_EITHER, BY_NOTHING.
    connects: str = BY_NOTHING
    #: The package it needs to reach its hardware, or "" when it needs none.
    needs: str = ""
    #: Why it cannot be used, or "" when it can. A driver that will not read is
    #: reported rather than left out: "the WMR300 is not in the list" and "the
    #: WMR300 needs pyusb" send somebody to very different places.
    problem: str = ""

    @property
    def about(self) -> str:
        """The sentence that goes with how it reaches its hardware."""
        return CONNECTS.get(self.connects, "")

    def defaults(self) -> dict[str, str]:
        """Every option's default, which is the shape a stanza is written in."""
        return {one.name: one.value for one in self.settings}


# -- the stanza ------------------------------------------------------------

_SECTION = re.compile(r"^(\[+)\s*([^\]]+?)\s*(\]+)\s*$")
_ENTRY = re.compile(r"^([^=\[]+?)\s*=\s*(.*)$")


def parse_stanza(text: str) -> tuple[str, list[Setting], dict[str, Any]]:
    """A driver's default stanza: its section, its options, its sub-sections.

    Comments are the point. Everything above an option up to the last blank
    line is that option's help, so that a section's own preamble and the
    banners some stanzas carry between groups of options stay with neither.

    Nothing raises. A stanza that will not parse yields what was read before
    the trouble, because half a form is worth more than none: the driver's own
    defaults are in it either way.
    """
    section = ""
    settings: list[Setting] = []
    nested: dict[str, Any] = {}
    comments: list[str] = []
    advanced = False
    # Which sub-section the lines belong to, deepest last. Empty means the top
    # level, which is the only level a field comes from.
    inside: list[str] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            # A blank line ends a comment block. This is what keeps a banner
            # from being read as the help for whatever follows it.
            comments = []
            continue
        if line.startswith("#"):
            if _is_a_rule(line):
                advanced = True
                comments = []
            else:
                comments.append(line.lstrip("#").strip())
            continue

        found = _SECTION.match(line)
        if found is not None:
            depth = len(found.group(1))
            name = found.group(2)
            comments = []
            if depth == 1:
                section = name
                inside = []
            else:
                inside = inside[:depth - 2] + [name]
                _place(nested, inside, {})
            continue

        found = _ENTRY.match(line)
        if found is None:
            continue
        key = found.group(1).strip()
        value = _bare(found.group(2))
        if inside:
            _place(nested, inside + [key], value)
        else:
            settings.append(Setting(name=key, value=value,
                                    help=tuple(comments), advanced=advanced))
        comments = []

    return section, settings, nested


def _place(tree: dict[str, Any], path: list[str], value: Any) -> None:
    """Put a value at a path in the sub-section tree, making what is missing.

    An existing sub-section is kept rather than replaced, so that re-entering
    one -- which ConfigObj allows and some drivers do -- adds to it.
    """
    node = tree
    for step in path[:-1]:
        found = node.get(step)
        if not isinstance(found, dict):
            found = {}
            node[step] = found
        node = found
    last = path[-1]
    if isinstance(value, dict) and isinstance(node.get(last), dict):
        return
    node[last] = value


def _bare(value: str) -> str:
    """A value with its trailing comment and its quotes taken off.

    A hash is only a comment outside quotes: `separator = " # "` is a hash
    somebody meant.
    """
    out = []
    quote = ""
    for char in value:
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "#":
            break
        else:
            out.append(char)
    text = "".join(out).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _is_a_rule(line: str) -> bool:
    """Whether a comment line is a row of hashes ruling off what follows.

    A divider, not a sentence. The stanza's order is somebody's idea of which
    options matter: a Vantage names the connection type and the port first,
    then rules off eleven that rarely need attention.
    """
    text = line.strip()
    return len(text) > 3 and set(text) == {"#"}


# -- reading a driver file, without running it -----------------------------

def read(path: str | Path, module: str = "") -> Driver:
    """What a driver file says about itself, without importing it.

    `module` is the import path to record; empty means the file's own name,
    which is what `--driver-file` wants.
    """
    found = Path(path)
    module = module or f"weewx.drivers.{found.stem}"
    try:
        source = found.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Driver(module=module, name=found.stem, path=found,
                      problem=str(exc))
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return Driver(module=module, name=found.stem, path=found,
                      problem=f"cannot be read: {exc}")

    name = _driver_name(tree) or found.stem
    needs = _needs(source)
    stanza = _stanza_source(tree)
    if stanza is None:
        # Every driver WeeWX ships writes its stanza out as a literal. One that
        # builds it has nothing to read, and saying so is better than an empty
        # form, which looks like a driver with no settings.
        return Driver(
            module=module, name=name, path=found, needs=needs,
            connects=_connects(source, set()),
            problem="it does not write its settings out, so there is nothing "
                    "to read; its options go in the file by hand",
        )

    section, settings, nested = parse_stanza(stanza)
    conditions = conditions_of(tree, module)
    choices = CHOICES.get(module, {})
    # The driver is not a setting: it is the module we already have, and a box
    # to retype it in is a box to mistype it in.
    kept = [_with_form(one, conditions.get(one.name), choices.get(one.name))
            for one in settings if one.name != "driver"]
    return Driver(
        module=module, name=section or name, path=found,
        settings=tuple(kept), nested=nested, needs=needs,
        connects=_connects(source, {one.name for one in kept}),
    )


def _with_form(one: Setting, when: tuple[str, tuple[str, ...]] | None,
               choices: list | None) -> Setting:
    """One setting with what the module said about it filled in."""
    if when is None and not choices:
        return one
    offered = tuple(
        (str(each[0]), str(each[1])) if isinstance(each, tuple)
        else (str(each), str(each))
        for each in (choices or ())
    )
    return Setting(name=one.name, value=one.value, help=one.help,
                   advanced=one.advanced, when=when, choices=offered)


def _driver_name(tree: ast.Module) -> str:
    """`DRIVER_NAME` at the top of the module, which names its section."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name) and target.id == "DRIVER_NAME"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                return node.value.value
    return ""


def is_a_driver(path: str | Path) -> bool:
    """Whether a file is a WeeWX driver: `loader` and `DRIVER_NAME`.

    That is all WeeWX itself requires of one. Read rather than imported, for
    the reason at the top of this module.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8",
                                              errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return False
    has_loader = any(isinstance(node, ast.FunctionDef) and node.name == "loader"
                     for node in tree.body)
    return has_loader and bool(_driver_name(tree))


def _stanza_source(tree: ast.Module) -> str | None:
    """The text of `default_stanza`, or None when it cannot be read.

    Ten of the thirteen return the string as written. Three build it, and
    reading them is worth the twenty lines: `cc3000` and `ultimeter` put their
    default serial port in with `%`, and that port is the one setting those two
    have to get right -- an empty field there would be the form failing at the
    only question it was opened for.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "default_stanza":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and inner.value is not None:
                found = _text_of(inner.value, tree)
                if found is not None:
                    return found
    return None


#: A `%s` or `%(name)s` in a stanza template, for when its value cannot be
#: read. Dropped rather than left standing: `port = %s` would be a value
#: somebody submits.
_PLACEHOLDER = re.compile(r"%(?:\([^)]*\))?[-#0 +]*[\d.*]*[hlL]?[a-zA-Z%]")


def _text_of(node: ast.expr, tree: ast.Module) -> str | None:
    """A string expression's value, as far as it can be had without running it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)):
        template = node.left.value
        parts = (node.right.elts if isinstance(node.right, ast.Tuple)
                 else [node.right])
        filled = tuple(_constant(one, tree) for one in parts)
        try:
            return template % filled
        except (TypeError, ValueError, KeyError):
            # te923 interpolates a comprehension building commented-out lines.
            # Nothing here can run that, and nothing is lost by leaving it out:
            # what it produces is comments.
            return _PLACEHOLDER.sub("", template)
    return None


def _constant(node: ast.expr, tree: ast.Module) -> str:
    """A literal, or a name in this same file that holds one. "" otherwise.

    `CC3000.DEFAULT_PORT` is a class attribute assigned a string three hundred
    lines up, so it can be looked up rather than run.
    """
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.Name):
        return _assigned(tree.body, node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        for one in tree.body:
            if isinstance(one, ast.ClassDef) and one.name == node.value.id:
                return _assigned(one.body, node.attr)
    return ""


def _assigned(body: list, name: str) -> str:
    """The literal assigned to a name in a block of statements, or ""."""
    for node in body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name) and target.id == name
                    and isinstance(node.value, ast.Constant)):
                return str(node.value.value)
    return ""


def _needs(source: str) -> str:
    """The package this driver needs to reach its hardware, or ""."""
    found = _IMPORTS.search(source)
    return NEEDS.get(found.group(1), "") if found is not None else ""


def _connects(source: str, keys: set[str]) -> str:
    """How a driver reaches its hardware, from what it needs to do it."""
    if "host" in keys or ("mode" in keys and "port" in keys):
        return BY_EITHER
    if re.search(r"^\s*import (usb|hid)\b|usb\.core", source, re.MULTILINE):
        return BY_USB
    if "port" in keys:
        return BY_CABLE
    return BY_NOTHING


# -- which options depend on which -----------------------------------------

def conditions_of(tree: ast.Module,
                  module: str = "") -> dict[str, tuple[str, tuple[str, ...]]]:
    """Which options only apply for certain values of another.

    A Vantage takes a port or a host and never both, and it says so in its own
    configuration editor: it asks for the type, then asks for one or the other
    inside an `if`. That is code rather than prose, so it can be read rather
    than guessed at, and it is the driver's own answer rather than a copy.

    An option counts as conditional only when every place it is asked for is
    under the same option, and those places together do not cover all of that
    option's values. That is what separates a Vantage, which asks for a port or
    a host, from a WS1, which asks for a port either way and only changes what
    it suggests.

    Empty for the eleven of the thirteen that have nothing conditional.
    """
    asked: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_prompt(node.value):
            for target in node.targets:
                name = _plain_name(target)
                if name:
                    asked[name] = _prompted_name(node.value)

    choices = CHOICES.get(module, {})
    everywhere: dict[str, list] = {}
    # The branches are inside prompt_for_settings, so that is where the reading
    # starts. Starting at the class would put every prompt at the top level,
    # which is the same as saying nothing is conditional.
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "prompt_for_settings":
            _under(node.body, None, asked, choices, everywhere)

    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for option, places in everywhere.items():
        if any(place is None for place in places):
            # Asked for outside any condition somewhere, so it always applies.
            continue
        fields = {one for one, _ in places}
        if len(fields) != 1:
            continue
        on = fields.pop()
        values: set[str] = set()
        for _, some in places:
            values |= some
        every = {_value_of(one) for one in choices.get(on, [])}
        if every and values >= every:
            # Asked for under every value the option can take, which is the
            # same as not being conditional at all.
            continue
        found[option] = (on, tuple(sorted(values)))
    return found


def _under(body: list, condition: tuple[str, set[str]] | None,
           asked: dict[str, str], choices: dict[str, list],
           everywhere: dict[str, list]) -> None:
    """Note which condition each prompt in a block of code is under.

    Recursive, once per branch, so that an `elif` is read as the branch it is
    rather than a second time as a statement of its own.
    """
    for node in body:
        if isinstance(node, ast.Assign) and _is_prompt(node.value):
            everywhere.setdefault(_prompted_name(node.value),
                                  []).append(condition)
            continue
        if isinstance(node, ast.If):
            _under(node.body, _tested(node.test, asked) or condition,
                   asked, choices, everywhere)
            _under(node.orelse, _otherwise(node.test, asked, choices) or condition,
                   asked, choices, everywhere)
            continue
        for inner in ("body", "orelse", "finalbody"):
            block = getattr(node, inner, None)
            if isinstance(block, list):
                _under(block, condition, asked, choices, everywhere)


def _tested(test: ast.expr,
            asked: dict[str, str]) -> tuple[str, set[str]] | None:
    """The option and values a condition tests, or None for anything else."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        # `con_mode == 'tcp' or con_mode == 'udp'`, which is how a WS1 writes
        # it. Only useful when both sides test the same option; two different
        # ones have no single answer and are left alone.
        merged: set[str] = set()
        on = ""
        for one in test.values:
            part = _tested(one, asked)
            if part is None or (on and part[0] != on):
                return None
            on = part[0]
            merged |= part[1]
        return (on, merged) if on else None
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    name = _prompted_source(test.left, asked)
    if not name:
        return None
    if isinstance(test.ops[0], ast.Eq):
        value = test.comparators[0]
        if isinstance(value, ast.Constant):
            return name, {str(value.value)}
    if isinstance(test.ops[0], ast.In):
        holder = test.comparators[0]
        if isinstance(holder, (ast.Tuple, ast.List, ast.Set)):
            return name, {str(one.value) for one in holder.elts
                          if isinstance(one, ast.Constant)}
    return None


def _otherwise(test: ast.expr, asked: dict[str, str],
               choices: dict[str, list]) -> tuple[str, set[str]] | None:
    """What an `else` branch is under: the values the `if` did not name.

    This is where a Vantage's `host` gets its condition. The `if` names
    `serial`, so the `else` is every other value the option takes -- which is
    knowable only because `CHOICES` says what they are. Without that the branch
    would be read as unconditional, and the form would offer a port and a host
    at once, which is the one thing the driver says never happens.
    """
    part = _tested(test, asked)
    if part is None:
        return None
    on, named = part
    rest = {_value_of(one) for one in choices.get(on, [])} - named
    return (on, rest) if rest else None


def _is_prompt(value: ast.expr | None) -> bool:
    """Whether an expression is a call to the editor's own `_prompt`."""
    return (isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "_prompt"
            and bool(value.args)
            and isinstance(value.args[0], ast.Constant))


def _prompted_name(value: ast.expr) -> str:
    """The option name a `_prompt` call asks for. Empty for anything else."""
    if not _is_prompt(value):
        return ""
    first = value.args[0]        # type: ignore[union-attr]
    return str(first.value)      # type: ignore[union-attr]


def _plain_name(target: ast.expr) -> str:
    """The variable or key a prompt was assigned to.

    Both forms appear: a Vantage writes `settings['type'] = ...` and a WS1
    writes `con_mode = ...`, and the condition below is written against
    whichever it used. A key is bracketed here so that a variable and a key of
    the same name cannot be taken for one another.
    """
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Subscript) and isinstance(target.slice,
                                                        ast.Constant):
        return f"[{target.slice.value}]"
    return ""


def _prompted_source(node: ast.expr, asked: dict[str, str]) -> str:
    """Which option a tested expression holds, through the name it went into."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        # `con_mode.lower() == 'serial'`, which is how a WS1 writes it.
        return _prompted_source(node.func.value, asked)
    return asked.get(_plain_name(node), "")


def _value_of(choice: Any) -> str:
    """The value out of a CHOICES entry, which may be a value or a pair."""
    return str(choice[0]) if isinstance(choice, tuple) else str(choice)


# -- what is on this machine ----------------------------------------------

def directory(configured: str | os.PathLike | None = None,
              beside: str | os.PathLike | None = None) -> Path:
    """Where WeeWX driver files go. Argument, environment, beside the data.

    The same order as `userdrivers.directory`, and for the same reason: the
    last one puts them beside the readings, which is the directory the service
    can write to and the one people back up.
    """
    if configured:
        return Path(configured).expanduser()
    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    if beside:
        return Path(beside).expanduser().parent / DIRNAME
    return Path.cwd() / DIRNAME


def available(where: str | os.PathLike | None = None) -> list[Driver]:
    """Every WeeWX driver this machine could run, sorted by name.

    Three places, and the third is the one that makes a console possible
    without WeeWX: the drivers WeeWX ships, the extensions an installation
    keeps under `user`, and the files somebody put in `where`.
    """
    found: dict[str, Driver] = {}
    for path, module in _files(where):
        if module in NOT_HARDWARE or not is_a_driver(path):
            continue
        # First wins, and the order in `_files` puts an installation's own
        # drivers ahead of a copy somebody dropped in the data directory. A
        # file shadowing `weewx.drivers.vantage` would otherwise be built by
        # `import_driver` under that name and be a different driver than the
        # form was read from.
        found.setdefault(module, read(path, module))
    return sorted(found.values(), key=lambda one: one.name.lower())


def by_module(module: str,
              where: str | os.PathLike | None = None) -> Driver | None:
    """One driver by its import path, or None when it is not on this machine."""
    for one in available(where):
        if one.module == module:
            return one
    return None


def _files(where: str | os.PathLike | None) -> list[tuple[Path, str]]:
    """Every file worth asking whether it is a driver, with its import path."""
    out: list[tuple[Path, str]] = []
    for package in ("weewx.drivers", "user"):
        for inside in _package_paths(package):
            for path in sorted(Path(inside).glob("*.py")):
                if not path.stem.startswith("_"):
                    out.append((path, f"{package}.{path.stem}"))
    here = Path(where) if where else None
    if here is not None and here.is_dir():
        for path in sorted(here.glob("*.py")):
            if not path.stem.startswith("_"):
                out.append((path, f"weewx.drivers.{path.stem}"))
    return out


def _package_paths(name: str) -> list[str]:
    """Where an installed package's modules are, without importing hardware.

    `weewx.drivers` and `user` are packages, so importing them runs their
    `__init__` and nothing else -- no driver, no pyusb. That is a different
    thing from importing thirteen drivers, and it is the only way to find out
    where an installation put them.
    """
    try:
        import importlib

        package = importlib.import_module(name)
    except Exception as exc:
        # No `user` package outside a WeeWX installation, which is the usual
        # case here, and `weewx.drivers` is the stand-in's, whose path is
        # deliberately empty.
        log.debug("no %s package to look in: %s", name, exc)
        return []
    return [str(one) for one in getattr(package, "__path__", []) or []]


def serial_ports() -> list[tuple[str, str]]:
    """The serial devices on this machine, as (value, label) pairs.

    "Which of these is my station" is a question somebody standing next to it
    can answer from the adapter's name, and cannot answer from a text box. So
    the list is what is actually there, by-id first because that name survives
    a reboot and `/dev/ttyUSB0` does not.

    Empty on a machine with no serial devices, and on one where /dev cannot be
    read, which is every Windows machine.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for where in SERIAL_DIRS:
        try:
            names = sorted(os.listdir(where))
        except OSError:
            continue
        for name in names:
            path = os.path.join(where, name)
            try:
                real = os.path.realpath(path)
            except OSError:
                real = ""
            if real and real in seen:
                continue
            seen.add(real or path)
            found.append((path, f"{name} → {real}" if real else name))
    try:
        rest = sorted(os.listdir("/dev"))
    except OSError:
        rest = []
    for name in rest:
        if not name.startswith(SERIAL_NAMES):
            continue
        path = os.path.join("/dev", name)
        if path in seen:
            continue
        seen.add(path)
        found.append((path, name))
    return found


# -- the configuration a driver is built from ------------------------------

def config_dict_for(one: Driver, values: dict[str, Any] | None = None,
                    interval: int = 300) -> dict[str, Any]:
    """The `config_dict` this driver would have got from a `weewx.conf`.

    The point of the module: a driver is handed exactly what `weectl` would
    have written, built from what somebody chose on a page. It cannot tell the
    difference, because there is none -- these are its own defaults under its
    own section name, and the two keys outside it are the two the engine sets.

    `values` are what was chosen; anything not in it keeps the driver's own
    default. An unknown key is passed through rather than dropped: a stanza
    that gained an option between versions must not lose what somebody typed
    for it.
    """
    chosen = dict(values or {})
    section: dict[str, Any] = {}
    for setting in one.settings:
        section[setting.name] = str(chosen.pop(setting.name, setting.value))
    for key, value in chosen.items():
        if key not in ("driver", "station_type"):
            section[key] = value
    section.update(one.nested)
    section["driver"] = one.module
    return {
        "Station": {"station_type": one.name},
        one.name: section,
        # What `archive_interval_of` falls back on. A driver with a logger
        # answers that itself and this is never read; one without a logger
        # needs a number, and five minutes is WeeWX's own default.
        "StdArchive": {"archive_interval": str(int(interval))},
    }


def stanza_text(one: Driver, values: dict[str, Any] | None = None) -> str:
    """The same thing as a `weewx.conf` section, for somebody to read or keep.

    Not what the driver is built from -- that is `config_dict_for`, and one
    thing produced by two paths is how the two drift apart. This is for the
    operator who wants the block, and for a bug report.
    """
    chosen = dict(values or {})
    lines = [f"[{one.name}]"]
    for setting in one.settings:
        lines.extend(f"    # {note}" for note in setting.help)
        lines.append(f"    {setting.name} = "
                     f"{chosen.get(setting.name, setting.value)}")
    lines.append(f"    driver = {one.module}")
    return "\n".join(lines) + "\n"
