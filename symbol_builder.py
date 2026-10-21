#!/usr/bin/env python3
"""
symbol_builder.py — MODULE (not launched on its own)

The GEOMETRY of the symbols we build for components that have no usable symbol in a KiCad
library: where each pin goes and how big the body is. Pure computation: it does not connect to
KiCad and knows nothing about threads, menus or netlists. symbol_library.py turns this geometry
into the text of a .kicad_sym symbol; keeping it in one place means the symbol in the library and
the pin positions the program computes for the schematic cannot diverge.

GEOMETRY (all on grid: coordinates in mil, multiples of 100 for the pins):
  rectangular body 400 mil wide (x from -200 to +200); the pins sit half on the left
  (connection point at x=-300, length 100) and half on the right (x=+300). The local
  coordinates use Y downward, like the instances in the schematic (verified on pin 1 of
  Device:R, which sits above the centre with negative y).

KNOWN LIMIT: no limit on the number of pins. An IC with hundreds of pins produces a symbol tens
of inches tall (one pin every 100 mil): valid but huge.
"""

import re

from kipy.schematic_types import SchematicPinOrientation as PO

BODY_WIDTH_MIL = 400
PIN_X_MIL = 300         # connection point of the pins (at +/- this x)
PIN_LENGTH_MIL = 100
PIN_PITCH_MIL = 100
EMPTY_PIN_NAME = "~"    # KiCad convention: "~" = pin without a visible name


def _natural_key(number: str):
    """Sorts pin numbers in a 'human' way: 2 before 10, A2 before A10."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", number)]


def reference_prefix(refdes: str) -> str:
    """The letters a reference starts with (U6101 -> U); 'U' if there are none."""
    m = re.match(r"[A-Za-z_]+", refdes)
    return m.group(0) if m else "U"


def layout_pins(pin_numbers: list[str]):
    """Computes where each pin goes. Returns (positions, top_y_mil, bottom_y_mil) where
    `positions` is a list of (number, x_mil, y_mil, orientation) and the two y values are the
    edges of the rectangular body. First half of the pins on the left, the rest on the right."""
    sorted_numbers = sorted(dict.fromkeys(pin_numbers), key=_natural_key)  # without duplicates
    n = len(sorted_numbers)
    n_left = (n + 1) // 2
    left, right = sorted_numbers[:n_left], sorted_numbers[n_left:]
    rows = max(len(left), len(right), 1)

    y_top = -((rows - 1) // 2) * PIN_PITCH_MIL
    positions = []
    for i, num in enumerate(left):
        positions.append((num, -PIN_X_MIL, y_top + i * PIN_PITCH_MIL, PO.SPO_RIGHT))
    for i, num in enumerate(right):
        positions.append((num, PIN_X_MIL, y_top + i * PIN_PITCH_MIL, PO.SPO_LEFT))

    top_edge = y_top - PIN_PITCH_MIL
    bottom_edge = y_top + (rows - 1) * PIN_PITCH_MIL + PIN_PITCH_MIL
    return positions, top_edge, bottom_edge
