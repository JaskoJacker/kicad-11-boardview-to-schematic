#!/usr/bin/env python3
"""
connection_help.py — MODULE (not launched on its own)

Turns the errors kipy raises when the program cannot talk to KiCad into a message that says what
the technician has to do, instead of a Python traceback.

The three situations seen in practice:
  - KiCad is not running, or its API server is disabled   -> "Connection refused" / ConnectionError
  - KiCad runs but only the project manager is open       -> "no handler available for request of
                                                             type ...GetOpenDocuments"
  - KiCad is busy with a dialog or an interactive command -> "KiCad is busy"
"""

from kipy.errors import ApiError
from kipy.errors import ConnectionError as KiCadConnectionError


def explain(error: Exception) -> str | None:
    """A human-readable explanation for a connection problem, or None for any other error."""
    text = str(error)
    lowered = text.lower()
    if isinstance(error, KiCadConnectionError) or "connection refused" in lowered:
        return (
            "[!] Cannot reach KiCad.\n"
            "    - Is KiCad 11 running?\n"
            "    - Is the API server enabled? Preferences > Plugins > enable the API server\n"
            "      (restart KiCad if you have just enabled it).\n"
            "    Then run this program again.")
    if isinstance(error, ApiError) and "no handler available" in lowered:
        return (
            "[!] KiCad answers, but no schematic editor is open.\n"
            "    In the KiCad project manager open the schematic of your project (double-click the\n"
            "    .kicad_sch file, or the schematic editor icon) and keep that window open.\n"
            "    Then run this program again.\n"
            f"    (KiCad's answer: {text})")
    if isinstance(error, ApiError) and "busy" in lowered:
        return (
            "[!] KiCad is busy (a dialog or an interactive command is open).\n"
            "    Close it, then run this program again.")
    return None
