"""End-to-end validation of the RTR animal-themed zone/action restructure.

Loads every adapter data file through its real Python loader (so a structural
problem in any JSON is caught), asserts the 12-zone network / three mode groups /
camera-control coverage, instantiates the StateMachine + Brain, and runs a short
scripted navigation to confirm zone transitions actually move the composite state.

Run from anywhere::

    python validate_all.py

Exits non-zero on the first failure.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
# The rtr package lives at realtimecontrol/rtr/rtr; make it importable.
RTR_DIR = os.path.join(ROOT, "realtimecontrol", "rtr")
sys.path.insert(0, RTR_DIR)

# Import core before brain (mirrors main.py's order) to avoid the brain<->core cycle.
from rtr.core.engine import Engine  # noqa: E402,F401
from rtr.state.zones import (  # noqa: E402
    MODE_GROUPS,
    Zones,
    load_state_data,
)
from rtr.state.machine import StateMachine  # noqa: E402
from rtr.brain.brain import Brain  # noqa: E402
from rtr.brain.config import load_brain_config  # noqa: E402
from rtr.sound.sound import load_sound_data  # noqa: E402
from rtr.patches.patches import load_patches_data, match_patch  # noqa: E402
from rtr.io.midi import load_midi_data  # noqa: E402

# Expected 12-zone network (the plan's new animal-themed zones).
EXPECTED_ZONES = [
    "resting", "sleeping", "cuddling", "purring", "stretching", "hunting",
    "examining", "performing", "roaring", "growling", "clawing", "leaping",
]
# The three mode groups and the zones each should hold.
EXPECTED_GROUPS = {
    "museum-closed": ["resting", "sleeping", "cuddling", "purring"],
    "event-sit-back": ["stretching", "hunting", "examining", "performing"],
    "stage-takeover": ["roaring", "growling", "clawing", "leaping"],
}


def _data_dir() -> str:
    return RTR_DIR


def check(label: str, ok, detail: str = "") -> bool:
    ok = bool(ok)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def main() -> int:
    failures = 0
    dd = _data_dir()

    print("=== Adapter data validation (real loaders) ===")

    # --- zones.json -------------------------------------------------------
    zones_path = os.path.join(dd, "zones.json")
    try:
        zdata = load_state_data(zones_path)
        zones = Zones(zdata["zones"])
        names = zones.names()
        ok = check("zones.json loads", True)
        ok &= check("12 zones present", len(names) == 12, f"got {len(names)}: {names}")
        ok &= check("zone names match plan", sorted(names) == sorted(EXPECTED_ZONES),
                    f"mismatch vs {sorted(EXPECTED_ZONES)}")
        # Mode groups must match the plan exactly.
        groups = zones.mode_groups()
        ok &= check("mode_groups partition", sorted(groups) == sorted(EXPECTED_GROUPS),
                    f"got {groups}")
        for g, zlist in EXPECTED_GROUPS.items():
            ok &= check(f"group {g} zones", sorted(groups.get(g, [])) == sorted(zlist),
                        f"got {groups.get(g)}")
        # Every zone must have a valid mode_group in MODE_GROUPS and at least one exit.
        bad_mg = [n for n in names if zones.get(n).mode_group not in MODE_GROUPS]
        ok &= check("all zones have a valid mode_group", not bad_mg, f"bad: {bad_mg}")
        no_exit = [n for n in names if not zones.get(n).exits()]
        ok &= check("all zones have >=1 exit", not no_exit, f"no exits: {no_exit}")
        # Exits must reference existing zones (graph is closed).
        graph = zones.graph()
        dangling = {z: t for z, ts in graph.items() for t in ts if t not in names}
        ok &= check("exits reference existing zones only", not dangling, f"dangling: {dangling}")
    except (OSError, ValueError) as exc:
        failures += 1
        check("zones.json loads", False, str(exc))
        zdata, zones, names = {}, None, []

    # --- brain.json -------------------------------------------------------
    brain_path = os.path.join(dd, "brain.json")
    try:
        bcfg = load_brain_config(brain_path)
        ok = check("brain.json loads", True)
        zgs = bcfg.get("zone_groups", {})
        ok &= check("3 mode groups in brain", set(zgs) == set(EXPECTED_GROUPS),
                    f"got {set(zgs)}")
        ok &= check("all mode groups enabled",
                    all(bool(zgs.get(g, {}).get("enabled")) for g in EXPECTED_GROUPS),
                    "flags: " + ", ".join(f"{g}: {zgs.get(g, {}).get('enabled')}" for g in EXPECTED_GROUPS))
        cam = bcfg.get("cam_control", {})
        missing_cam = [z for z in EXPECTED_ZONES if z not in cam]
        ok &= check("cam_control covers all 12 zones", not missing_cam, f"missing: {missing_cam}")
    except (OSError, ValueError) as exc:
        failures += 1
        check("brain.json loads", False, str(exc))
        bcfg = {}

    # --- sound.json -------------------------------------------------------
    sound_path = os.path.join(dd, "sound.json")
    try:
        sdata = load_sound_data(sound_path)
        ok = check("sound.json loads", True)
        sz = sdata.get("zones", {})
        ok &= check("sound has 12 zone entries", len(sz) == 12, f"got {len(sz)}: {sorted(sz)}")
    except (OSError, ValueError) as exc:
        failures += 1
        check("sound.json loads", False, str(exc))

    # --- patches.json -----------------------------------------------------
    patches_path = os.path.join(dd, "patches.json")
    try:
        pdata = load_patches_data(patches_path)
        ok = check("patches.json loads", True)
        ok &= check("patches has a default", isinstance(pdata.get("default"), str) and pdata.get("default"))
        pz = pdata.get("zones", {})
        ok &= check("patches zones table present", isinstance(pz, dict) and len(pz) > 0,
                    f"got {len(pz)}")
        # Default fallback resolves.
        code = match_patch(pdata)
        ok &= check("match_patch falls back to default", isinstance(code, str) and bool(code),
                    f"got {code!r}")
    except (OSError, ValueError) as exc:
        failures += 1
        check("patches.json loads", False, str(exc))

    # --- midi.json --------------------------------------------------------
    midi_path = os.path.join(dd, "midi.json")
    try:
        mdata = load_midi_data(midi_path)
        ok = check("midi.json loads", True)
        ok &= check("midi enabled", bool(mdata.get("enabled")), f"got {mdata.get('enabled')}")
        mz = mdata.get("zones", {})
        ok &= check("midi has 12 zone entries", len(mz) == 12, f"got {len(mz)}: {sorted(mz)}")
    except (OSError, ValueError) as exc:
        failures += 1
        check("midi.json loads", False, str(exc))

    # --- StateMachine + Brain instantiation --------------------------------
    print("=== Core instantiation (StateMachine + Brain) ===")
    try:
        sm = StateMachine(zones, initial_zone="resting", tick_hz=20.0)
        brain = Brain(sm, camera=None, config_loader=lambda: load_brain_config(brain_path))
        ok = check("StateMachine + Brain construct", True)
        ok &= check("initial zone is resting", sm.current_zone == "resting", f"got {sm.current_zone}")
        info = brain.autonomy_info()
        ok &= check("autonomy enabled", bool(info.get("enabled")), f"got {info}")
        ok &= check("3 enabled groups reported",
                    len(info.get("enabled_groups", [])) == 3, f"got {info.get('enabled_groups')}")
    except Exception as exc:
        failures += 1
        check("StateMachine + Brain construct", False, repr(exc))

    # --- Short scripted navigation (zone transitions) ---------------------
    print("=== Zone transition sanity ===")
    try:
        # Follow the plan's exits graph: each hop must be a declared exit of the
        # current zone. The machine is async — trigger_action only arms the
        # transition; update() commits it on arrival at the entry pose. So after
        # triggering we drive one step() at the target's entry pose to commit.
        # A 5-hop walk that stays on declared exits and crosses two mode-group
        # boundaries (museum-closed -> event-sit-back -> stage-takeover).
        seq = [
            ("resting", "stretching", "look"),
            ("stretching", "hunting", "track"),
            ("hunting", "examining", "focus"),
            ("examining", "performing", "trick"),
            ("performing", "roaring", "roar"),
        ]
        for cur, tz, ta in seq:
            exits = [tuple(e) for e in zones.get(cur).exits()]
            if not check(f"{cur} -> {tz}/{ta} is a declared exit", (tz, ta) in exits,
                         f"exits={exits}"):
                failures += 1
                continue
            entry = zones.get(tz).action_entry_pose(ta)
            if not check(f"{cur} -> {tz}/{ta} has an entry pose", entry is not None,
                         "entry pose is None"):
                failures += 1
                continue
            if not check(f"{cur} -> {tz} trigger accepted", sm.trigger_action(tz, ta),
                         "trigger_action returned False"):
                failures += 1
                continue
            sm.step(entry)  # drive one tick at the entry pose -> transition commits
            if not check(f"{cur} -> {tz} transition commits", sm.current_zone == tz,
                         f"current_zone={sm.current_zone}"):
                failures += 1
    except Exception as exc:
        failures += 1
        check("zone transition sanity", False, repr(exc))

    print()
    if failures:
        print(f"=== FAILED: {failures} check(s) failed ===")
        return 1
    print("=== All validations passed. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
