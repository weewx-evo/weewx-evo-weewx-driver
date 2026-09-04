#
#    Copyright (c) 2026 Manuel Hilgert
#
#    See the file LICENSE for your full rights.
#
"""The settings page for a collector that runs a WeeWX driver.

Read out of the driver rather than listed here: every WeeWX driver carries a
`confeditor` whose `default_stanza` is exactly the form -- each option, a
working default, and the author's own comment about what it is. A second
list here would be wrong the moment a driver gains an option.

These moved out of the core with the shim. What stayed there is the
machinery every kind of collector shares -- the name, the endpoint, the
section in the file -- and `Kind.options` is where a kind says the rest.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: Where a hosted driver's own settings sit, under the collector's
#: section. The core's, because it is the core that reads them back
#: out of the file: two spellings of this prefix would be settings
#: written where nothing looks for them.
from weewx_evo.collectors import PREFIX  # noqa: E402


def options(settings: dict) -> list:
    """A WeeWX driver: which one, how it is reached, what it keeps."""
    from weewx_evo.options import Group, Option

    stored = dict(settings or {})
    chosen = str(stored.get("driver") or "").strip()
    return [
        Group("The hardware", "What it reads, and how it is reached.", (
            Option("driver", "The hardware this collector reads",
                   kind="choice", default="",
                   choices=(("", "-- from a weewx.conf --"),),
                   choices_from=lambda: _hardware_choices(chosen),
                   help="Every WeeWX driver on this machine. Choosing one "
                        "and saving brings up its own settings below, read "
                        "out of the driver itself."),
            Option("conf", "The weewx.conf it is configured by",
                   kind="path", default="",
                   when=("driver", ("",)),
                   help="For an installation that already has one. The "
                        "driver reads its settings from the section named "
                        "after it, and nothing here touches that file."),
            Option("driver_file", "Load the driver from this file",
                   kind="path", default="",
                   help="A driver is one file. With this set, WeeWX itself "
                        "does not have to be installed: what the file "
                        "imports is stood in for."),
        )),
        *_hardware_settings(chosen),
        Group("The console", "What it is, and what it keeps.", (
            Option("source", "Record its readings under this name",
                   kind="text", default="",
                   help="Empty means the driver's own hardware name. This is "
                        "the identity a station is matched on, so it is what "
                        "to announce on the Stations page."),
            Option("catchup", "Fetch this much of the console's own log at "
                              "startup",
                   kind="duration", default=0,
                   help="For hardware that keeps its own records. This is "
                        "what turns an outage into a filled gap rather than "
                        "a lost one. Zero for hardware that logs nothing."),
            Option("batch", "Seconds of packets per delivery",
                   kind="duration", default=5,
                   help="One request per loop packet is a round trip every "
                        "two seconds for readings that are aggregated at the "
                        "end of the interval anyway."),
        )),
    ]


def _hardware_choices(chosen: str = "") -> list[tuple[str, str]]:
    """Every WeeWX driver on this machine, for the hardware list.

    `chosen` is what is configured now, and it is always in the list even
    when nothing on disk answers to it any more. A driver file that was
    removed would otherwise take its own setting with it: the page would
    refuse the stored value as one that is not offered, fall back to the
    default, and the collector would quietly become one reading a weewx.conf
    that may not exist. That is the `archive_names` failure -- the page
    telling the operator that the truth is a mistake.
    """
    from . import weewxdrivers

    out: list[tuple[str, str]] = []
    for one in weewxdrivers.available(_driver_directory()):
        if one.problem:
            label = f"{one.name} -- {one.problem}"
        elif one.needs:
            label = f"{one.name} ({one.module}), needs {one.needs}"
        else:
            label = f"{one.name} ({one.module})"
        out.append((one.module, label))
    if chosen and chosen not in [value for value, _ in out]:
        out.append((chosen, f"{chosen} -- not found on this machine"))
    return out


def _driver_directory() -> Any:
    """Where WeeWX driver files are kept, for the file the form describes.

    Through `options._current_config` and not through `settings.running()`.
    The settings page can be its own process (`weewx-evo admin`) and builds
    forms for a named file, so a list read off the running settings would be
    a list of what some other file has -- which is the mistake `building_for`
    exists to prevent, and it has been made twice before.
    """
    from pathlib import Path

    from weewx_evo import archives as archive_defs
    from weewx_evo import config as config_file
    from weewx_evo import options as option_defs

    from . import weewxdrivers

    current = option_defs._current_config() or {}
    for_file = option_defs._config_path()
    base = Path(str(for_file)).parent if for_file else Path(".")

    class Saved:
        def get(self, name: str, default: Any = None) -> Any:
            value = config_file.get(current, name)
            return default if value in (None, "") else value

    register = archive_defs.Register.load(base / archive_defs.FILENAME, Saved())
    beside = register.get(None).file
    # A relative path counts against the file it is written in, not against
    # whatever directory this process was started in. `Settings._anchor` is
    # the same rule and says why: `archive_db = "weewx.sdb"` otherwise names
    # two different files, and the page acts on the one the service is not
    # using -- while reporting success. Here the symptom would be a hardware
    # list that is empty on an installation whose drivers are right there.
    found = Path(str(beside))
    if not found.is_absolute():
        if for_file:
            found = base / found
    return weewxdrivers.directory(beside=found)


def _hardware_settings(module: str) -> list:
    """One group holding the chosen driver's own options, or none.

    The whole point of the module behind it: these are not written down here.
    They are what the driver's own configuration editor describes, and a
    driver that gains an option gains a field with no change on this side.
    """
    if not module:
        return []
    from weewx_evo.options import Group, Option

    from . import weewxdrivers

    found = weewxdrivers.by_module(module, _driver_directory())
    if found is None:
        return [Group(
            "The hardware's own settings",
            f"{module} is not on this machine, so its settings cannot be "
            f"read. Install it, or point at its file above.", ())]
    if found.problem:
        return [Group("The hardware's own settings", found.problem, ())]

    made = []
    for one in found.settings:
        # The driver's author wrote this comment above this option. It is the
        # help text, and it is worth more than anything that could be written
        # here: it is current, and it is about this version of this driver.
        help_text = " ".join(one.help)
        made.append(Option(
            f"{PREFIX}.{one.name}",
            # The option's own name as the label. A driver says `iss_id` and
            # its documentation says `iss_id`; renaming it to "ISS identifier"
            # would mean the field and every answer written about it disagree.
            one.name,
            kind="choice" if one.choices else "text",
            default=one.value,
            help=help_text,
            choices=one.choices,
            advanced=one.advanced,
            when=((f"{PREFIX}.{one.when[0]}", one.when[1])
                  if one.when else None),
            suggestions_from=(weewxdrivers.serial_ports
                              if one.name == "port" else None),
        ))
    note = found.about or (
        f"Read out of {found.module}, which is where they are defined.")
    if found.needs:
        note += (f" It reaches its hardware through {found.needs}, which has "
                 f"to be installed where this collector runs.")
    # No group prefix: the names carry it, because a form field is addressed
    # by the option's name alone. See PREFIX.
    return [Group(f"{found.name}", note, tuple(made))]
