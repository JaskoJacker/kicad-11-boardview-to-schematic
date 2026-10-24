#!/usr/bin/env python3
"""
part_identity.py — MODULE (not launched on its own)

Who is this chip? Turns the free-text description a boardview gives for a component into a part
identifier, and turns identifiers into names that are valid for a KiCad symbol.

The descriptions of the boards seen so far are BOM lines of the form
    <type words and part name> // <BRAND>/<CODE> <more text>
for example
    OVP IC TPD6S300ARUKR//TI WQFN-20(3*3)                       -> TPD6S300ARUKR
    POWER SW. G2898KD1U TDFN2*3-14//HUABEI/HQ11170522000        -> G2898KD1U
    RES 10K OHM 1/20W (0201) 5%//YAGEO/RC0201JR-0710KL          -> RC0201JR-0710KL
    FPC CON 10P 0.5 1H R/A SMT//HUABEI/HQ11920271000            -> HQ11920271000
The format is not uniform, so extraction follows these rules, in this order:
  1. The first token on the LEFT of "//" that looks like a part number: letters and digits,
     at least 5 characters, not a package name (QFN, SOT, BGA...), not a value (10UF, 1.05V,
     5V_0.15PF) and not a size or a dimension (anything with parentheses, "*" or "/<digits>").
     A suffix such as "/TR" is allowed (WNM3013-3/TR).
  2. Otherwise the CODE after "<BRAND>/" on the right of "//" (at least 5 characters).
  3. Otherwise there is no identifier: the caller asks the technician.
Better no identifier than a wrong one: the identifier decides which symbol is used.
"""

import hashlib
import re

# part-number-like token: starts with an alphanumeric, may contain . _ + -, optional "/TR"-style suffix
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*(?:/[A-Za-z]{1,3})?$")
# 10UF, 0.01UF, 1.05V, 5V_0.15PF ...
_VALUE_RE = re.compile(r"^\d+(?:\.\d+)?[A-Za-z]{1,4}(?:_\d+(?:\.\d+)?[A-Za-z]{0,4})*$")
# SOT-23, WQFN32, TDFN2, SO8, SMD-3225, FBGA315 ...
_PACKAGE_RE = re.compile(
    r"^[A-Z]{0,3}(?:QFN|DFN|BGA|SOT|SOIC|SOD|TSOT|WLCSP|DSBGA|LGA|QFP|TSSOP|SSOP|MSOP|DIP|FCCSP|"
    r"PRPAK|WBSOT|SOP|SMD|SO)-?\d", re.IGNORECASE)

_MIN_LENGTH = 5
_SYMBOL_NAME_FORBIDDEN = re.compile(r"[^A-Za-z0-9_.+\-]")


def _looks_like_part_number(token: str) -> bool:
    token = token.strip(",;")
    if len(token) < _MIN_LENGTH or not _TOKEN_RE.match(token):
        return False
    letters = sum(c.isalpha() for c in token)
    digits = sum(c.isdigit() for c in token)
    if letters < 1 or digits < 2:
        return False
    return not (_VALUE_RE.match(token) or _PACKAGE_RE.match(token))


def _code_after_brand(right: str) -> str | None:
    """The CODE of a `BRAND/CODE` pair on the right of "//" (the brand may contain a space)."""
    for token in right.split():
        if "/" in token:
            code = token.split("/", 1)[1].strip(",;")
            return code if _looks_like_part_number(code) else None
    return None


def extract_part_identifier(description: str | None) -> str | None:
    """The part identifier written in a boardview description, or None if it is not clear."""
    if not description or not description.strip():
        return None
    left, _, right = description.partition("//")
    for token in left.split():
        if _looks_like_part_number(token):
            return token.strip(",;")
    return _code_after_brand(right)


# prefixes whose description carries an electrical value worth putting in the Value field
VALUE_PREFIXES = {"R", "C", "L", "FB"}
_CAPACITANCE_OR_INDUCTANCE = re.compile(r"(\d+(?:\.\d+)?)\s*(PF|NF|UF|MF|NH|UH|MH)\b", re.IGNORECASE)
_OHMS = re.compile(r"(\d+(?:\.\d+)?)\s*([KM]?)\s*OHMS?\b", re.IGNORECASE)


def short_value(reference_prefix: str, description: str | None) -> str:
    """The electrical value written in a boardview description of a resistor, capacitor, inductor or
    ferrite bead ("RES 1K OHM 1/16W(0402)1%" -> "1K", "MLCC 0.1UF/10V" -> "0.1UF", "FERRITE BEAD 120OHM"
    -> "120OHM"), or "" when there is none or the prefix is not one of those. Only the text before the
    "//" is read: after it come the maker and its code."""
    if not description or reference_prefix.upper() not in VALUE_PREFIXES:
        return ""
    text = description.partition("//")[0]
    found = _CAPACITANCE_OR_INDUCTANCE.search(text)
    if found:
        return f"{found.group(1)}{found.group(2).upper()}"
    found = _OHMS.search(text)
    if found:
        unit = "OHM" if "FERRITE" in text.upper() else ""
        return f"{found.group(1)}{found.group(2).upper()}{unit}"
    return ""


def clean_symbol_name(identifier: str) -> str:
    """A valid KiCad symbol name for the identifier: every other character becomes '_'
    (a '/' or a ':' would break the library id)."""
    return _SYMBOL_NAME_FORBIDDEN.sub("_", identifier.strip())


def generic_symbol_name(pin_numbers) -> str:
    """Name of the generic symbol used when the part is unknown. It only depends on the pins, so
    every unknown part with the same pins shares one symbol: GENERIC_2P for pins 1,2;
    GENERIC_8P for pins 1..8; otherwise GENERIC_<n>P_<hash of the pin numbers>."""
    numbers = sorted(set(pin_numbers), key=_natural_key)
    count = len(numbers)
    if numbers == [str(i) for i in range(1, count + 1)]:
        return f"GENERIC_{count}P"
    digest = hashlib.sha1(",".join(numbers).encode("utf-8")).hexdigest()[:6]
    return f"GENERIC_{count}P_{digest}"


def _natural_key(number: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", number)]
