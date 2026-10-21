#!/usr/bin/env python3
"""
symbol_resolver.py — MODULE (not launched on its own)

Which symbol do we place for a component? Given the identifier of the part (e.g. TPD6S300ARUKR)
and the pin numbers the boardview gives for the component, it decides, in this order:

  1. KiCad's own libraries: a symbol with the SAME NAME IN FULL (case does not matter) whose pins
     are the same as the boardview's -> use it. The name must match for the whole identifier:
     `TPD6S300A` is NOT enough to say that a part is `TPD6S300ARUKR` (package, pin names...).
  2. Our own library (boardview_custom): the same rule, so a part built once is reused.
  3. Otherwise a new symbol named after the part is built from the boardview pins and written
     into our library. If a symbol with that name already exists but with other pins, the new one
     gets a numbered variant name (<name>_2, <name>_3...) and the technician is told.

"Same pins" means: the same set of pin numbers (the boardview gives no pin names), one unit only
(the pins of a multi-unit symbol that is not placed would break the pin-position computation) and
no pin number used twice.

Everything here talks to KiCad: call it ONLY from inside the KiCadWorker thread.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import symbol_library
from kipy.proto.common.types import LibraryTableScope, LibraryType, base_types_pb2 as bt
from part_identity import clean_symbol_name

LIBRARY_WAIT_SEC = 10          # how long to wait for KiCad to list a symbol just written
NATIVE_CHUNK = 40              # libraries asked per call (tested against KiCad 10.99)
MAX_VARIANTS = 50


@dataclass
class Resolution:
    """The outcome: which library id to place and where it came from."""
    lib_id: str
    origin: str        # "native", "custom (existing)" or "custom (new)"
    note: str = ""     # something the technician should know (a mismatch, a variant name...)


class SymbolResolver:
    def __init__(self, kicad, project_folder):
        self.kicad = kicad
        self.project_folder = Path(project_folder)
        self.document = kicad.get_open_documents(bt.DOCTYPE_SCHEMATIC)[0]
        self._native_names: dict[str, list[str]] | None = None   # lower-case name -> library ids

    # --- reading KiCad's libraries -----------------------------------------------------

    def _native_index(self) -> dict[str, list[str]]:
        """All symbols of the global libraries, by lower-case name. Built once (about 2 s)."""
        if self._native_names is None:
            rows = self.kicad.get_library_statuses(LibraryType.LT_SYMBOL, LibraryTableScope.LTS_GLOBAL)
            nicknames = [r.nickname for r in rows if r.nickname != symbol_library.LIBRARY_NAME]
            index: dict[str, list[str]] = {}
            for i in range(0, len(nicknames), NATIVE_CHUNK):
                for symbol_id in self.kicad.get_library_symbols(nicknames[i:i + NATIVE_CHUNK]):
                    symbol_id = str(symbol_id)
                    index.setdefault(symbol_id.split(":", 1)[1].lower(), []).append(symbol_id)
            self._native_names = index
        return self._native_names

    def _custom_names(self) -> set[str]:
        ids = self.kicad.get_library_symbols([symbol_library.LIBRARY_NAME])
        return {str(i).split(":", 1)[1] for i in ids}

    def _pins_problem(self, lib_id: str, wanted: set[str]) -> str | None:
        """None if the library symbol fits the boardview pins, otherwise why it does not."""
        try:
            symbol = self.kicad.get_library_symbol(lib_id, self.document)
        except Exception as error:  # noqa: BLE001 — a symbol that cannot be read cannot be used
            return f"cannot be read ({error})"
        numbers = [p.number for p in symbol.pins]
        if symbol.unit_count != 1:
            return f"has {symbol.unit_count} units"
        if len(numbers) != len(set(numbers)):
            return "uses a pin number more than once"
        if set(numbers) != wanted:
            missing = sorted(wanted - set(numbers))
            extra = sorted(set(numbers) - wanted)
            return f"has other pins (boardview-only: {missing}, symbol-only: {extra})"
        return None

    def _wait_until_listed(self, name: str):
        """KiCad re-reads the changed file by itself (it appears immediately); if after 1 second it
        is still not there, a reload_library is asked explicitly."""
        reload_done = False
        begin = time.monotonic()
        while time.monotonic() - begin < LIBRARY_WAIT_SEC:
            if name in self._custom_names():
                return
            if not reload_done and time.monotonic() - begin > 1:
                self.kicad.reload_library(LibraryType.LT_SYMBOL, LibraryTableScope.LTS_PROJECT,
                                          [symbol_library.LIBRARY_NAME])
                reload_done = True
            time.sleep(0.5)
        raise RuntimeError(f"KiCad does not list {symbol_library.symbol_id(name)} after {LIBRARY_WAIT_SEC}s: "
                           f"is the library {symbol_library.LIBRARY_NAME} loaded?")

    # --- the decision ----------------------------------------------------------------

    def resolve(self, name: str, pin_numbers: list[str], reference_prefix: str, description: str,
                use_native: bool = True) -> Resolution:
        """The symbol to place for the part `name` (see the module docstring for the rules).
        `use_native=False` skips KiCad's own libraries (used for the generic symbol)."""
        wanted = set(pin_numbers)
        notes = []

        if use_native:
            for lib_id in self._native_index().get(name.lower(), []):
                problem = self._pins_problem(lib_id, wanted)
                if problem is None:
                    return Resolution(lib_id, "native")
                notes.append(f"{lib_id} has the same name but {problem}")

        base = clean_symbol_name(name)
        existing = self._custom_names()
        for variant in range(1, MAX_VARIANTS + 1):
            candidate = base if variant == 1 else f"{base}_{variant}"
            lib_id = symbol_library.symbol_id(candidate)
            if candidate not in existing:
                symbol_library.add_symbol(self.project_folder, candidate, pin_numbers, reference_prefix, description)
                self._wait_until_listed(candidate)
                if variant > 1:
                    notes.append(f"'{base}' already exists in {symbol_library.LIBRARY_NAME} with other pins: "
                                 f"this part is saved as '{candidate}'")
                return Resolution(lib_id, "custom (new)", "; ".join(notes))
            if self._pins_problem(lib_id, wanted) is None:
                if variant > 1:
                    notes.append(f"variant '{candidate}' used: '{base}' exists in {symbol_library.LIBRARY_NAME} "
                                 f"with other pins")
                return Resolution(lib_id, "custom (existing)", "; ".join(notes))
        raise RuntimeError(f"Too many variants of '{base}' in {symbol_library.LIBRARY_NAME}")
