#!/usr/bin/env python3
"""
interactive_session.py — interactive session: boardview -> KiCad schematic through the KiCad 11 API

Entry point of the program. KiCad 11 must already be open, with the schematic editor showing the
project's schematic and the API server enabled (Preferences > Plugins).

WHAT IT DOES
  1. Asks for the boardview (GENCAD .cad), reads its netlist, and asks for the primary component
     and the starting pin. The component is placed on the schematic and its pin circled.
  2. For the net of that pin it shows a menu of the members not yet handled. The technician
     chooses which one to place; it is placed and the pin to connect is circled. Excluded nets
     (GND, or more than 50 pins) are never expanded and a component on such a net is not placed.
  3. The technician draws the wires in KiCad and names each net with a label equal to the
     boardview net name. The program only checks the pins it placed (not the whole net): are they
     connected, on the same KiCad net, is the net named correctly, was another pin of the same
     component connected by mistake. Confirmed pins lose their circle. A pin may also be named
     DIRECTLY: a label with the net name placed on the pin, no wire (a net cannot be drawn if it
     closes on no other component yet): the pin counts as connected and the net is not dangling.
  4. At the restart prompt the technician can type:
       - REFDES.PIN of a free pin of a component already on the schematic, or of ANY component
         of the boardview (it is placed): the pin is circled and the menu of its net opens;
       - REFDES alone: shows the pins of that component with their net, to choose from;
       - C: recheck every net that still has pins to confirm (connect calmly, then press C);
       - Enter: end.
     Under the list of highlighted pins the program lists the FREE PINS of the components on the
     schematic with their net ("already expanded" marks nets whose menu was already done).
  5. RESUME. If the open schematic already contains components of the boardview (saved and
     reopened, or a _L<n> copy), the session state is rebuilt by reading ONLY the schematic
     (Session.rebuild_from_schematic): components = symbols of the boardview_custom library whose
     reference is in the netlist; pins TO CONFIRM = those with our circle centred on the pin; pins
     ALREADY CONFIRMED = connected to a net with the exact name of their boardview net. Then it
     rechecks and goes straight to the restart prompt. To start over, open an empty schematic.
  6. ONE COPY PER NET. When moving to a new net, BEFORE placing anything, a copy of the schematic
     is saved as `<project>_L<n>.kicad_sch` (+ `.kicad_pro`) in the project folder: Ln is the
     state just completed, to which one can return (open that file and relaunch the program). The
     open document does not change (the copy is a save_as). The open schematic must be saved BY THE
     TECHNICIAN (Ctrl+S): the program does not save it.
  7. A background job (every POLLING_INTERVAL_SEC seconds) rechecks the position of all the pins
     still highlighted and redraws the circle if the technician moved or rotated the component.
     NOTE: rotating a component does NOT drag the wires (eeschema limit, verified 2026-10-03):
     rotate BEFORE connecting.

SYMBOLS (which symbol is placed for a component)
  The part is identified first: from the boardview description (part_identity.py, e.g.
  `OVP IC TPD6S300ARUKR//TI WQFN-20(3*3)` -> TPD6S300ARUKR). When the boardview says nothing
  usable the technician is asked for the part identifier (Enter = unknown -> a generic symbol
  that only depends on the pins, e.g. GENERIC_2P). Then symbol_resolver.py decides, in this order:
    1. KiCad's own libraries: a symbol with the SAME NAME IN FULL and the same pin numbers is used;
    2. our library `boardview_custom` (project folder): same rule, so a part built once is reused;
    3. otherwise a symbol named after the part is built from the boardview pins and written into
       `boardview_custom.kicad_sym` (symbol_library.py); a name that exists with other pins gets
       a numbered variant (<name>_2) and the technician is told.
  The symbol is placed with `place_symbol_from_library` followed by `update_items` (necessary for
  symbols taken from a library, otherwise their pins do not attach to wires). The id always
  exists in a real library, so "Update symbol from library" works. The question to the technician
  is asked in the MAIN thread, never inside the worker (see place_with_part_choice).

FIRST LAUNCH ON A PROJECT: the `boardview_custom` row must be added to the project's
`sym-lib-table` and KiCad does NOT see it until it re-reads the table (verified: reload_library
answers "not found"). In that case the program creates file and row, then STOPS with the
instructions (Preferences > Manage symbol libraries, or close and reopen the project) and must be
relaunched. It happens once per project.

WHY A SINGLE-THREAD WORKER (kicad_worker.py): if the periodic check and the command-line
interface both called the kipy API each from their own thread, they would risk breaking each
other (the kipy client uses a socket without an internal lock). Here ONLY ONE thread touches
kipy: both the interactive menu and the periodic check submit jobs to it through the same queue,
and they are executed serially, never concurrently.

MODULES USED (separation of tasks, so a future GUI reuses the modules unchanged and replaces only
this file with a graphical interface):
  - kicad_worker.py        -> owns the connection, serialises access
  - net_check.py           -> check logic (pure, no printing inside it)
  - boardview.py           -> reads the boardview (netlist + component descriptions)
  - part_identity.py       -> which chip is it: part identifier from a description
  - symbol_resolver.py     -> chooses the symbol: KiCad libraries, then ours, then a new one
  - symbol_library.py      -> writes the symbols into the project's `boardview_custom` library
    (uses symbol_builder.py as the single source of the geometry)
  - connection_help.py     -> explains KiCad connection problems
  - data_location.py       -> where the boardview data folder is (outside the repository)
  - netlist_solver.py      -> the "excluded net" rule (and the reference traversal solver)

KNOWN LIMITS:
  - no placement heuristic (horizontal row, fixed pitch);
  - an IC with hundreds of pins produces a very tall symbol (one pin every 100 mil).
"""

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SOLVER_DIR = Path(__file__).resolve().parent  # netlist_solver.py lives in this folder
from data_location import ask_netlist_path, data_dir  # noqa: E402  (module next to this file)
DATA_DIR = data_dir()  # boardview data files live OUTSIDE the repository

sys.path.insert(0, str(SOLVER_DIR))
from netlist_solver import is_huge_net, HUGE_NET_THRESHOLD  # noqa: E402

from kipy.geometry import Vector2  # noqa: E402
from kipy.common_types import Circle  # noqa: E402
from kipy.schematic_types import (  # noqa: E402
    BaseLabel, SchematicGraphicShape, SchematicSymbolInstance, SchematicSymbolOrientation as Orient, SheetPath,
)

from kicad_worker import KiCadWorker  # noqa: E402
import boardview  # noqa: E402
import connection_help  # noqa: E402
import net_check  # noqa: E402
import net_policy  # noqa: E402
import part_identity  # noqa: E402
import symbol_builder  # noqa: E402
import symbol_library  # noqa: E402
from boardview_data import BoardviewError  # noqa: E402
from symbol_resolver import SymbolResolver  # noqa: E402
from kipy.proto.common.types import (  # noqa: E402
    LibraryType, LibraryTableScope, base_types_pb2 as bt,
)

GRID_MIL = 50
PIN_MARK_RADIUS_MIL = 15
ROW_Y_MIL = 2000
ROW_START_X_MIL = 2000
ROW_STEP_X_MIL = 1000  # a symbol is ~600 mil wide (pins included): 1000 leaves room
POLLING_INTERVAL_SEC = 3  # how often the background job rechecks the circles


def rotate_local_point(point: Vector2, orientation, mirror_x: bool, mirror_y: bool) -> Vector2:
    """Applies to the LOCAL offset of a pin (relative to the symbol origin,
    as given by SchematicPin.position — see kipy/schematic_types.py)
    the rotation/mirroring of the symbol (SchematicSymbolInstance.transform),
    BEFORE adding it to the symbol's absolute position.

    The local offset must not be added to the position as it is: that only works for
    NEVER-rotated symbols (the default of place_symbol_from_library() is SSO_0/no rotation,
    verified live). If the technician rotates a component AFTER placing it, the circle would
    end up on the wrong pin.

    ROTATION CONVENTION: for 180 degrees the formula (x,y) -> (-x,-y) is
    valid regardless of the direction (clockwise/counterclockwise) — verified
    live on a component rotated by 180 degrees by the technician.
    For 90/270 the direction is deduced from the standard KiCad convention
    (Y downward) but NOT yet verified live in a targeted way:
    if one day it turned out to be reversed, swap the two SSO_90/SSO_270 branches."""
    x, y = point.x, point.y
    if orientation == Orient.SSO_90:
        x, y = -y, x
    elif orientation == Orient.SSO_180:
        x, y = -x, -y
    elif orientation == Orient.SSO_270:
        x, y = y, -x
    # SSO_0 and SSO_UNKNOWN: no rotation (x, y already correct)

    if mirror_x:
        x = -x
    if mirror_y:
        y = -y

    return Vector2.from_xy(x, y)


# ---------------------------------------------------------------------------
# User input (same style as netlist_solver.py, for consistency)
# ---------------------------------------------------------------------------

def _ask(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{msg}{suffix}: ").strip()
    return val if val else (default or "")


def _ask_required(msg: str) -> str:
    val = ""
    while not val:
        val = input(f"{msg}: ").strip()
        if not val:
            print("  (required, try again)")
    return val


# ---------------------------------------------------------------------------
# Session state: what is already on the schematic.
#
# IMPORTANT: the methods of this class touch `self.sch` (kipy), so they
# must ALWAYS be called through worker.run(...), never directly from another
# thread (see the invariant explained in kicad_worker.py). The internal
# dictionaries (instances, marked_pins, circles) need no locks of their own
# PRECISELY because this rule guarantees that they are always read/written
# by the same thread (the worker's), never concurrently.
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, netlist, sch, kicad, project_folder):
        self.netlist = netlist
        self.sch = sch
        self.kicad = kicad                        # to query the libraries (get_library_symbols)
        self.project_folder = Path(project_folder)  # where boardview_custom.kicad_sym lives
        self.instances = {}        # refdes -> SchematicSymbolInstance
        self.marked_pins = set()   # (refdes, pin) already proposed/handled (for the menu)
        self.circles = {}          # (refdes, pin) -> ACTIVE SchematicGraphicShape (absent if removed)
        self.expansions = {}      # boardview net name -> [(refdes, pin)] placed in all its expansions
        self._next_column = 0
        self._resolver = None      # SymbolResolver, created on first use INSIDE the worker
        self.not_in_place = set()  # references the technician declared NC (not mounted on the real board)

    def _next_position(self) -> Vector2:
        x = ROW_START_X_MIL + self._next_column * ROW_STEP_X_MIL
        self._next_column += 1
        return Vector2.from_xy_mils(x, ROW_Y_MIL)

    def component_already_placed(self, refdes: str) -> bool:
        return refdes in self.instances

    def placed_count(self) -> int:
        return len(self.instances)

    def component_pins(self, refdes: str) -> list:
        """Pin numbers of the component according to the boardview netlist."""
        return [pin for pin, _net in self.netlist.comp_to_pins.get(refdes, [])]

    def other_pins(self, refdes: str, pin: str) -> list:
        """The other pins of the same component (for the 'wrong pin' check)."""
        return [p for p in self.component_pins(refdes) if p != pin]

    # --- register of the pins placed per net (needed by command C: recheck) ---------
    # Like all the state of Session, it must be touched ONLY inside worker.run(...).

    def register_pin(self, net_name: str, refdes: str, pin: str):
        pins = self.expansions.setdefault(net_name, [])
        if (refdes, pin) not in pins:
            pins.append((refdes, pin))

    def net_pins(self, net_name: str) -> list:
        return list(self.expansions.get(net_name, []))

    def free_pins(self) -> list:
        """[(refdes, pin, net, net_already_expanded)] for the pins of the components ON THE SCHEMATIC
        that are not highlighted yet. The filter on huge nets is done by whoever prints."""
        free = []
        for refdes in sorted(self.instances):
            for pin, net in self.netlist.comp_to_pins.get(refdes, []):
                if (refdes, pin) not in self.marked_pins:
                    free.append((refdes, pin, net, net in self.expansions))
        return free

    def nets_in_progress(self) -> list:
        """[(net, members_left, first_placed_pin)] for the nets already worked on that still have
        members not highlighted, so the technician can see where the rest of a net is. The first
        placed pin is where the menu of that net is reopened from."""
        out = []
        for net in self.expansions:
            if is_huge_net(self.netlist, net, HUGE_NET_THRESHOLD):
                continue
            members = self.netlist.net_to_pins.get(net, [])
            left = [m for m in members if m not in self.marked_pins]
            start = next((m for m in members if m[0] in self.instances), None)
            if left and start:
                out.append((net, len(left), start))
        return out

    def marked_pins_list(self) -> list:
        """[(refdes, pin, to_confirm)] for all the pins highlighted so far."""
        return [(r, p, (r, p) in self.circles) for (r, p) in sorted(self.marked_pins)]

    def place_component(self, refdes: str, identifier: str | None = None, description: str = "",
                        not_in_place: bool = False):
        """Chooses the symbol of `refdes` (see symbol_resolver.py) and places it, if the component
        is not already on the schematic. `identifier` is the part identifier (from the boardview or
        typed by the technician); None means the part is unknown and a generic symbol is used.
        `not_in_place` (NC): the component exists only in the boardview, not on the real board: it
        gets a generic symbol and the instance is marked Do-Not-Populate.
        Returns the symbol_resolver.Resolution, or None if the component was already placed.

        After place_symbol_from_library, update_items() is needed: without it, the pin does
        not attach to the wires drawn afterwards — it holds for
        symbols TAKEN FROM A LIBRARY, unlike those created with create_items."""
        if refdes in self.instances:
            return None

        pin = self.component_pins(refdes)
        if not pin:
            raise KeyError(f"{refdes} has no pins in the netlist: nothing to place")

        if self._resolver is None:
            self._resolver = SymbolResolver(self.kicad, self.project_folder)
        prefix = symbol_builder.reference_prefix(refdes)
        if identifier:
            resolution = self._resolver.resolve(identifier, pin, prefix,
                                                description or symbol_library.DEFAULT_DESCRIPTION)
        else:
            resolution = self._resolver.resolve(part_identity.generic_symbol_name(pin), pin, prefix,
                                                "Generic symbol: part not identified", use_native=False)

        commit = self.sch.begin_commit()
        try:
            instance = self.sch.place_symbol_from_library(
                resolution.lib_id, self._next_position(), reference=refdes
            )
        finally:
            self.sch.push_commit(commit, f"Place boardview component {refdes}")

        instance.show_pin_numbers = True
        if not_in_place:
            instance.attributes.do_not_populate = True
            self.not_in_place.add(refdes)
        commit = self.sch.begin_commit()
        try:
            self.sch.update_items(instance)
        finally:
            self.sch.push_commit(commit, f"Heal symbol {refdes} (update_items)")

        in_library = {p.number for p in instance.definition.pins}
        if in_library != set(pin):
            print(f"  [!] WARNING: {resolution.lib_id} has pins {sorted(in_library)} but the "
                  f"netlist of {refdes} has {sorted(pin)}")

        self.instances[refdes] = instance
        return resolution

    def evaluate(self, leaving: bool):
        """One round of checks over everything the technician has done (see net_policy for the
        severity rules): the pins of every net worked on (net_check.check) and the whole schematic
        (net_check.audit). It also keeps the circles right: a pin carries its circle exactly while it
        is not confirmed, i.e. not wired, or on a wrong net, or on a net with a grave error.
        `leaving`: the technician is about to move to another net or to end the session."""
        symbols = self.sch.get_symbols()
        by_ref = {s.reference_field.text.value: s for s in symbols}
        labels = net_check.net_labels(self.sch)
        net_map = net_check.net_position_map(self.sch, labels)

        def position(ref, pin):
            return self._pin_position(by_ref[ref], pin)

        results = {}
        for net, pins in self.expansions.items():
            present = [p for p in pins if p[0] in by_ref]
            if present:
                results[net] = net_check.check(self.sch, position, net, present,
                                               other_pins_fn=self.other_pins, net_map=net_map)
        placed = []
        for refdes in self.instances:
            if refdes in by_ref:
                seen = set()
                for pin, net in self.netlist.comp_to_pins[refdes]:
                    if pin not in seen:
                        seen.add(pin)
                        placed.append((refdes, pin, net))
        all_pin_points = [point for s in symbols for point in self._pin_points(s)]
        audit = net_check.audit(self.sch, placed, position, net_map=net_map, all_pin_points=all_pin_points,
                                 label_points=[label.position for label in labels])

        # a net with fewer than two pins placed cannot be wired yet: nothing to report about its wires
        single_pin_nets = {n for n in results if len([p for p in self.expansions[n] if p[0] in by_ref]) < 2}
        findings = net_policy.evaluate(results, audit, leaving, single_pin_nets)
        grave_nets = {f.kicad_net for f in findings if f.severity == net_policy.GRAVE and f.kicad_net}

        confirmed_nets = []
        for net, r in results.items():
            net_is_fine = r.kicad_net_name not in grave_nets
            for refdes, pin in self.expansions[net]:
                if refdes in by_ref:
                    confirmed = net_is_fine and (refdes, pin) in r.confirmed_ok_pins
                    self._set_circle(refdes, pin, wanted=not confirmed)
            if net_is_fine and r.confirmed_ok_pins:
                confirmed_nets.append(net)
        return net_policy.Evaluation(findings, confirmed_nets)

    def apply_fix(self, finding) -> int:
        """Applies the automatic fix of a grave finding: removes the wires and labels of the
        offending KiCad net (or only its labels). One commit, so Ctrl+Z in KiCad restores them.
        Returns how many items were removed."""
        items = net_check.net_items(self.sch, finding.kicad_net)
        if finding.fix == net_policy.REMOVE_LABELS:
            items = [i for i in items if isinstance(i, BaseLabel)]
        else:
            items = [i for i in items if not isinstance(i, SchematicSymbolInstance)]
        if not items:
            return 0
        commit = self.sch.begin_commit()
        try:
            self.sch.remove_items(items)
        finally:
            self.sch.push_commit(commit, f"Remove net '{finding.kicad_net}' ({finding.kind})")
        return len(items)

    def _set_circle(self, refdes: str, pin: str, wanted: bool):
        """Makes the circle of a pin present (redrawn at the pin's current position) or absent."""
        if wanted:
            if (refdes, pin) in self.circles:
                self.resync_circle(refdes, pin)
            else:
                self.circles[(refdes, pin)] = self._draw_circle(self.absolute_pin_position(refdes, pin))
        else:
            self.remove_circle(refdes, pin)

    def _fresh_instance(self, refdes: str):
        """Re-reads the instance LIVE through its ID (sch.get_items_by_id),
        instead of trusting the copy in self.instances captured at the moment
        of placement. NECESSARY because the program may keep running for a long
        time (interactive menu, periodic job): in the meantime the technician can
        move or ROTATE the component from KiCad's GUI, and the old in-memory copy
        would never know (found when the component was rotated by hand)."""
        old = self.instances[refdes]
        updated = self.sch.get_items_by_id(old.id)
        if not updated:
            raise KeyError(f"{refdes} no longer found on the schematic (ID {old.id} missing: "
                            f"maybe the technician deleted it?)")
        self.instances[refdes] = updated[0]
        return updated[0]

    def absolute_pin_position(self, refdes: str, pin: str) -> Vector2:
        """Absolute position of a pin: symbol position + local offset of the pin,
        ROTATED according to the current orientation/mirroring of the symbol
        (see rotate_local_point). ALWAYS re-reads the fresh instance from KiCad
        (see _fresh_instance), never the potentially stale in-memory copy."""
        return self._pin_position(self._fresh_instance(refdes), pin)

    @staticmethod
    def _pin_position(instance, pin: str) -> Vector2:
        """Only the computation, on an instance ALREADY read (no call to KiCad): it serves both
        absolute_pin_position and the session resume, which has just read all the
        symbols with a single get_symbols()."""
        p = next((p for p in instance.definition.pins if p.number == pin), None)
        if p is None:
            raise KeyError(f"The symbol {instance.reference_field.text.value} has no pin {pin!r}")
        rotated_local = rotate_local_point(
            p.position, instance.transform.orientation,
            instance.transform.mirror_x, instance.transform.mirror_y,
        )
        return Vector2.from_xy(instance.position.x + rotated_local.x,
                                instance.position.y + rotated_local.y)

    @staticmethod
    def _pin_points(instance) -> list:
        """The absolute position of EVERY pin of a symbol on the sheet, ours or not."""
        points = []
        for p in instance.definition.pins:
            rotated_local = rotate_local_point(
                p.position, instance.transform.orientation,
                instance.transform.mirror_x, instance.transform.mirror_y,
            )
            points.append(Vector2.from_xy(instance.position.x + rotated_local.x,
                                          instance.position.y + rotated_local.y))
        return points

    # --- resuming a session from an already saved schematic ---------------------------

    def rebuild_from_schematic(self) -> dict:
        """Puts the session state back together by reading ONLY the open schematic (no
        separate state file): this way one can save, close, reopen any file
        (even a _L<n> snapshot) and continue. The criterion, all verifiable on the schematic:
          - components: symbols whose reference is in the netlist and whose pin numbers are
            exactly those of that component in the boardview (from any library);
          - pins TO CONFIRM: those with a circle of ours (radius PIN_MARK_RADIUS_MIL)
            centred on the pin;
          - pins ALREADY CONFIRMED: pins connected to a net that carries exactly the name
            of their net in the boardview (it is the condition with which a pin loses the circle);
          - expanded nets (register): the nets of all the previous pins.
        Pins on huge nets (GND, >50 pins) are never considered handled. A circle that no
        longer falls on any pin (component moved by hand) is ignored."""
        for s in self.sch.get_symbols():
            ref = s.reference_field.text.value
            if ref in self.netlist.comp_to_pins and \
                    {p.number for p in s.definition.pins} == set(self.component_pins(ref)):
                self.instances[ref] = s

        positions = {}
        for ref, inst in self.instances.items():
            for pin, _net in self.netlist.comp_to_pins[ref]:
                positions[(ref, pin)] = self._pin_position(inst, pin)

        radius = PIN_MARK_RADIUS_MIL * 25400
        for shape in self.sch.get_shapes():
            if not isinstance(shape.shape, Circle):
                continue
            center = shape.shape.center
            if abs(shape.shape.radius() - radius) > 2000:   # radius is a METHOD of Circle
                continue
            for key, pos in positions.items():
                if net_check.near(center, pos):
                    self.circles[key] = shape
                    self.marked_pins.add(key)
                    break

        to_confirm = len(self.circles)
        mapping = net_check.net_position_map(self.sch)
        for key, pos in positions.items():
            if key in self.marked_pins:
                continue
            net = self.netlist.pin_to_net[key]
            if is_huge_net(self.netlist, net, HUGE_NET_THRESHOLD):
                continue
            kicad_net = net_check.net_from_map(mapping, pos)
            if kicad_net is not None and kicad_net.lstrip("/") == net:
                self.marked_pins.add(key)

        for key in sorted(self.marked_pins):
            self.register_pin(self.netlist.pin_to_net[key], *key)

        if self.instances:
            x_max_mil = max(i.position.x for i in self.instances.values()) / 25400
            self._next_column = max(
                0, math.ceil((x_max_mil + ROW_STEP_X_MIL - ROW_START_X_MIL) / ROW_STEP_X_MIL))
        return {"components": len(self.instances), "to_confirm": to_confirm,
                "confirmed": len(self.marked_pins) - to_confirm}

    # --- snapshots: one file per net ----------------------------------------------------

    def save_snapshot(self):
        """Saves a COPY of the schematic as it is now in `<project>_L<n>.kicad_sch` (n = first
        free index) in the project folder, without moving the open document.
        Called when moving to a new net: the copy fixes the state just completed,
        to which one can return (open that file and relaunch the program).

        WARNING: `self.sch` comes from get_schematic(), which strips the sheet path from the
        document, and with that document save_as() writes an EMPTY schematic (verified
        2026-10-03). So a Schematic explicitly bound to the root sheet is used."""
        doc = self.kicad.get_open_documents(bt.DOCTYPE_SCHEMATIC)[0]
        radice = self.sch.for_sheet(SheetPath(proto=doc.sheet_path))
        name = doc.project.name
        present = [int(m.group(1)) for f in self.project_folder.glob(f"{name}_L*.kicad_sch")
                    if (m := re.fullmatch(re.escape(name) + r"_L(\d+)\.kicad_sch", f.name))]
        n = max(present) + 1 if present else 0
        destination = self.project_folder / f"{name}_L{n}.kicad_sch"
        radice.save_as(str(destination), overwrite=False, include_project=True)
        return destination

    def highlight_pin(self, refdes: str, pin: str):
        if (refdes, pin) in self.marked_pins:
            return
        center = self.absolute_pin_position(refdes, pin)
        creato = self._draw_circle(center)
        self.circles[(refdes, pin)] = creato
        self.marked_pins.add((refdes, pin))

    def _draw_circle(self, center: Vector2):
        circle = Circle.from_center_radius(center, PIN_MARK_RADIUS_MIL * 25400)
        shape = SchematicGraphicShape()
        shape.shape = circle
        commit = self.sch.begin_commit()
        try:
            creati = self.sch.create_items(shape)
        finally:
            self.sch.push_commit(commit, "Draw circle on pin")
        return creati[0]

    def remove_circle(self, refdes: str, pin: str):
        """The pin was verified OK (same net, correct name): the
        circle has served its purpose and is removed."""
        circle = self.circles.pop((refdes, pin), None)
        if circle is None:
            return
        commit = self.sch.begin_commit()
        try:
            self.sch.remove_items(circle)
        finally:
            self.sch.push_commit(commit, f"Remove circle: {refdes}.{pin} verified OK")

    def resync_circle(self, refdes: str, pin: str):
        """If the component was moved after the highlighting, the
        circle (fixed coordinates) stays behind. It recomputes it and redraws it at the CURRENT
        position of the pin, deleting the old one if it no longer coincides.

        The check is on `self.circles` (is there an ACTIVE marker?), not on
        `self.marked_pins` (which stays True even after the marker was removed because
        confirmed OK) — otherwise resync_all() would wrongly redraw a circle on a pin
        already verified and correctly "closed"."""
        if (refdes, pin) not in self.circles:
            return
        current = self.absolute_pin_position(refdes, pin)
        old_circle = self.circles[(refdes, pin)]
        old_center = old_circle.shape.center
        if net_check.near(old_center, current):
            return  # already aligned, no action
        commit = self.sch.begin_commit()
        try:
            self.sch.remove_items(old_circle)
        finally:
            self.sch.push_commit(commit, f"Remove misaligned circle of {refdes}.{pin}")
        self.circles[(refdes, pin)] = self._draw_circle(current)

    def resync_all(self):
        """Periodic job (see POLLING_INTERVAL_SEC): rechecks ALL the circles still
        active, not only those of the last expansion. Designed to be passed to
        worker.schedule_periodic() as it is."""
        for (refdes, pin) in list(self.circles.keys()):
            self.resync_circle(refdes, pin)


# ---------------------------------------------------------------------------
# Logic of the net expansion menu
# ---------------------------------------------------------------------------

def members_to_propose(netlist, net_name, already_marked):
    members_list = netlist.net_to_pins.get(net_name, [])
    return [(r, p) for (r, p) in members_list if (r, p) not in already_marked]


AUTO_REMOVE_GRAVE_NETS = True   # solve a grave error by deleting the offending net (Ctrl+Z restores it)
MAX_FIX_ROUNDS = 5              # safety limit: fixes that keep coming back are reported, not repeated


def run_checks(worker: KiCadWorker, session: Session, leaving: bool, title: str,
               allow_quit: bool = False) -> bool:
    """Checks everything and applies the technician's severity rules (net_policy):
    NOTICE findings are printed and the program goes on; GRAVE findings that have an automatic
    fix are fixed (the offending net is deleted) and the checks run again; GRAVE findings that
    only the technician can solve BLOCK the program: it accepts nothing until they are gone.
    `leaving`: the technician is moving to another net or ending (unwired pins become GRAVE).
    `allow_quit`: the blocking prompt also accepts QUIT (used when ending the session).
    Returns True when no grave error is left, False if the technician quit with some left.
    Runs in the MAIN thread (it asks questions); every KiCad access goes through the worker."""
    if worker.run(session.placed_count) == 0:
        return True
    print(f"\n  --- {title} ---")
    fix_rounds = 0
    while True:
        evaluation = worker.run(session.evaluate, leaving)
        grave = [f for f in evaluation.findings if f.severity == net_policy.GRAVE]
        fixable = [f for f in grave if f.fix] if AUTO_REMOVE_GRAVE_NETS and fix_rounds < MAX_FIX_ROUNDS else []
        if fixable:
            fix_rounds += 1
            fixed_nets = set()
            for f in fixable:
                if f.kicad_net in fixed_nets:   # one fix per KiCad net per round: the next round re-checks
                    continue
                fixed_nets.add(f.kicad_net)
                print(f"  [GRAVE] {f.message}")
                removed = worker.run(session.apply_fix, f)
                print(f"          Solved by removing {removed} wire/label item(s) of KiCad net "
                      f"'{f.kicad_net}' (Ctrl+Z in KiCad brings them back).")
            continue
        for net in evaluation.confirmed_nets:
            print(f"  [OK] net '{net}': the pins wired so far are correct.")
        for f in evaluation.findings:
            print(f"  [{f.severity}] {f.message}")
        if not evaluation.findings and not evaluation.confirmed_nets:
            print("  [OK] Nothing to report.")
        if not grave:
            return True
        print("  The program is blocked until the GRAVE errors above are solved.")
        ending = " or type QUIT to end anyway" if allow_quit else ""
        answer = _ask(f"Solve it in KiCad, then press Enter to check again{ending} (Ctrl+C aborts)", "")
        if allow_quit and answer.strip().upper() == "QUIT":
            return False


def parse_pin_reference(netlist, text: str):
    """(refdes, pin) for text like UT27.7 when that pin exists in the boardview: the component is
    matched as match_refdes does, the pin exactly or else ignoring case when that is unambiguous.
    None when the text is not a pin of the boardview."""
    if "." not in text:
        return None
    refdes_text, pin_text = text.rsplit(".", 1)
    refdes = match_refdes(netlist, refdes_text.strip())
    if refdes is None:
        return None
    pins = {pin for pin, _net in netlist.comp_to_pins[refdes]}
    pin_text = pin_text.strip()
    if pin_text in pins:
        return refdes, pin_text
    found = [p for p in pins if p.lower() == pin_text.lower()]
    return (refdes, found[0]) if len(found) == 1 else None


def expand_net(worker: KiCadWorker, session: Session, start_refdes: str, start_pin: str):
    """IMPORTANT NOTE: this
    function runs in the MAIN THREAD, not inside the worker — it is the one that asks
    the interactive questions (_ask/input()). If it ran inside the worker (as
    in the first version of this file), while waiting for the technician's
    answer the worker thread would stay blocked and the periodic
    circle resynchronisation job could NEVER run in the meantime —
    exactly the bug observed live with the technician. Every single operation
    that touches KiCad goes instead through worker.run(...).

    Returns None, or (refdes, pin) when the technician typed a pin of ANOTHER net: a net does not
    have to be closed before starting another, so the caller moves on from that pin."""
    netlist = session.netlist
    net_name = netlist.pin_to_net.get((start_refdes, start_pin))
    if net_name is None:
        print(f"ERROR: pin {start_refdes}.{start_pin} not found in the netlist.")
        return

    if is_huge_net(netlist, net_name, HUGE_NET_THRESHOLD):
        print(f"[!] The net '{net_name}' is excluded (GND or >{HUGE_NET_THRESHOLD} pins): "
              f"declared but NEVER expanded, by the solver's rule.")
        return

    print(f"\n[+] Current net: '{net_name}'")
    worker.run(session.register_pin, net_name, start_refdes, start_pin)

    while True:
        proposals = members_to_propose(netlist, net_name, session.marked_pins)
        if not proposals:
            print("  All the members of this net have already been highlighted.")
            run_checks(worker, session, False, f"Check of net '{net_name}'")
            return

        print("  Components still to insert on this net:")
        for i, (r, p) in enumerate(proposals, start=1):
            on_schematic = " (already on the schematic)" if session.component_already_placed(r) else ""
            print(f"    {i}. {r}.{p}{on_schematic}")
        print("    C. check now and stay on this net")
        print("    0. end of expansion of this net")

        # THIS input() runs in the main thread: the worker stays free
        # to run the periodic job while we wait for the answer.
        choice = _ask("Which to insert: REFDES.PIN (e.g. UT27.7, also a pin of another net), or its "
                      "number in the list", "0")
        if choice == "0" or choice == "":
            run_checks(worker, session, False, f"Check of net '{net_name}'")
            return
        if choice.upper() == "C":
            run_checks(worker, session, False, f"Check of net '{net_name}'")
            continue
        if "." in choice:
            target = parse_pin_reference(netlist, choice)
            if target is None:
                print(f"  {choice} is not a pin of the boardview. Expected REFDES.PIN (e.g. UT27.7), try again.")
                continue
            refdes, pin = target
            if netlist.pin_to_net[target] != net_name:
                print(f"  {refdes}.{pin} belongs to net '{netlist.pin_to_net[target]}', not to this one: "
                      f"moving on to it.")
                return target
            if target not in proposals:
                print(f"  {refdes}.{pin} is already highlighted.")
                continue
        else:
            try:
                idx = int(choice)
                refdes, pin = proposals[idx - 1]
            except (ValueError, IndexError):
                print("  Invalid choice, try again.")
                continue

        place_with_part_choice(worker, session, refdes)
        worker.run(session.highlight_pin, refdes, pin)
        worker.run(session.register_pin, net_name, refdes, pin)
        print(f"  [+] {refdes} placed (or already present), pin {pin} highlighted with the circle.")


@dataclass
class PartChoice:
    """What is known about the part of a component: its identifier (None = unknown), the text to
    keep in the symbol's description, and whether it is NC (not in place on the real board)."""
    identifier: str | None
    description: str = ""
    not_in_place: bool = False


def choose_part(session: Session, refdes: str) -> PartChoice:
    """The part of `refdes`. The identifier comes from the boardview description; when the
    boardview does not say, the technician is asked: a part identifier, Enter = unknown (generic
    symbol) or NC = not in place (the component exists only in the boardview: generic symbol marked
    Do-Not-Populate). Runs in the MAIN thread: it uses input()."""
    component = session.netlist.components.get(refdes)
    description = component.description if component else None
    identifier = part_identity.extract_part_identifier(description)
    if identifier:
        return PartChoice(identifier, description)
    if description:
        print(f"  [?] {refdes}: the boardview describes it as '{description}', "
              f"but no part identifier can be read from that.")
    else:
        print(f"  [?] {refdes}: the boardview has no data about this component "
              f"({len(session.component_pins(refdes))} pins).")
    answer = _ask(f"Part identifier of {refdes} (Enter = unknown, NC = not in place)", "").strip()
    if answer.upper() == "NC":
        return PartChoice(None, "", True)
    if answer:
        return PartChoice(answer, "Part identifier typed by the technician")
    return PartChoice(None)


def print_resolution(refdes: str, choice: PartChoice, resolution):
    """Tells the technician which symbol was chosen and why."""
    if choice.not_in_place:
        print(f"  [+] {refdes}: NC (not in place) -> generic symbol {resolution.lib_id} "
              f"({resolution.origin}), marked Do-Not-Populate.")
    elif choice.identifier is None:
        print(f"  [+] {refdes}: part unknown -> generic symbol {resolution.lib_id} ({resolution.origin}).")
    else:
        reasons = {"native": "KiCad library symbol with the same name and pins",
                   "custom (existing)": "already built before, same pins",
                   "custom (new)": "new symbol built from the boardview pins"}
        print(f"  [+] {refdes}: part {choice.identifier} -> {resolution.lib_id} ({reasons[resolution.origin]}).")
    for note in filter(None, resolution.note.split("; ")):
        print(f"      [!] {note}")


def place_with_part_choice(worker: KiCadWorker, session: Session, refdes: str):
    """Places `refdes` if it is not on the schematic yet: first the part is identified (this may
    ask the technician, in the main thread), then the worker chooses the symbol and places it."""
    if worker.run(session.component_already_placed, refdes):
        return
    choice = choose_part(session, refdes)
    resolution = worker.run(session.place_component, refdes, choice.identifier, choice.description,
                            choice.not_in_place)
    print_resolution(refdes, choice, resolution)


MAX_FREE_PIN_ROWS = 60  # beyond this, the list of free pins is truncated


def prepare_start_pin(worker: KiCadWorker, session: Session, refdes: str, pin: str,
                             snapshot: bool = False) -> bool:
    """Prepares the pin from which to open the menu of a net: checks that it exists and that its
    net is expandable, places the component if it is not on the schematic yet, highlights the
    pin (if it is not already). True if one can proceed with expand_net(). It holds both for the
    first component and for every restart (pin of an already placed component or
    of a new one taken from the boardview). The check on the huge net happens BEFORE
    placement: a component on GND must not be put on the schematic at all."""
    netlist = session.netlist
    net = netlist.pin_to_net.get((refdes, pin))
    if net is None:
        print(f"  The pin {refdes}.{pin} does not exist in the netlist. Try again.")
        return False
    if is_huge_net(netlist, net, HUGE_NET_THRESHOLD):
        print(f"  [!] The net '{net}' of {refdes}.{pin} is excluded (GND or >{HUGE_NET_THRESHOLD} pins): "
              f"it is not expanded, the component is not placed. Choose another pin.")
        return False
    on_schematic = session.component_already_placed(refdes)
    if snapshot:
        # We are moving to a new net: BEFORE touching the schematic a copy of it is saved, which fixes
        # the state just completed (one file per net).
        try:
            destination = worker.run(session.save_snapshot)
            print(f"  [+] Copy of the current state saved: {destination.name}")
        except Exception as e:  # noqa: BLE001 — the snapshot is a safety net, it does not block the work
            print(f"  [!] Copy of the state NOT saved ({e}). Going on anyway.")
    place_with_part_choice(worker, session, refdes)
    worker.run(session.highlight_pin, refdes, pin)
    status = "already on the schematic" if on_schematic else "placed"
    print(f"  [+] {refdes} {status}, pin {pin} highlighted (net '{net}').")
    return True


def print_free_pins(worker: KiCadWorker, session: Session):
    """List of the free pins of the components on the schematic, with their net, from which one
    can restart. Pins on huge nets (not expandable) are excluded."""
    free = worker.run(session.free_pins)
    lines = [(r, p, n, expanded) for (r, p, n, expanded) in free
             if not is_huge_net(session.netlist, n, HUGE_NET_THRESHOLD)]
    if not lines:
        print("\nFree pins of the components on the schematic: none (other pins only on excluded nets).")
        return
    print("\nFree pins of the components on the schematic (from which you can restart):")
    for (r, p, n, expanded) in lines[:MAX_FREE_PIN_ROWS]:
        note = "  (already expanded)" if expanded else ""
        print(f"  {r}.{p}  -> net '{n}'{note}")
    if len(lines) > MAX_FREE_PIN_ROWS:
        print(f"  ... and {len(lines) - MAX_FREE_PIN_ROWS} more free pins")


def print_component_card(session: Session, refdes: str):
    """Pins of a boardview component with their net (to choose from which one to restart)."""
    pins = session.netlist.comp_to_pins.get(refdes, [])
    status = "already on the schematic" if session.component_already_placed(refdes) else "not yet on the schematic"
    print(f"  {refdes} ({status}), {len(pins)} pins:")
    for pin, net in pins[:MAX_FREE_PIN_ROWS]:
        huge = "  (excluded net)" if is_huge_net(session.netlist, net, HUGE_NET_THRESHOLD) else ""
        print(f"    {refdes}.{pin}  -> net '{net}'{huge}")
    if len(pins) > MAX_FREE_PIN_ROWS:
        print(f"    ... and {len(pins) - MAX_FREE_PIN_ROWS} more pins")


def ask_retry_when_busy(error: Exception) -> bool:
    """KiCad refused a command because a dialog or an interactive command is open (typically a wire
    still being drawn): tell the technician and try again when they press Enter."""
    print("\n[!] KiCad is busy (a dialog or an interactive command is open: finish or cancel it, "
          "for example press Esc after drawing a wire).")
    _ask("Press Enter to try again (Ctrl+C aborts)", "")
    return True


def match_refdes(netlist, text: str):
    """The component named `text` in the boardview: exact match, else a unique match ignoring case."""
    if text in netlist.comp_to_pins:
        return text
    found = [r for r in netlist.comp_to_pins if r.lower() == text.lower()]
    return found[0] if len(found) == 1 else None


def choose_pin_of(session: Session, refdes: str):
    """REFDES typed without a pin: if the component has a single pin that can be expanded it is used
    directly; otherwise its pins are shown and the technician picks one (Enter = go back).
    Pins on excluded nets (GND, >50 pins) are never offered. Runs in the MAIN thread (input())."""
    netlist = session.netlist
    usable = []
    for pin, net in netlist.comp_to_pins.get(refdes, []):
        if pin not in usable and not is_huge_net(netlist, net, HUGE_NET_THRESHOLD):
            usable.append(pin)
    if not usable:
        print(f"  {refdes} has only pins on excluded nets (GND or >{HUGE_NET_THRESHOLD} pins): "
              f"nothing to expand from it.")
        return None
    if len(usable) == 1:
        print(f"  {refdes}: its only pin that can be expanded is {usable[0]} "
              f"(net '{netlist.pin_to_net[(refdes, usable[0])]}').")
        return usable[0]
    print_component_card(session, refdes)
    answer = _ask(f"Which pin of {refdes} (Enter = go back)", "").strip()
    if answer in usable:
        return answer
    if answer:
        print(f"  {refdes} has no usable pin '{answer}'.")
    return None


def recheck(worker: KiCadWorker, session: Session):
    """Command C: checks everything again (every net worked on and the whole schematic)."""
    run_checks(worker, session, False, "Recheck of everything")


# ---------------------------------------------------------------------------
# Main — every function that touches `sch` is passed to worker.run(), never
# called directly. The worker interleaves, in the same thread, also the
# periodic circle resynchronisation job.
# ---------------------------------------------------------------------------

def _connect_to_kicad():
    """Executed INSIDE the worker: creates the connection here, so it lives for
    the whole duration of the program in the same thread that will always use it."""
    from kipy import KiCad
    kicad = KiCad()
    sch = kicad.get_schematic()
    version = kicad.get_version()
    folder = Path(kicad.get_open_documents(bt.DOCTYPE_SCHEMATIC)[0].project.path)
    return sch, version, kicad, folder


def _library_visible(kicad) -> bool:
    """Executed INSIDE the worker: is the row of our library in the table that
    KiCad has LOADED for the open project? (Having written it in the file is not enough.)"""
    lines = kicad.get_library_statuses(LibraryType.LT_SYMBOL, LibraryTableScope.LTS_PROJECT)
    return any(r.nickname == symbol_library.LIBRARY_NAME for r in lines)


def go_to_pin(worker: KiCadWorker, session: Session, refdes: str, pin: str) -> bool:
    """Leaves the current net and gets ready to expand the net of `refdes.pin`: the checks of leaving,
    then the pin is prepared (component placed, pin highlighted, _L<n> copy saved). False if the pin
    cannot be used (the reason was printed)."""
    run_checks(worker, session, True, "Check before leaving the net")
    return prepare_start_pin(worker, session, refdes, pin, snapshot=True)


def restart_prompt(worker: KiCadWorker, session: Session):
    """Shows the highlighted pins and the free ones and asks where to restart from. Returns
    (refdes, pin) of the new starting point, ALREADY PREPARED (component placed, pin
    highlighted, _L<n> copy saved), or None if the technician ends. C rechecks the
    connections and re-proposes the prompt; an invalid input only re-proposes the prompt."""
    netlist = session.netlist
    while True:
        print("\nPins already highlighted on the schematic:")
        listing = worker.run(session.marked_pins_list)
        for i, (r, p, to_confirm) in enumerate(listing, start=1):
            note = "  (to confirm)" if to_confirm else ""
            print(f"  {i}. {r}.{p}{note}")
        in_progress = worker.run(session.nets_in_progress)
        if in_progress:
            print("\nNets worked on that still have members to place (type the net name to continue it):")
            for net, left, _start in in_progress[:MAX_FREE_PIN_ROWS]:
                print(f"  {net}  ({left} member(s) not placed yet)")
        print_free_pins(worker, session)

        reply = _ask(
            "Where to restart from: a NET NAME from the list above, REFDES.PIN (a free pin of a "
            "component on the schematic, or of any component of the boardview), REFDES alone to see "
            "its pins, C = recheck the connections, Enter = end",
            ""
        )
        if not reply:
            run_checks(worker, session, True, "Final check", allow_quit=True)
            return None
        if reply.upper() == "C":
            recheck(worker, session)
            continue
        by_net = {net: start for net, _left, start in in_progress}
        if reply in by_net:
            reply = ".".join(by_net[reply])     # same as typing the net's first placed pin
        if "." not in reply:
            refdes = match_refdes(netlist, reply)
            if refdes is None:
                print("  Expected format: REFDES.PIN (e.g. R5609.2), a net name from the list, "
                      "REFDES alone, or C. Try again.")
                continue
            pin = choose_pin_of(session, refdes)
            if pin is None:
                continue
            reply = f"{refdes}.{pin}"
        refdes, pin = reply.rsplit(".", 1)
        refdes = match_refdes(netlist, refdes) or refdes
        if refdes not in netlist.comp_to_pins:
            print(f"  The component {refdes} does not exist in the netlist. Try again.")
            continue
        if not go_to_pin(worker, session, refdes, pin):
            continue
        return refdes, pin


def main():
    print("=== interactive_session - resume from a saved schematic, one copy per net ===")
    print("(KiCad 11 must already be open, with a schematic loaded and the API server enabled)\n")

    netlist_path = ask_netlist_path(_ask, DATA_DIR / "UX.cad")
    try:
        netlist = boardview.read_boardview(netlist_path)
    except BoardviewError as error:
        print(f"[!] {error}")
        sys.exit(1)
    print(f"[+] Netlist loaded: {netlist.stats()}")

    worker = KiCadWorker()
    worker.on_busy = ask_retry_when_busy
    worker.start()
    try:
        print("\n[+] Connecting to KiCad...")
        try:
            sch, version, kicad, folder = worker.run(_connect_to_kicad)
        except Exception as error:  # noqa: BLE001 — only connection problems are explained
            explanation = connection_help.explain(error)
            if explanation is None:
                raise
            print(explanation)
            sys.exit(3)
        print(f"[+] Connected: KiCad {version} (project: {folder})")

        # Symbol library: file and table row (file writing only, no call to
        # KiCad, so it can be done here in the main thread).
        symbol_library.ensure_library(folder)
        if not worker.run(_library_visible, kicad):
            print(f"\n[!] KiCad has not loaded the library '{symbol_library.LIBRARY_NAME}' "
                  f"for this project yet.\n"
                  f"    File and table row are ready in: {folder}\n"
                  f"    Make KiCad re-read the table: in 'Preferences > Manage symbol libraries' "
                  f"check that the library appears among the 'Project specific libraries' "
                  f"and press OK, or close and reopen the project.\n"
                  f"    Then relaunch this program (it happens only once per project).")
            sys.exit(2)

        session = Session(netlist, sch, kicad, folder)

        # RESUME: if the open schematic already contains components of the boardview (because it
        # was saved and reopened, or it is an _L<n> copy), the state is rebuilt from
        # there and we go on; otherwise it is a new session and the first component is asked.
        summary = worker.run(session.rebuild_from_schematic)
        to_expand = None
        if summary["components"] > 0:
            print(f"\n[+] RESUME: the schematic already contains {summary['components']} components of the "
                  f"boardview: {summary['confirmed']} pins already confirmed, "
                  f"{summary['to_confirm']} to confirm.")
            print("    (To start over from scratch empty the schematic or open an empty one.)")
            recheck(worker, session)
        else:
            refdes0 = _ask_required("PRIMARY starting component (REFDES, e.g. R5609)")
            pin0 = _ask_required("Starting pad/pin (e.g. 1)")
            if not prepare_start_pin(worker, session, refdes0, pin0):
                print("    Relaunch the program choosing another starting pin.")
                sys.exit(1)
            print(f"[+] Primary component {refdes0}: ready.")
            to_expand = (refdes0, pin0)

        # Background job: rechecks all the circles every POLLING_INTERVAL_SEC
        # seconds, serialised in the same worker queue (see kicad_worker.py).
        # It can run freely ALSO while the main thread waits for an
        # answer from the technician, because expand_net() does NOT run inside the worker
        # (see its docstring).
        worker.schedule_periodic(session.resync_all, POLLING_INTERVAL_SEC,
                                 log_name="resync circles")

        while True:
            if to_expand is not None:
                jump = expand_net(worker, session, *to_expand)
                if jump is not None and go_to_pin(worker, session, *jump):
                    to_expand = jump
                    continue
            to_expand = restart_prompt(worker, session)
            if to_expand is None:
                print("[+] Session ended. Save the schematic (Ctrl+S) to be able to resume it: "
                      "on the next run the program rebuilds by itself where you left off.")
                return
    finally:
        worker.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[+] Aborted with Ctrl+C. Nothing is lost: the schematic stays open in KiCad "
              "(save it with Ctrl+S) and the next run resumes from it.")
        sys.exit(130)
