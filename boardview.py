#!/usr/bin/env python3
"""
boardview.py — MODULE (not launched on its own)

A module = one file of functions with ONE purpose. The purpose of this one is to READ A BOARDVIEW:

    data = read_boardview("path/UX.cad")      # -> BoardviewData (unified format)

It receives the path of a boardview file, picks from the extension the function that knows how to
read that format, and returns the data in the unified format (see boardview_data.py). The rest of
the program does not know which formats exist.

TO ADD A FORMAT write ONE FUNCTION in this file, "file -> BoardviewData", and mark it with the
decorator `@reader(".extension")`: nothing else to touch (no list to update, no new file). A
reading function takes care of EVERYTHING its format requires: for the .fz, for example,
decryption and reading of the two blocks (content and bill of materials).
The helper functions shared by several formats (units, numbers...) are at the end of the
"helpers" section; those of a single format carry its name.

RULES FOR EVERY READING FUNCTION (the same as the unified format, boardview_data.py)
  - it fills what its format contains and leaves None for the rest: NEVER invent a datum;
  - lengths in millimetres, with the original unit recorded in meta["original_unit"];
  - if it is not sure about a transformation, it leaves the coordinates empty and says so in
    meta["warnings"];
  - it adds pins ONLY with BoardviewData.add_pin (the file order must be preserved);
  - an unreadable file, or one that is not in its format -> BoardviewError with a message for the
    technician.

`supported_extensions()` is also meant for whoever has to LOOK FOR the boardview of a project
(<name>.<extension> for every registered extension): a new format is found automatically.
"""

from pathlib import Path
from typing import Optional

from boardview_data import UNIT, BoardviewData, BoardviewError

# extension (lowercase, with the dot) -> reading function. Fills itself via @reader.
FORMATS: dict = {}


def reader(*estensioni):
    """Decorator: registers the function as the reader of the given extensions."""
    def registra(func):
        for e in estensioni:
            FORMATS[e.lower()] = func
        return func
    return registra


def supported_extensions() -> list:
    return sorted(FORMATS)


def read_boardview(path) -> BoardviewData:
    path = Path(path)
    if not path.exists():
        raise BoardviewError(f"The file '{path}' does not exist.")
    func = FORMATS.get(path.suffix.lower())
    if func is None:
        raise BoardviewError(
            f"Format '{path.suffix}' is not supported. Supported formats: "
            f"{', '.join(supported_extensions())}."
        )
    return func(str(path))


# ======================================================================================
# HELPERS (shared by several formats)
# ======================================================================================

MM_PER_INCH = 25.4


def _text_after_first_word(line: str) -> Optional[str]:
    """The text after the first word of the line ('PART  ' -> None, 'PART abc d' -> 'abc d')."""
    pieces = line.split(None, 1)
    return pieces[1].strip() if len(pieces) > 1 and pieces[1].strip() else None


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def _mm(value: Optional[float], factor: Optional[float]) -> Optional[float]:
    """A value in the file's unit -> millimetres (None if the value or the factor is missing)."""
    if value is None or factor is None:
        return None
    return round(value * factor, 6)


# ======================================================================================
# GENCAD FORMAT (.cad, for example UX.cad)
# ======================================================================================
#
# It originates from the BOARDVIEW parser (netlist_solver.parse_gencad_signals, validated on
# UX.cad), whose semantics on the signals it takes over EXACTLY: lines `SIGNAL name` (the first
# token is taken) and `NODE refdes pin` (only with exactly 3 tokens), pins and nets in file order.
# The project no longer depends on that folder (decision of the technician, 2026-10-11).
#
# WHAT IT READS (and where it ends up)
#   $HEADER      -> meta: GENCAD version, user, drawing, revision, attributes; UNITS and ORIGIN
#   $SHAPES      -> for each shape its pins (`PIN name padstack x y side rotation mirror`) and the
#                   mounting type (`INSERT SMD`)
#   $COMPONENTS  -> for each component: PLACE, side (LAYER), rotation (ROTATION), shape, device
#   $DEVICES     -> description (PART, if it contains real text) and VALUE of the component's device
#   $SIGNALS     -> nets and pins (NODE)
#   $BOARD, $PADS, $PADSTACKS -> the original text goes into `extra` (in UX.cad they are empty or
#                   useless: a single pad, and the pins cite a padstack "XXX" that is not defined
#                   anywhere). The outline is NOT interpreted (no sample file contains one).
#
# COORDINATES (spatial data: they must be kept). The file's units (`UNITS USER n` = n units per
# inch, in UX.cad 1000 = mil) are converted to millimetres. The absolute position of a pin is the
# component's PLACE + the pin's coordinates in the shape. It is applied ONLY when the
# transformation is certain: rotation 0 and no mirror/flip (true for all 3650 components of
# UX.cad). Otherwise the pin's x and y stay None and meta["warnings"] says so. The recognised units
# are USER n, INCH, THOU and MM; others -> no coordinates + warning.
#
# PINS WITHOUT A NET: the pins that the shape lists and no signal uses are added with net None (in
# UX.cad there are none: 10986 pins in the shapes, 10986 in the signals).
#
# Notes on the sample file: the device has the same name as the refdes for all 3650 components,
# and VALUE coincides with the refdes: a VALUE equal to the refdes is not a value (it is ignored).

def _gencad_mm_per_unit(words: list) -> Optional[float]:
    """Millimetres per file unit, from the `UNITS ...` line (words already split), or None if
    the unit is not one that can be converted with certainty."""
    if len(words) < 2:
        return None
    kind = words[1].upper()
    if kind == "USER" and len(words) > 2 and _to_float(words[2]):
        return MM_PER_INCH / float(words[2])     # USER n = n units per inch
    if kind == "INCH":
        return MM_PER_INCH
    if kind == "THOU":
        return MM_PER_INCH / 1000
    if kind == "MM":
        return 1.0
    return None


@reader(".cad")
def read_gencad(path: str) -> BoardviewData:
    file_name = Path(path).name
    data = BoardviewData(format_name="GENCAD", file=file_name)
    data.meta["unit"] = UNIT

    section = None
    raw_lines = {"$BOARD": [], "$PADS": [], "$PADSTACKS": []}
    mm_factor: Optional[float] = None
    shapes: dict = {}                 # shape name -> [dict(name, padstack, x, y, side, rot, mirror)]
    current_shape = None
    mounting_per_shape: dict = {}   # shape name -> "SMD"/...
    current_comp = None
    comp_info: dict = {}             # refdes -> {side, rotation, shape, device, place, mirror, flip}
    current_device = None
    device_info: dict = {}           # device -> {part, value}
    signal = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("$"):
                    section = None if line.startswith("$END") else line
                    continue
                if section is None:
                    continue

                if section in raw_lines:
                    raw_lines[section].append(line)
                    continue

                words = line.split()
                key = words[0]

                if section == "$HEADER":
                    if key == "GENCAD":
                        data.meta["gencad_version"] = _text_after_first_word(line)
                    elif key in ("USER", "DRAWING", "REVISION"):
                        text = _text_after_first_word(line)
                        data.meta[key.lower()] = text.strip('"') if text else None
                    elif key == "UNITS":
                        data.meta["original_unit"] = _text_after_first_word(line)
                        mm_factor = _gencad_mm_per_unit(words)
                        if mm_factor is None:
                            data.add_warning(f"Units '{data.meta['original_unit']}' not recognised: "
                                        f"no coordinates read.")
                    elif key == "ORIGIN":
                        data.meta["origin"] = _text_after_first_word(line)
                    elif key == "ATTRIBUTE":
                        data.meta.setdefault("attributes", []).append(_text_after_first_word(line))

                elif section == "$SHAPES":
                    if key == "SHAPE" and len(words) > 1:
                        current_shape = words[1]
                        shapes.setdefault(current_shape, [])
                    elif key == "PIN" and current_shape and len(words) >= 6:
                        shapes[current_shape].append({
                            "name": words[1], "padstack": words[2],
                            "x": _to_float(words[3]), "y": _to_float(words[4]), "side": words[5],
                            "rot": words[6] if len(words) > 6 else "0",
                            "mirror": words[7] if len(words) > 7 else "0",
                        })
                    elif key == "INSERT" and current_shape and len(words) > 1:
                        mounting_per_shape[current_shape] = words[1]

                elif section == "$COMPONENTS":
                    if key == "COMPONENT" and len(words) > 1:
                        current_comp = words[1]
                        comp_info[current_comp] = {}
                    elif current_comp is not None:
                        info = comp_info[current_comp]
                        if key == "PLACE" and len(words) >= 3:
                            info["place"] = (_to_float(words[1]), _to_float(words[2]))
                        elif key == "LAYER" and len(words) > 1:
                            info["side"] = words[1]
                        elif key == "ROTATION" and len(words) > 1:
                            info["rotation"] = _to_float(words[1])
                        elif key == "SHAPE" and len(words) > 1:
                            info["shape"] = words[1]
                            info["mirror"] = words[2] if len(words) > 2 else "0"
                            info["flip"] = words[3] if len(words) > 3 else "0"
                        elif key == "DEVICE" and len(words) > 1:
                            info["device"] = words[1]

                elif section == "$DEVICES":
                    if key == "DEVICE" and len(words) > 1:
                        current_device = words[1]
                        device_info[current_device] = {}
                    elif current_device is not None:
                        if key == "PART":
                            device_info[current_device]["part"] = _text_after_first_word(line)
                        elif key == "VALUE":
                            device_info[current_device]["value"] = _text_after_first_word(line)

                elif section == "$SIGNALS":
                    if key == "SIGNAL" and len(words) > 1:
                        signal = words[1]
                    elif key == "NODE":
                        if signal is None or len(words) != 3:
                            continue
                        _, refdes, pin_number = words
                        data.add_pin(refdes, pin_number, net=signal)
    except OSError as e:
        raise BoardviewError(f"Cannot read the file '{path}': {e}") from e

    for section_name, content in raw_lines.items():
        if content:
            data.extra["gencad" + section_name.lower()] = content
    if raw_lines["$BOARD"]:
        data.add_warning("The file contains an outline ($BOARD) that is not interpreted: "
                    "the original text is in extra['gencad$board'].")

    # The component data are applied at the end: the order of the sections in the file does not matter.
    untransformable = []
    for refdes, info in comp_info.items():
        comp = data.component(refdes)
        comp.side = info.get("side")
        comp.rotation = info.get("rotation")
        comp.symbol_name = info.get("shape")
        comp.mounting = mounting_per_shape.get(info.get("shape"))
        place = info.get("place", (None, None))
        comp.x, comp.y = _mm(place[0], mm_factor), _mm(place[1], mm_factor)
        dev = device_info.get(info.get("device"), {})
        comp.description = dev.get("part")
        value = dev.get("value")
        comp.value = value if value and value != refdes else None

        shape_pins = shapes.get(info.get("shape"), [])
        if not shape_pins:
            continue
        certain_transform = (
            mm_factor is not None and place[0] is not None and place[1] is not None
            and info.get("rotation") in (None, 0.0)
            and info.get("mirror", "0") == "0" and info.get("flip", "0") == "0"
        )
        if not certain_transform:
            untransformable.append(refdes)

        present = {}
        for p in comp.pin:
            present.setdefault(p.number, p)
        for pf in shape_pins:
            p = present.get(pf["name"])
            if p is None:   # pin of the shape that no signal uses
                p = data.add_pin(refdes, pf["name"], net=None)
                present[pf["name"]] = p
            if certain_transform and pf["x"] is not None and pf["y"] is not None:
                p.x = _mm(place[0] + pf["x"], mm_factor)
                p.y = _mm(place[1] + pf["y"], mm_factor)
            p.side = pf["side"]
            p.extra["padstack"] = pf["padstack"]
            if pf["rot"] != "0":
                p.extra["pad_rotation"] = pf["rot"]
            if pf["mirror"] != "0":
                p.extra["pad_mirror"] = pf["mirror"]

    if untransformable:
        data.add_warning(f"{len(untransformable)} components have a non-zero rotation or mirror/flip "
                    f"(e.g. {untransformable[0]}): coordinates of their pins not read.")

    if not data.components:
        raise BoardviewError(f"'{file_name}' contains no components: is it really a GENCAD file?")
    return data
