#!/usr/bin/env python3
"""check_zone_limits.py — verify every value in zones.json against the real axis limits.

Checks (ALL zones and actions, including disabled ones, which are marked [disabled]):
  * zone safezone bands     6 [lo, hi] bands per zone (A1..A6)
  * action poses            6-axis arrays; "x" frozen axes are skipped (counted)
  * action axis_bands       per-axis [lo, hi] bands used by tracking actions

Every out-of-limit value is listed as a change with a proposed clamp to the nearest
limit. By default the script then asks to apply ALL changes, ONE BY ONE, or NONE.

Usage:
  python3 check_zone_limits.py               # report + interactive prompt
  python3 check_zone_limits.py --dry-run     # report only, never writes
  python3 check_zone_limits.py --apply-all   # apply every change without prompting
  python3 check_zone_limits.py --apply-one   # prompt per change (y/n)
  python3 check_zone_limits.py --file Z.json # check a different file

Limits: the real KR60 joint limits (degrees), identical to HARDWARE_LIMITS in
rtr/state/zones.py, validate_zones.js and HW_LIMITS in rtr/remote/view.py:
  A1 [-185, 185]  A2 [-135, 35]  A3 [-95, 158]
  A4 [-180, 180]  A5 [-55, 100]  A6 [-150, 150]

Exit code: 0 when no violations remain (clean, or all applied); 1 when violations
remain unapplied; 2 on file/structure errors.
"""

import argparse
import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass

# Real axis limits (degrees). A3 lower limit is -95 (confirmed against the
# xacro-sourced HARDWARE_LIMITS in rtr/state/zones.py).
AXIS_LIMITS = [(-185, 185), (-135, 35), (-95, 158), (-180, 180), (-55, 100), (-150, 150)]
AXIS_NAMES = ["A1", "A2", "A3", "A4", "A5", "A6"]


def is_num(x):
    """True for real numbers (bool is excluded)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def clamp(v, lo, hi):
    """Clamp v into [lo, hi]; int in -> int out (JSON stays integral)."""
    return max(lo, min(hi, v))


@dataclass
class Violation:
    zone: str
    kind: str        # "safezone" | "pose" | "axis_bands"
    action: str      # "" for safezone
    pose_idx: int    # pose index (pose kind only)
    axis: int        # 0..5
    end: str         # "lo" / "hi" for bands, "" for poses
    value: float
    limit_lo: float
    limit_hi: float
    fixed: float
    disabled: bool = False

    @property
    def axis_name(self):
        return AXIS_NAMES[self.axis]

    def describe(self):
        flag = " [disabled]" if self.disabled else ""
        if self.kind == "safezone":
            loc = f"[{self.zone}] safezone {self.axis_name} {self.end}"
        elif self.kind == "pose":
            loc = f"[{self.zone}] actions.{self.action}.poses[{self.pose_idx}] {self.axis_name}"
        else:
            loc = f"[{self.zone}] actions.{self.action}.axis_bands[{self.axis}] {self.end}"
        return (f"{loc}{flag}: {self.value} outside "
                f"[{self.limit_lo},{self.limit_hi}] -> clamp to {self.fixed}")


def collect(data):
    """Walk zones.json and return (violations, warnings, stats)."""
    violations = []
    warnings = []
    stats = {"zones": 0, "actions": 0, "poses_checked": 0, "x_axes": 0}
    zones = data.get("zones", {})
    if not isinstance(zones, dict):
        warnings.append("top-level 'zones' object missing — nothing to check")
        return violations, warnings, stats

    def band_check(where, zone, action, disabled, ax, band):
        if not isinstance(band, list) or len(band) != 2 or not all(is_num(b) for b in band):
            warnings.append(f"{where} is not a numeric [lo,hi] band — skipped")
            return
        for end, idx in (("lo", 0), ("hi", 1)):
            val = band[idx]
            lo, hi = AXIS_LIMITS[ax]
            if val < lo or val > hi:
                violations.append(Violation(
                    zone, "safezone" if action is None else "axis_bands",
                    action or "", -1, ax, end, val, lo, hi,
                    clamp(val, lo, hi), disabled,
                ))

    for zname, zone in zones.items():
        stats["zones"] += 1
        zdis = not zone.get("enabled", True)
        sz = zone.get("safezone")
        if not isinstance(sz, list) or len(sz) != 6:
            warnings.append(f"[{zname}] safezone missing or not 6 bands — skipped")
        else:
            for ax in range(6):
                band_check(f"[{zname}] safezone {AXIS_NAMES[ax]}", zname, None, zdis, ax, sz[ax])
        for aname, action in (zone.get("actions") or {}).items():
            stats["actions"] += 1
            disabled = zdis or not action.get("enabled", True)
            for pi, pose in enumerate(action.get("poses") or []):
                if not isinstance(pose, list) or len(pose) != 6:
                    warnings.append(
                        f"[{zname}] actions.{aname}.poses[{pi}] is not a 6-axis array — skipped")
                    continue
                stats["poses_checked"] += 1
                for ax in range(6):
                    val = pose[ax]
                    if isinstance(val, str):
                        stats["x_axes"] += 1  # frozen axis: value comes from the live robot
                        continue
                    if not is_num(val):
                        warnings.append(
                            f"[{zname}] actions.{aname}.poses[{pi}] {AXIS_NAMES[ax]} "
                            f"is not numeric ({val!r}) — skipped")
                        continue
                    lo, hi = AXIS_LIMITS[ax]
                    if val < lo or val > hi:
                        violations.append(Violation(
                            zname, "pose", aname, pi, ax, "", val, lo, hi,
                            clamp(val, lo, hi), disabled,
                        ))
            ab = action.get("axis_bands")
            if ab is not None:
                if not isinstance(ab, dict):
                    warnings.append(f"[{zname}] actions.{aname}.axis_bands is not an object — skipped")
                    continue
                for axkey, band in ab.items():
                    try:
                        ax = int(axkey)
                    except ValueError:
                        warnings.append(
                            f"[{zname}] actions.{aname}.axis_bands key {axkey!r} is not an axis index — skipped")
                        continue
                    if not 0 <= ax <= 5:
                        warnings.append(
                            f"[{zname}] actions.{aname}.axis_bands key {axkey!r} out of range — skipped")
                        continue
                    band_check(
                        f"[{zname}] actions.{aname}.axis_bands[{ax}] {AXIS_NAMES[ax]}",
                        zname, aname, disabled, ax, band,
                    )
    return violations, warnings, stats


def apply_violation(data, v):
    zone = data["zones"][v.zone]
    if v.kind == "safezone":
        band = zone["safezone"][v.axis]
        band[0 if v.end == "lo" else 1] = v.fixed
    elif v.kind == "pose":
        zone["actions"][v.action]["poses"][v.pose_idx][v.axis] = v.fixed
    elif v.kind == "axis_bands":
        zone["actions"][v.action]["axis_bands"][str(v.axis)][0 if v.end == "lo" else 1] = v.fixed


def write_file(path, data):
    bak = path + ".bak-pre-limit-check"
    shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"written: {path}  (backup: {bak})")


def report(path, violations, warnings, stats):
    print("=== Zone Limit Check ===")
    print(f"file: {path}")
    print("limits: " + "  ".join(
        f"{AXIS_NAMES[i]} [{lo},{hi}]" for i, (lo, hi) in enumerate(AXIS_LIMITS)))
    print(f"zones: {stats['zones']}   actions: {stats['actions']}   "
          f"poses checked: {stats['poses_checked']}   "
          f"('x' frozen axes skipped: {stats['x_axes']})")
    if warnings:
        print(f"\nWARNINGS ({len(warnings)})")
        for w in warnings:
            print(f"  - {w}")
    if not violations:
        print("\nOK — all zones and actions are within the real axis limits.")
        return
    print(f"\nCHANGES ({len(violations)})")
    for i, v in enumerate(violations, 1):
        print(f"  {i:3d}. {v.describe()}")
    per_zone = Counter(v.zone for v in violations)
    print("\nSUMMARY BY ZONE")
    for z, n in sorted(per_zone.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {z}: {n}")


def interactive_apply(violations, data, path):
    """Ask all / one-by-one / none. Returns number of changes applied."""
    if not sys.stdin.isatty():
        print("\nstdin is not interactive — applying NONE "
              "(use --apply-all / --apply-one to apply).")
        return 0
    print(f"\n{len(violations)} change(s) available.")
    while True:
        try:
            ans = input("Apply all, one by one, or none? [A]/[O]/[n] ").strip().lower()
        except EOFError:
            print("\nEOF — applying NONE.")
            return 0
        if ans in ("a", "all"):
            for v in violations:
                apply_violation(data, v)
            write_file(path, data)
            return len(violations)
        if ans in ("o", "one"):
            applied = 0
            for i, v in enumerate(violations, 1):
                print(f"  {i:3d}. {v.describe()}")
                try:
                    yn = input("    apply? [Y/n] ").strip().lower()
                except EOFError:
                    print("\nEOF — skipping the rest.")
                    break
                if yn in ("", "y", "yes"):
                    apply_violation(data, v)
                    applied += 1
            if applied:
                write_file(path, data)
            return applied
        if ans in ("", "n", "none"):
            return 0
        print("enter a / o / n")


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Check zones.json against the real axis limits and optionally "
                    "apply clamping changes.")
    parser.add_argument("--file", default=os.path.join(here, "zones.json"),
                        help="zones file to check (default: zones.json next to this script)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report only; never writes")
    parser.add_argument("--apply-all", action="store_true",
                        help="apply every change without prompting")
    parser.add_argument("--apply-one", action="store_true",
                        help="prompt per change (y/n)")
    args = parser.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read {args.file}: {e}")
        return 2

    violations, warnings, stats = collect(data)
    report(args.file, violations, warnings, stats)

    if not violations:
        return 0

    if args.dry_run:
        print("\n--dry-run: no changes applied.")
        return 1

    if args.apply_all:
        for v in violations:
            apply_violation(data, v)
        write_file(args.file, data)
        remaining, _, _ = collect(data)
        print(f"applied {len(violations)}/{len(violations)} changes; "
              f"re-check: {len(remaining)} violation(s) remaining")
        return 0 if not remaining else 1

    if args.apply_one:
        applied = interactive_apply(violations, data, args.file)
        remaining, _, _ = collect(data)
        print(f"applied {applied}/{len(violations)} changes; "
              f"re-check: {len(remaining)} violation(s) remaining")
        return 0 if not remaining else 1

    applied = interactive_apply(violations, data, args.file)
    remaining, _, _ = collect(data)
    print(f"applied {applied}/{len(violations)} changes; "
          f"re-check: {len(remaining)} violation(s) remaining")
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
