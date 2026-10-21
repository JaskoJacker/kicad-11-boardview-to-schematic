#!/usr/bin/env python3
"""
data_location.py — MODULE (not launched on its own)

Tells the program where the boardview data files (.cad, .fz, ...) live. They are kept OUTSIDE
the repository on purpose: their provenance is not validated, so they must never be published
(or even sit in a folder that could be zipped or committed by mistake).

Resolution order:
  1. the environment variable BOARDVIEW_DATA_DIR, if set;
  2. otherwise a folder next to the repository, named after it with a "_data" suffix
     (e.g. C:\\...\\Desktop\\BOARDVIEW_API_11  ->  C:\\...\\Desktop\\BOARDVIEW_API_11_data).

The folder is never created automatically, and it is refused if it would be inside the repository.

Also here: the helpers that turn a typed or pasted file path into a usable path (quotes added by
Windows "Copy as path", padding spaces) and ask again when the file does not exist.
"""

import os
from pathlib import Path

ENV_VAR = "BOARDVIEW_DATA_DIR"
REPO_ROOT = Path(__file__).resolve().parent


def data_dir() -> Path:
    """The folder holding the boardview data files (outside the repository)."""
    configured = os.environ.get(ENV_VAR)
    folder = Path(configured).expanduser() if configured else REPO_ROOT.parent / f"{REPO_ROOT.name}_data"
    folder = folder.resolve()
    if folder == REPO_ROOT or REPO_ROOT in folder.parents:
        raise ValueError(
            f"The boardview data folder '{folder}' is inside the repository. Boardview data must stay "
            f"outside it (set {ENV_VAR} to another folder).")
    return folder


def normalize_path(text: str) -> Path:
    """Turns what a user typed or pasted into a path: surrounding whitespace and one pair of
    quotes are removed (Windows "Copy as path" adds them), then ~ and %VARIABLES% are expanded."""
    cleaned = text.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return Path(os.path.expandvars(os.path.expanduser(cleaned)))


def ask_netlist_path(ask, default: Path) -> str:
    """Asks for the boardview file with `ask(message, default)` until an existing file is given.

    Accepts quoted and padded paths (see normalize_path) and explains the problem instead of
    letting the program crash when the file does not exist."""
    while True:
        answer = ask("Netlist file (GENCAD .cad)", str(default))
        path = normalize_path(answer)
        if path.is_file():
            return str(path)
        print(f"  [!] File not found: '{path}'. Type the full path of an existing boardview file.")
