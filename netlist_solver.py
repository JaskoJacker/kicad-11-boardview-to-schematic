#!/usr/bin/env python3
"""
netlist_solver.py — electrical solve on a GENCAD netlist

Engine of the net traversal rules: solve(), parse_gencad_signals() and is_huge_net(). The
interactive session only uses the parser and the "excluded net" test; the solver itself (BFS hop by
hop) is kept because it is the reference for the traversal rules below.

RULES (engine behaviour)
  * BFS queue, NET-by-NET resolution.
  * Component with MORE than 10 pads/pins in total in the netlist -> NEVER traversed (its pads
    stay terminals on the NET).
  * Component with 10 pads/pins or fewer -> when touched by a NET being resolved, it opens ALL
    its other pads not yet visited.
  * Excluded NET (never resolved/expanded): name exactly "GND", or any NET with MORE than 50
    physical pads/pins.

FILE FORMAT — GENCAD 1.4, $SIGNALS section.

STANDALONE USE
  This script asks for the starting component/pad, resolves, shows the log on screen AND saves the
  WHOLE result (all hops, never filtered) in a JSON file, with the METADATA of the resolution under
  the "meta" key.

USAGE
    python netlist_solver.py --self-test
    python netlist_solver.py                  (interactive mode: asks for everything)
"""

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set


DEFAULT_NETLIST = "UX.cad"
MAX_BRIDGE_PADS = 10
HUGE_NET_THRESHOLD = 50


# ---------------------------------------------------------------------------
# Data model — physical NET<->PIN graph, never modified by solve()
# (UNCHANGED from the original)
# ---------------------------------------------------------------------------

@dataclass
class Pin:
    net_name: str
    refdes: str
    pin_number: str


class Netlist:
    def __init__(self):
        self.pins: List[Pin] = []
        self.net_to_pins: Dict[str, List[Tuple[str, str]]] = {}
        self.comp_to_pins: Dict[str, List[Tuple[str, str]]] = {}
        self.pin_to_net: Dict[Tuple[str, str], str] = {}

    def add_pin(self, pin: Pin):
        self.pins.append(pin)
        self.net_to_pins.setdefault(pin.net_name, []).append((pin.refdes, pin.pin_number))
        self.comp_to_pins.setdefault(pin.refdes, []).append((pin.pin_number, pin.net_name))
        self.pin_to_net[(pin.refdes, pin.pin_number)] = pin.net_name

    def stats(self) -> str:
        return (f"{len(self.comp_to_pins)} referenced components, {len(self.pins)} pins, "
                f"{len(self.net_to_pins)} unique nets")


# ---------------------------------------------------------------------------
# Parser — VALIDATED on UX.cad (GENCAD 1.4, $SIGNALS), unchanged from the previous versions
# ---------------------------------------------------------------------------

def parse_gencad_signals(path: str) -> Netlist:
    nl = Netlist()
    current_net: Optional[str] = None
    in_signals = False

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if line == "$SIGNALS":
                in_signals = True
                continue
            if line == "$ENDSIGNALS":
                break
            if not in_signals:
                continue

            parts = line.split()
            if parts[0] == "SIGNAL":
                current_net = parts[1]
            elif parts[0] == "NODE":
                if current_net is None or len(parts) != 3:
                    continue
                _, refdes, pin_number = parts
                nl.add_pin(Pin(net_name=current_net, refdes=refdes, pin_number=pin_number))

    return nl


# ---------------------------------------------------------------------------
# Excluded NET: name "GND", or fan-out above the threshold (default 50)
# (UNCHANGED from the original)
# ---------------------------------------------------------------------------

def is_huge_net(netlist: Netlist, net_name: str, threshold: int = 50) -> bool:
    if net_name == "GND":
        return True
    return len(netlist.net_to_pins.get(net_name, [])) > threshold


# ---------------------------------------------------------------------------
# Solve engine — BFS queue, traversal based on the pad count (<=10)
# (UNCHANGED from the original — not a single line touched)
# ---------------------------------------------------------------------------

def solve(netlist: Netlist, start_refdes: str, start_pad: str,
          max_bridge_pads: int = 10, huge_net_threshold: int = 50) -> dict:
    """
    Solves the circuit from (start_refdes, start_pad) with a FIFO queue
    (breadth-first): one NET at a time, then the pads determined by the NET
    itself are grouped by component.

    For each component found on a NET:
      - if it has MORE than max_bridge_pads pads in total in the netlist -> it is
        NEVER traversed: its pads on this NET stay members, no
        further exploration from this component.
      - if it has max_bridge_pads or fewer -> ALL its pads not yet
        visited are opened (excluding those on excluded NETs), queued for the
        next hop.

    An excluded NET (is_huge_net) is declared as a terminal
    ("huge_net_stops") but NEVER resolved nor queued.

    Returns:
      {
        "start": "UT26.A1",
        "resolved_nets": [
           {
             "hop": int,
             "net": net_name,
             "members": [...],            # complete physical fact, not filtered
             "reached_via": edge | None,   # component_traversal that discovered this net
             "components": [
                {
                  "component": refdes,
                  "pads_on_this_net": [...],       # labels, all explicit
                  "total_pads_in_netlist": int,
                  "bridgeable": bool,               # <= max_bridge_pads
                  "further_pads": [
                      {"pin":..., "net":..., "status": "queued"|"global_stop"}
                  ]
                }, ...
             ]
           }, ...
        ],
        "huge_net_stops": [ {"from": edge_or_None, "net": net_name}, ... ]
      }
    netlist is never mutated.
    """
    start_key = (start_refdes, start_pad)
    if start_key not in netlist.pin_to_net:
        raise KeyError(f"Pin not found in netlist: {start_refdes}.{start_pad}")

    def _is_huge(net_name: str) -> bool:
        return is_huge_net(netlist, net_name, huge_net_threshold)

    resolved_nets: List[dict] = []
    huge_net_stops: List[dict] = []
    visited_nets: Set[str] = set()
    visited_pins: Set[Tuple[str, str]] = {start_key}

    queue = deque()
    queue.append((start_key, 0, None))  # (pin, hop, reached_via)

    while queue:
        pin_key, hop, reached_via = queue.popleft()
        net_name = netlist.pin_to_net.get(pin_key)
        if net_name is None:
            continue

        if net_name in visited_nets:
            continue  # already resolved by arriving from another path

        if _is_huge(net_name):
            huge_net_stops.append({"from": reached_via, "net": net_name})
            continue  # NEVER resolved/expanded: no further hop from here

        visited_nets.add(net_name)
        members_raw = netlist.net_to_pins.get(net_name, [])
        members = [{"refdes": r, "pin": p} for (r, p) in members_raw]

        # group the pads of this NET by component: several pads of the same
        # component on the same net are a single electrical point, but the
        # labels all stay explicit (no loss of information)
        by_component: Dict[str, List[str]] = {}
        for (r, p) in members_raw:
            by_component.setdefault(r, []).append(p)
            visited_pins.add((r, p))

        components_out: List[dict] = []
        for refdes, pads_on_net in by_component.items():
            all_pads = netlist.comp_to_pins.get(refdes, [])
            total_pads = len(all_pads)
            bridgeable = total_pads <= max_bridge_pads

            further: List[dict] = []
            if bridgeable:
                for (other_pin, other_net) in all_pads:
                    other_key = (refdes, other_pin)
                    if other_key in visited_pins:
                        continue
                    visited_pins.add(other_key)

                    edge = {
                        "edge_type": "component_traversal",
                        "component": refdes,
                        "entry_pads": pads_on_net,
                        "exit_pin": other_pin,
                    }

                    if _is_huge(other_net):
                        huge_net_stops.append({"from": edge, "net": other_net})
                        further.append({"pin": other_pin, "net": other_net, "status": "global_stop"})
                    else:
                        queue.append((other_key, hop + 1, edge))
                        further.append({"pin": other_pin, "net": other_net, "status": "queued"})

            components_out.append({
                "component": refdes,
                "pads_on_this_net": pads_on_net,
                "total_pads_in_netlist": total_pads,
                "bridgeable": bridgeable,
                "further_pads": further,
            })

        resolved_nets.append({
            "hop": hop,
            "net": net_name,
            "members": members,
            "reached_via": reached_via,
            "components": components_out,
        })

    return {
        "start": f"{start_refdes}.{start_pad}",
        "resolved_nets": resolved_nets,
        "huge_net_stops": huge_net_stops,
    }


# ---------------------------------------------------------------------------
# Readable output — sequential log in resolution order (hop by hop)
# (UNCHANGED from the original)
# ---------------------------------------------------------------------------

def render_log(result: dict) -> str:
    lines = [f"START: {result['start']}", ""]

    def members_str(members: List[dict]) -> str:
        return ", ".join(f"{m['refdes']}.{m['pin']}" for m in members)

    def edge_str(edge: Optional[dict]) -> str:
        if edge is None:
            return ""
        entry = "+".join(f".{p}" for p in edge["entry_pads"])
        return (f"  (reached via {edge['component']}{entry} "
                f"-> {edge['component']}.{edge['exit_pin']})")

    for rec in result["resolved_nets"]:
        lines.append(f"[hop {rec['hop']}] NET: {rec['net']}{edge_str(rec['reached_via'])}")
        lines.append(f"          physical members: {members_str(rec['members'])}")
        for comp in rec["components"]:
            pads = ", ".join(comp["pads_on_this_net"])
            tag = ("traversable" if comp["bridgeable"]
                   else f"NOT traversable ({comp['total_pads_in_netlist']} total pads > threshold)")
            lines.append(f"          - {comp['component']} (pads: {pads})  "
                         f"[{comp['total_pads_in_netlist']} total pads, {tag}]")
            for fp in comp["further_pads"]:
                if fp["status"] == "global_stop":
                    lines.append(f"              -> pad {fp['pin']} -> {fp['net']}  [GLOBAL/STOP]")
                else:
                    lines.append(f"              -> pad {fp['pin']} -> queued (hop {rec['hop'] + 1})")
        lines.append("")

    lines.append("=== Declared excluded NETs (never resolved) ===")
    if not result["huge_net_stops"]:
        lines.append("  (none)")
    for hs in result["huge_net_stops"]:
        frm = hs["from"]
        if frm is None:
            lines.append(f"  START -> {hs['net']}  [GLOBAL/STOP]")
        else:
            entry = "+".join(f".{p}" for p in frm["entry_pads"])
            lines.append(f"  {frm['component']}{entry} -> {frm['component']}.{frm['exit_pin']} "
                         f"-> {hs['net']}  [GLOBAL/STOP]")

    return "\n".join(lines)


def summary_stats(result: dict) -> str:
    n_nets = len(result["resolved_nets"])
    n_stops = len(result["huge_net_stops"])
    max_hop = max((r["hop"] for r in result["resolved_nets"]), default=0)
    return f"{n_nets} nets resolved (local), {n_stops} stops on excluded NETs, maximum hop reached: {max_hop}"


# ---------------------------------------------------------------------------
# Self-test — UNCHANGED from the original (exactly the same set of assertions)
# ---------------------------------------------------------------------------

def _build_demo_netlist() -> Netlist:
    nl = Netlist()

    demo_pins = [
        ("NETA", "UT26", "A1"),
        ("NETA", "R26", "1"),
        ("NETA", "IC50", "C10"),
        ("NETA", "R31", "1"),
        ("NETA", "R32", "1"),

        ("NETX", "R26", "2"),
        ("NETX", "D1", "3"),

        ("NET_IN", "D1", "1"),
        ("NET_IN", "D1", "2"),
        ("NET_IN", "R30", "1"),

        ("NET_OUT", "R30", "2"),

        ("GND", "R31", "2"),
    ]
    for net_name, refdes, pin in demo_pins:
        nl.add_pin(Pin(net_name=net_name, refdes=refdes, pin_number=pin))

    for i in range(2, 51):
        nl.add_pin(Pin(net_name=f"UT26_DUMMY_{i}", refdes="UT26", pin_number=f"D{i}"))

    for i in range(1, 50):
        nl.add_pin(Pin(net_name=f"IC50_DUMMY_{i}", refdes="IC50", pin_number=f"D{i}"))

    for i in range(1, 52):
        nl.add_pin(Pin(net_name="NET_BIG", refdes=f"DUMMY{i}", pin_number="1"))
    nl.add_pin(Pin(net_name="NET_BIG", refdes="R32", pin_number="2"))

    return nl


def _self_test():
    nl = _build_demo_netlist()
    result = solve(nl, "UT26", "A1")
    print(render_log(result))
    print()
    print(summary_stats(result))

    nets = {r["net"]: r for r in result["resolved_nets"]}

    assert nets["NETA"]["hop"] == 0

    ic50 = next(c for c in nets["NETA"]["components"] if c["component"] == "IC50")
    assert ic50["bridgeable"] is False
    assert ic50["further_pads"] == []

    r26 = next(c for c in nets["NETA"]["components"] if c["component"] == "R26")
    assert r26["bridgeable"] is True
    assert any(fp["net"] == "NETX" for fp in r26["further_pads"])

    assert "NETX" in nets
    d1_on_netx = next(c for c in nets["NETX"]["components"] if c["component"] == "D1")
    assert d1_on_netx["pads_on_this_net"] == ["3"]
    assert d1_on_netx["bridgeable"] is True
    assert {fp["pin"] for fp in d1_on_netx["further_pads"]} == {"1", "2"}

    assert "NET_IN" in nets
    d1_on_netin = next(c for c in nets["NET_IN"]["components"] if c["component"] == "D1")
    assert set(d1_on_netin["pads_on_this_net"]) == {"1", "2"}

    assert "NET_OUT" in nets

    assert "GND" not in nets
    assert any(hs["net"] == "GND" for hs in result["huge_net_stops"])

    assert "NET_BIG" not in nets
    assert any(hs["net"] == "NET_BIG" for hs in result["huge_net_stops"])

    print("\n[self-test OK] all rules verified: 10-pad threshold on the component, "
          "multiple pads of the same component on one net, GND by name, "
          "NET_BIG (51 pads) excluded only by fan-out.")


# ---------------------------------------------------------------------------
# Interactive mode — asks on screen for the starting component and pad
# ---------------------------------------------------------------------------

def _ask(msg: str, default: Optional[str] = None) -> str:
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


def run_interactive():
    print("=== netlist_solver — interactive mode ===")
    netlist_path = _ask("Netlist file (GENCAD .cad)", DEFAULT_NETLIST)

    nl = parse_gencad_signals(netlist_path)
    print(f"[+] Netlist loaded: {nl.stats()}")

    refdes = _ask_required("Starting component (REFDES, e.g. R4724)")
    pad = _ask_required("Starting pad/pin (e.g. 1)")

    try:
        result = solve(nl, refdes, pad,
                        max_bridge_pads=MAX_BRIDGE_PADS,
                        huge_net_threshold=HUGE_NET_THRESHOLD)
    except KeyError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print()
    print(render_log(result))
    print()
    print(f"[+] {summary_stats(result)}")

    default_json = f"result_{refdes}_{pad}.json"
    json_name = _ask("Output JSON file name", default_json)

    container = {
        "meta": {
            "netlist": netlist_path,
            "refdes": refdes,
            "pad": pad,
            "max_bridge_pads": MAX_BRIDGE_PADS,
            "huge_net_threshold": HUGE_NET_THRESHOLD,
        },
        "result": result,
    }
    with open(json_name, "w", encoding="utf-8") as f:
        json.dump(container, f, indent=2, ensure_ascii=False)
    print(f"[+] Data saved in {json_name!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Electrical solve on a GENCAD netlist")
    ap.add_argument("--self-test", action="store_true",
                     help="Runs only the synthetic self-test (no input required)")
    args = ap.parse_args()

    if args.self_test:
        _self_test()
        return

    run_interactive()


if __name__ == "__main__":
    main()
