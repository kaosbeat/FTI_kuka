# Plan: Restrict interface navigation to available paths (exits + reverse exits)

## Problem

The two operator interfaces disagree about which next-states are offered from the
current zone:

- **Web UI** (`realtimecontrol/rtr/client.html`): renders a button for **every** zone
  and only dims those unreachable via BFS over the `exits` graph (`reachable()`).
  Since the graph is strongly connected, almost every zone is clickable at any time.
- **TUI** (`realtimecontrol/rtr/rtr/remote/tui.py`): already offers only the current
  zone's `exits` (the "GO TO" section) plus the zone's actions grouped by kind.

The intended rule (per the operator): at any moment an interface should offer only
the **possible next states** of the current zone — its declared **exits** and the
**reverse exits** (the zones you could have come from) — and grey out everything else.
No arbitrary "zone" options, nothing outside the safezone the exits graph defines.

## Decision (confirmed with user)

- **Reverse exits = static incoming edges.** Each UI computes them from the zone
  table it already holds: `reverse_exits(zones, cur) = [n for n in zones if cur in zones[n].exits]`.
  No core changes, no navigation-history tracking. In the current symmetric
  `zones.json` the incoming edges equal the outgoing exits, so the offered set is
  unchanged there; the mechanism matters once edges become one-directional.
- **UI-only restriction.** The core (`machine.request_zone`) keeps accepting any
  reachable zone (MIDI / programmatic multi-hop routing still works). Only the two
  operator interfaces restrict what they *offer*.
- **Web UI keeps all zone buttons** but enables only the available ones and dims the
  rest (the user asked for "greyed out", not removed).
- **TUI** lists the GO TO section as `exits ∪ reverse_exits`, marking reverse-only
  entries (a "←" marker) so forward vs. back is visible in asymmetric graphs.

## Out of scope

- Core / `machine.py` / `Snapshot` / display frame: unchanged.
- `editor.html` (zone-data editor, not navigation): unchanged.
- Manual joint-pose control (`set_joint_pose`): unchanged (already safezone-clamped).
- Action buttons: already per-zone and already dim disabled actions; unchanged.

## Changes

### 1. `realtimecontrol/rtr/client.html` (web UI)

- Add a helper `availableZones(current)` returning the set of
  `ZONES[current].exits` ∪ the incoming edges (zones whose `exits` include
  `current`).
- In `renderState`, replace the BFS `reachable(s.zone, name)` dim check with
  `availableZones(s.zone).has(name)`. Keep: `active` = current zone; `dim` =
  disabled zone OR not in `availableZones`.
- Remove (or repurpose) the now-unused `reachable()` BFS helper.
- Note: `FALLBACK_ZONES` already carries `exits`, so the computation works both
  against the live `/api/zones` table and the offline fallback.

### 2. `realtimecontrol/rtr/rtr/remote/tui.py` (TUI)

- Add a pure helper `reverse_exits(zones, zone) -> List[str]` (incoming edges,
  ordered by zone-table order).
- In `build_items`, build the GO TO ("zones") section as the ordered union
  `exits` (declared order) then `reverse_exits` not already in exits; tag each
  item with a `back` flag (`True` when the name is not one of the zone's own exits).
- In `item_label`, append a `←` marker for zone items with `back=True` so reverse
  exits are visually distinct from forward exits.
- Keep `activation_commands` unchanged (zone rows still send `goto_zone`).

### 3. `realtimecontrol/rtr/tests/test_remote_tui.py`

- Add tests for `reverse_exits` (symmetric and asymmetric tables; unknown zone → []).
- Add tests that `build_items` GO TO section = exits ∪ reverse, with correct
  ordering and the `back` flag (no marker when reverse ⊆ exits, marker when a
  reverse-only zone exists).
- Add a test that `item_label` renders the `←` marker for a back zone row.
- Register the new tests in the module's `main()` runner list.

## Validation

- `python tests/test_remote_tui.py` (and `pytest tests/`) — new + existing TUI tests pass.
- Full suite: `pytest tests/` (or run each `tests/test_*.py`) — no core regressions
  (the core is untouched, so all existing tests must still pass).
- `ruff check` on the changed Python file (`rtr/remote/tui.py`) — clean.
- Manual web check: start the core (`python main.py --sim`), open `client.html`,
  confirm only the current zone's exits (+ reverse) are enabled and the rest are
  dimmed; click an enabled button and confirm the robot transitions.
- Manual TUI check: run `python remote/tui.py` against the sim, confirm the GO TO
  section lists exits (+ reverse with `←` when applicable) and activation sends
  `goto_zone`.

## Risks / notes

- The `exits` graph is the safezone model for navigation; restricting to it is safe
  by construction. If a future zone declares a one-way edge, the reverse-exit logic
  (incoming edges) will still offer a safe "back" option.
- The web UI's `FALLBACK_ZONES` and the live table must both carry `exits` for the
  reverse computation; they currently do.
- Labeling (`←` marker) is cosmetic; it only appears when reverse ⊄ exits.
