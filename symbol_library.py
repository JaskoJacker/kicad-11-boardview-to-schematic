#!/usr/bin/env python3
"""
symbol_library.py — MODULE (not launched on its own)

Keeps a REAL symbol library (`boardview_custom.kicad_sym`) in the KiCad project folder, with its
row in the project's library table (`sym-lib-table`). It holds the symbols we build for the
components that have no suitable symbol in KiCad's own libraries; each symbol is named after the
part it represents (e.g. `TPD6S300ARUKR`), see symbol_resolver.py. It is needed because a symbol
placed with a library id that does not exist cannot be updated ("Update symbol from library":
*** symbol not found ***, seen by the technician on 2026-10-03) and cannot be replaced cleanly
with "Change symbol".

WHY A FILE IS WRITTEN INSTEAD OF USING THE API: the kipy API (verified by reading the code,
2026-10-03) can only READ libraries (get_library_symbols, get_library_symbol,
get_library_statuses) and reload an already present row (reload_library); it has no command to
write a symbol into a library nor to add a row to the table.

GEOMETRY: it is not redefined here. We start from `symbol_builder.layout_pins()`, the same
function whose geometry the program assumes when it computes pin positions, so the library symbol
and the symbol on the schematic cannot diverge. Here we only convert from mil with Y downward
(API) to mm with Y upward (.kicad_sym file, as in KiCad 10.99's Device.kicad_sym, format version
20251024).

A symbol already built is never rewritten: add_symbol does nothing if the name is already in the
file. A library shared between projects would require moving the file and the row into a global
table (not done: it touches the user's configuration).
"""

from pathlib import Path

import symbol_builder as geometry
from kipy.schematic_types import SchematicPinOrientation as PO

LIBRARY_NAME = "boardview_custom"
LIBRARY_FILE = LIBRARY_NAME + ".kicad_sym"
LIBRARY_TABLE_FILE = "sym-lib-table"
FORMAT_VERSION = 20251024
MM_PER_MIL = 0.0254
DEFAULT_DESCRIPTION = "Symbol built from the boardview"


def symbol_id(name: str) -> str:
    """The full id in the library, in the format KiCad expects: `boardview_custom:<name>`."""
    return f"{LIBRARY_NAME}:{name}"


def _mm(mil: float) -> str:
    return f"{mil * MM_PER_MIL:.4f}".rstrip("0").rstrip(".") or "0"


def _angle(orientation) -> int:
    return 0 if orientation == PO.SPO_RIGHT else 180


def _escape(text: str) -> str:
    """A text inside a quoted string of the .kicad_sym file."""
    return " ".join(text.split()).replace("\\", "\\\\").replace('"', '\\"')


def _property(name: str, value: str, x: str, y: str, hidden: bool) -> str:
    hide = "\n\t\t\t(hide yes)" if hidden else ""
    return (
        f'\t\t(property "{name}" "{_escape(value)}"\n'
        f"\t\t\t(at {x} {y} 0)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no){hide}\n"
        f"\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n"
        f"\t\t)\n"
    )


def symbol_sexpr(name: str, reference_prefix: str, pin_numbers: list[str],
                 description: str = DEFAULT_DESCRIPTION) -> str:
    """The `(symbol "<name>" ...)` block for the .kicad_sym file."""
    positions, top_edge, bottom_edge = geometry.layout_pins(pin_numbers)
    left = _mm(-geometry.BODY_WIDTH_MIL / 2)
    right = _mm(geometry.BODY_WIDTH_MIL / 2)
    top = _mm(-top_edge)      # Y upward in the file
    bottom = _mm(-bottom_edge)
    above_body = _mm(-top_edge + 50)

    s = f'\t(symbol "{name}"\n'
    s += "\t\t(pin_names\n\t\t\t(offset 0)\n\t\t\t(hide yes)\n\t\t)\n"
    s += "\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
    s += "\t\t(in_pos_files yes)\n\t\t(duplicate_pin_numbers_are_jumpers no)\n"
    s += _property("Reference", reference_prefix, "0", above_body, False)
    s += _property("Value", "", "0", "0", True)     # the name is the part identifier, not a value
    s += _property("Footprint", "", "0", "0", True)
    s += _property("Datasheet", "", "0", "0", True)
    s += _property("Description", description, "0", "0", True)
    s += f'\t\t(symbol "{name}_0_1"\n'
    s += (f"\t\t\t(rectangle\n\t\t\t\t(start {left} {top})\n\t\t\t\t(end {right} {bottom})\n"
          f"\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n\t\t\t\t\t(type default)\n\t\t\t\t)\n"
          f"\t\t\t\t(fill\n\t\t\t\t\t(type none)\n\t\t\t\t)\n\t\t\t)\n")
    s += "\t\t)\n"
    s += f'\t\t(symbol "{name}_1_1"\n'
    for (num, x_mil, y_mil, orient) in positions:
        s += (
            f"\t\t\t(pin passive line\n"
            f"\t\t\t\t(at {_mm(x_mil)} {_mm(-y_mil)} {_angle(orient)})\n"
            f"\t\t\t\t(length {_mm(geometry.PIN_LENGTH_MIL)})\n"
            f'\t\t\t\t(name "{geometry.EMPTY_PIN_NAME}"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n'
            f"\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n"
            f'\t\t\t\t(number "{_escape(num)}"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n'
            f"\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n"
            f"\t\t\t)\n"
        )
    s += "\t\t)\n\t\t(embedded_fonts no)\n\t)\n"
    return s


def _table_row() -> str:
    return (f'\t(lib (name "{LIBRARY_NAME}") (type "KiCad") '
            f'(uri "${{KIPRJMOD}}/{LIBRARY_FILE}") (options "") '
            f'(descr "Symbols built from the boardview"))\n')


def ensure_library(project_folder: Path) -> bool:
    """Creates (if missing) the library file and its row in the project table.
    Returns True if it had to MODIFY THE TABLE: in that case KiCad may not see the
    library until it re-reads the table (reopening the project)."""
    project_folder = Path(project_folder)
    lib_file = project_folder / LIBRARY_FILE
    if not lib_file.exists():
        lib_file.write_text(
            f'(kicad_symbol_lib\n\t(version {FORMAT_VERSION})\n\t(generator "boardview_api11")\n'
            f'\t(generator_version "1")\n)\n', encoding="utf-8")

    table = project_folder / LIBRARY_TABLE_FILE
    if not table.exists():
        table.write_text(f"(sym_lib_table\n\t(version 7)\n{_table_row()})\n", encoding="utf-8")
        return True
    text = table.read_text(encoding="utf-8")
    if f'(name "{LIBRARY_NAME}")' in text:
        return False
    closing = text.rstrip().rfind(")")
    table.write_text(text[:closing].rstrip() + "\n" + _table_row() + ")\n", encoding="utf-8")
    return True


def add_symbol(project_folder: Path, name: str, pin_numbers: list[str], reference_prefix: str = "U",
               description: str = DEFAULT_DESCRIPTION) -> bool:
    """Adds the symbol `name` to the library if it is not there already. True if it added it."""
    lib_file = Path(project_folder) / LIBRARY_FILE
    text = lib_file.read_text(encoding="utf-8")
    if f'(symbol "{name}"' in text:
        return False
    closing = text.rstrip().rfind(")")
    lib_file.write_text(
        text[:closing].rstrip() + "\n" + symbol_sexpr(name, reference_prefix, pin_numbers, description) + ")\n",
        encoding="utf-8")
    return True
