#!/usr/bin/env python3
"""
boardview_data.py — MODULE (not launched on its own)

The UNIFIED in-memory FORMAT of a boardview: it is the only thing the rest of the program knows
about a boardview. One reading function per format (in boardview.py: `read_gencad`, in the future
the one for the .fz, etc.) turns its own file into a `BoardviewData`; nothing else in the program
knows which formats exist. This file contains ONLY the data structure (the shared "vocabulary")
and the derived views: no file reading, no KiCad.

HOW IT WAS DESIGNED (decisions of the technician, 2026-10-11)
  - It is designed for the RICHEST case, not the poorest one: the .cad format has less data than
    the .fz, but the structure must already have room for everything the .fz (and other formats)
    can give, even if nobody uses it today. A reader fills what its format contains and leaves
    None for the rest: NEVER invent a datum.
  - What matters immediately to the project: nets, pins/pads, component data (description, codes,
    brand, value...). But the SPATIAL DATA MUST BE KEPT (position of pins and components, side,
    rotation, outline): today the flow does not use them, in the future they will be needed (for
    example to group components by proximity on the schematic).
  - UNITS: all lengths of the unified format are in MILLIMETRES. The reader converts and records
    the original unit in `meta` (`original_unit`), so the conversion is always verifiable and
    reversible.
  - COORDINATES: the board's coordinate system as given by the file, absolute; the side
    (TOP/BOTTOM) is separate information (no mirroring applied by the format).
    If a file asks for a transformation that the reader cannot apply with certainty, the
    coordinates stay None and the reason ends up in `meta["warnings"]`: better empty than wrong.
  - `extra` (free dictionary on BoardviewData, component and pin) collects what a format has and
    our model does not foresee: it is not lost and the format need not change. The flow does not
    use it.
  - `FORMAT_VERSION`: if one day the structure changes in an incompatible way it is
    incremented; adding optional fields is NOT an incompatible change.

KNOWN DATA IN THE FORMATS WE HAVE (to understand what the fields are for)
  GENCAD (.cad, UX.cad): component with side, rotation, shape, mounting type (INSERT SMD),
    description (PART, only for a part); pins = signal nodes, with the coordinates in the shapes
    (mil). Outline ($BOARD) and pads ($PADS) present but empty/not useful in UX.cad.
  FZ (.fz): two blocks. Content: component table (insertion code, symbol name, mirrored,
    rotation), pin table (net, number, NAME, x, y, test point, radius), test point/via table.
    Description = bill of materials: PARTNUMBER, DESCRIPTION, QTY, LOCATION (refdes), PARTNUMBER2.
    (The .fz is NOT read yet: the fields exist, empty for the .cad.)

DERIVED VIEWS (for the existing flow)
  comp_to_pins, pin_to_net, net_to_pins and stats() have the same shape as the `Netlist` of
  netlist_solver, so the current code can use this object in its place. They are computed on
  first request and stay cached: the reader must have FINISHED building the object before anyone
  asks for them (otherwise `invalidate_views()`).
"""

from dataclasses import dataclass, field
from functools import cached_property
from typing import Optional

FORMAT_VERSION = 1
UNIT = "mm"

KIND_COMPONENT = "component"
KIND_TEST_POINT = "test_point"
KIND_VIA = "via"


class BoardviewError(Exception):
    """Error while reading a boardview (missing file, unsupported format, invalid content).
    The message is meant to be shown as it is to the technician."""


@dataclass
class Pin:
    number: str                              # mandatory; may be alphanumeric (A1, B3...)
    net: Optional[str] = None                # None = not connected or not known to the format
    name: Optional[str] = None               # functional name of the pin (e.g. VCC), if the format gives it
    test_point: Optional[bool] = None
    # --- placement (mm, board system) ---
    x: Optional[float] = None
    y: Optional[float] = None
    side: Optional[str] = None               # "TOP" / "BOTTOM"
    pad_shape: Optional[str] = None          # e.g. "ROUND", "RECT"
    radius: Optional[float] = None           # pad radius, mm
    extra: dict = field(default_factory=dict)


@dataclass
class Component:
    refdes: str                              # mandatory
    kind: str = KIND_COMPONENT
    # --- component data (what matters immediately to the project) ---
    description: Optional[str] = None        # raw text, as it is in the file
    part_number: Optional[str] = None
    part_number_2: Optional[str] = None
    manufacturer: Optional[str] = None
    manufacturer_code: Optional[str] = None
    value: Optional[str] = None
    package: Optional[str] = None
    symbol_name: Optional[str] = None       # name of the shape/symbol in the source file
    mounting: Optional[str] = None          # "SMD", "TH"...
    # --- placement (mm): what the FORMAT declares for the component ---
    side: Optional[str] = None               # "TOP" / "BOTTOM"
    x: Optional[float] = None                # declared placement point (GENCAD: PLACE)
    y: Optional[float] = None
    rotation: Optional[float] = None        # degrees
    mirrored: Optional[bool] = None
    # ---
    pin: list = field(default_factory=list)  # list[Pin], in the order the file presents them
    extra: dict = field(default_factory=dict)

    def center(self) -> Optional[tuple]:
        """The centre of the component in mm, or None if it cannot be computed. It is NOT a datum
        of the file but a derived view: the centre of the rectangle that contains the pins with
        coordinates. It is needed because the declared placement point may not be the real
        position (in UX.cad PLACE is 0 0 for all, the real coordinates are already in the pins)."""
        points = [(p.x, p.y) for p in self.pin if p.x is not None and p.y is not None]
        if not points:
            return None
        xs, ys = [q[0] for q in points], [q[1] for q in points]
        return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


@dataclass
class BoardviewData:
    format_name: str                             # name of the source format, e.g. "GENCAD"
    file: str                                # name of the file read
    format_version: int = FORMAT_VERSION
    # file header and reading information. Keys used by the readers:
    #   unit ("mm", always), original_unit (text as in the file), warnings (list of texts),
    #   plus the header's own fields (user, revision...).
    meta: dict = field(default_factory=dict)
    components: dict = field(default_factory=dict)  # refdes -> Component, in file order
    # Board outline: list of polygons, each a list of (x, y) points in mm. Empty if the format
    # does not give it or the reader cannot interpret it yet (in that case the original text is
    # in `extra`).
    outline: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    # Order in which the pins were ADDED (= file order). Needed by net_to_pins: the members of a
    # net must come out in file order (like with the Netlist of netlist_solver), because the
    # menu shows them in that order. For this reason the readers add pins ONLY with
    # add_pin(), never with component.pin.append(...).
    _pins_in_order: list = field(default_factory=list, repr=False)

    # --- construction (for the readers) ------------------------------------------------

    def component(self, refdes: str) -> Component:
        """The component `refdes`, created empty if it is not there yet."""
        if refdes not in self.components:
            self.components[refdes] = Component(refdes=refdes)
        return self.components[refdes]

    def add_pin(self, refdes: str, number: str, net: Optional[str] = None, **fields) -> Pin:
        p = Pin(number=number, net=net, **fields)
        self.component(refdes).pin.append(p)
        self._pins_in_order.append((refdes, p))
        return p

    def add_warning(self, text: str):
        """Records a reading warning (something was not interpreted or was omitted)."""
        self.meta.setdefault("warnings", []).append(text)

    def invalidate_views(self):
        for name in ("comp_to_pins", "pin_to_net", "net_to_pins"):
            self.__dict__.pop(name, None)

    # --- derived views, same shape as the Netlist of netlist_solver -------------------

    @cached_property
    def comp_to_pins(self) -> dict:
        """refdes -> [(pin_number, net)], ALL the pins of the component (net None if not connected)."""
        return {r: [(p.number, p.net) for p in c.pin] for r, c in self.components.items() if c.pin}

    @cached_property
    def pin_to_net(self) -> dict:
        """(refdes, pin_number) -> net, only for the connected pins."""
        return {(r, p.number): p.net
                for r, c in self.components.items() for p in c.pin if p.net is not None}

    @cached_property
    def net_to_pins(self) -> dict:
        """net -> [(refdes, pin_number)], in the order in which the pins appear in the file."""
        out: dict = {}
        for r, p in self._pins_in_order:
            if p.net is not None:
                out.setdefault(p.net, []).append((r, p.number))
        return out

    def stats(self) -> str:
        n_pins = sum(len(c.pin) for c in self.components.values())
        return (f"{len(self.comp_to_pins)} referenced components, {n_pins} pins, "
                f"{len(self.net_to_pins)} unique nets")
