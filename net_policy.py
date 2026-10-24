#!/usr/bin/env python3
"""
net_policy.py — MODULE (not launched on its own)

How serious is each problem the net checks find? Pure decision logic: it receives the structured
results of net_check.check (one per boardview net) and net_check.audit (whole schematic) and
returns a list of findings, each GRAVE or NOTICE, with the automatic fix when there is one.
Rules stated by the technician on 2026-10-18, amended on 2026-10-24:

  GRAVE (the program does not go on until it is solved):
    - a net that is not connected must not exist: a dangling net (attached to a single pin).
      Exception (2026-10-21): a net may be NAMED DIRECTLY ON A PIN, with a label on the pin and no
      wire, to be joined to the rest later: such a pin counts as connected and is not dangling;
    - a connection with a wrong start or end point must not exist: a wire that joins pins of
      different boardview nets (SHORT), a wire on the wrong pin of a component (WRONG PIN);
    - a net with the wrong name (assumption: not stated explicitly, treated like the above);
    - a net the technician has CLOSED (answer 0 in its menu, or no member left to place) that has
      no name: the technician must name it.
  NOTICE (reported, the program goes on):
    - a net that has no name yet while it is not closed;
    - pins still waiting for their wire, or wired in separate pieces: nobody is forced to wire every
      pin of a component, so this is never grave, not even when leaving a net or ending.

Fixes the program can apply by itself (the others need the technician):
  remove_net     delete the wires and labels of the offending KiCad net (SHORT, DANGLING, WRONG PIN);
  remove_labels  delete only the wrong label, the wires stay (WRONG NAME).
"""

from dataclasses import dataclass, field

GRAVE = "GRAVE"
NOTICE = "NOTICE"
REMOVE_NET = "remove_net"
REMOVE_LABELS = "remove_labels"


@dataclass
class Finding:
    severity: str                       # GRAVE or NOTICE
    kind: str                           # SHORT, DANGLING, WRONG_PIN, WRONG_NAME, NOT_CONNECTED, UNNAMED...
    message: str                        # one line for the technician
    kicad_net: str | None = None        # the KiCad net the fix acts on
    fix: str | None = None              # REMOVE_NET, REMOVE_LABELS or None
    pins: list = field(default_factory=list)


@dataclass
class Evaluation:
    """What one round of checks found."""
    findings: list = field(default_factory=list)
    confirmed_nets: list = field(default_factory=list)   # boardview nets wired and named correctly


def evaluate(results: dict, audit, closed_nets=(), single_pin_nets=()) -> list:
    """The findings, most serious first.

    `results`: {boardview net: net_check.CheckResult} for the nets the technician has worked on.
    `audit`: net_check.AuditResult for the whole schematic.
    `closed_nets`: the boardview nets the technician has closed (only for those is a missing name GRAVE).
    `single_pin_nets`: boardview nets with fewer than two pins placed, which cannot be wired yet."""
    findings: list[Finding] = []

    for kicad_net, groups in audit.shorts:
        parts = " | ".join(f"{net}: {', '.join(pins)}" for net, pins in groups.items())
        findings.append(Finding(GRAVE, "SHORT", f"SHORT: KiCad net '{kicad_net}' joins pins of different "
                                f"boardview nets -> {parts}", kicad_net, REMOVE_NET,
                                [p for pins in groups.values() for p in pins]))
    for kicad_net, pins in audit.dangling:
        findings.append(Finding(GRAVE, "DANGLING", f"DANGLING NET: KiCad net '{kicad_net}' is attached only to "
                                f"{', '.join(pins)}: a net must join at least two pins.",
                                kicad_net, REMOVE_NET, pins))
    for kicad_net, boardview_net, pins in audit.wrong_names:
        findings.append(Finding(GRAVE, "WRONG_NAME", f"WRONG NET NAME: {', '.join(pins)} belong to boardview net "
                                f"'{boardview_net}' but the KiCad net is named '{kicad_net}'.",
                                kicad_net, REMOVE_LABELS, pins))

    named_here = set()
    for boardview_net, r in results.items():
        for refdes, expected, found, kicad_net in r.wrong_pins:
            findings.append(Finding(GRAVE, "WRONG_PIN", f"WRONG PIN: on {refdes} pin {expected} was expected to be "
                                    f"wired (net '{boardview_net}'), but pin {found} is wired instead "
                                    f"(KiCad net '{kicad_net}').", kicad_net, REMOVE_NET,
                                    [f"{refdes}.{found}"]))
        if r.wrong_pins:
            continue
        if (r.unconnected_pins or r.mixed_nets) and boardview_net not in single_pin_nets:
            if r.unconnected_pins:
                listing = ", ".join(f"{x}.{y}" for x, y in r.unconnected_pins)
                text = (f"NOT CONNECTED: net '{boardview_net}': no wire touches {listing}. Wire "
                        f"{'it' if len(r.unconnected_pins) == 1 else 'them'}, or name the net right on the "
                        f"pin with a label '{boardview_net}'.")
            else:
                text = (f"NOT CONNECTED: net '{boardview_net}' is split in separate pieces: "
                        + ", ".join(f"{x}.{y} -> '{n}'" for (x, y), n in r.mixed_nets.items()))
            findings.append(Finding(NOTICE, "NOT_CONNECTED", text + " (waiting for the wires)",
                                    pins=[f"{x}.{y}" for x, y in r.unconnected_pins or r.mixed_nets]))
        if r.unnamed:
            named_here.add(r.kicad_net_name)
            if boardview_net in closed_nets:
                findings.append(Finding(GRAVE, "UNNAMED", f"UNNAMED NET: net '{boardview_net}' was closed but "
                                        f"has no name: label it '{boardview_net}' in KiCad.", r.kicad_net_name))
            else:
                findings.append(Finding(NOTICE, "UNNAMED", f"UNNAMED NET: net '{boardview_net}' is wired but not "
                                        f"named yet: label it '{boardview_net}'.", r.kicad_net_name))

    for kicad_net, boardview_net, pins in audit.unnamed:
        if kicad_net not in named_here:
            findings.append(Finding(NOTICE, "UNNAMED", f"UNNAMED NET: {', '.join(pins)} are wired together "
                                    f"(KiCad calls it '{kicad_net}') and belong to boardview net "
                                    f"'{boardview_net}': label it '{boardview_net}'.", kicad_net, None, pins))

    findings.sort(key=lambda f: 0 if f.severity == GRAVE else 1)
    return findings
