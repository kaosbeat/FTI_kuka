# Action Editor (Step 3) + Flow-tab Actions (Step 4) — Small-Step TODO

**Goal:** Implement the remaining `editor.html` work for the action-editor plan
(`.kilo/plans/1790578000000-action-editor-plan.md`): the **action editor** (Step 3) and the
**flow-tab action rendering** (Step 4), then the leftover validation/tests/README (Step 5).
Split into small, independently verifiable sub-steps, each ending with a handoff entry in
`.kilo/task-handoff.md` (a safe resume point).

## Verified Current State

**Done (Steps 1–2):**
- `rtr/state/zones.py` — `ACTION_KINDS`, per-action `kind`/`loop`/`target` validation, and
  `Zone.action_kind/action_loops/action_target` accessors. Backward compatible.
- `rtr/state/machine.py` — `step_action()` loop-vs-single; `_advance_after_action`;
  `request_exit`; `request_zone(..., entry_action, handoff_pose)`; `_commit_zone(...,
  entry_action)` entry playback. Tests pass.

**`editor.html` (1810 lines) — present but JS-incomplete:**
- CSS already in place: `#actionEditor`/`#aePanel`/`.aeHead`/`.aeSec`/`.aePoseRow`/
  `.aePoseCtl`/`.aePatch`/`.aePreview`/`.aeStatus` (lines 100–122) and flow action-row
  classes `.faRow`/`.faLabel`/`.faLoop`/`.faDot`/`.fedgeTarget` (lines 123–130).
- HTML overlay present (lines 243–254): `#actionEditor` > `#aePanel` > `.aeHead`
  (`#aeTitle`, `#aeSave`, `#aeClose`) + empty `#aeBody`. **No** static patch section, no
  `#aeSaveStatus`.
- **No** action-editor JS: no `AE` state, no `openActionEditor`/`closeActionEditor`,
  `buildActionEditor`, `ensureAeHydra`/`evalAePatchPreview`, `reconcileActionSound`,
  `aeSwapPoses`, `saveActionEditor`; no `validate()` kind/loop/target rules; no edit button
  in `buildActionList`; no init wiring for the modal.
- Flow tab still uses fixed `NODE_H` (line 332) in `flowAutoLayout` (1405/1416/1422),
  `flowFitView` (1436–1437), `flowAddEdge` (1491–1492), `flowAddNode` (1523–1527),
  `flowNodeAt` (1575), `flowDragMove` (1606), `addZone` (1712). **No** action rows, no
  `FA_Y0`/`FA_ROW`, no `flowRenderTargetEdges`, no `flowArrowTarget` marker.

**Reusable helpers (already in `editor.html`):**
- `msgEditor(msg, onType)` (490), `getPatchCode`/`setPatchCode` (711/723, support
  `actions.<zn>.<an>` keys), `commitPatchEditor` (739), `ensurePatchHydra` (780),
  `evalPatchPreview` (796), `validatePatches` (879), `savePatches` (897),
  `buildZoneForm` (992), `buildActionList` (1115), `validate` (1245), `saveZones` (1282),
  `h3`/`list6`/`numGrid`.
- Globals: `AXES` (265), `MODES` (266), `HW_LIMITS` (306), `TABLE`/`SOUND`/`PATCHES` (308–310),
  `PATCHES_FALLBACK` (336), `FLOW` (322), `NODE_W=140`/`NODE_H=70` (332).
- Flow: `svgEl`/`svgPoint`/`edgePath` (1334–1353), `flowBuildDefs` (1355), `flowRender`
  (1536), `flowRenderEdges` (1508), `buildFlowDetail` (1641), init block (1751–1807).

**Known gap (out of scope here, note for later):** `brain.py` `GOTO_ZONE` does not yet pass
an `entry_action` payload. The editor + machine are ready; the brain wiring is a separate
follow-up.

## Confirmed Design (from the parent plan)

- Each action has exactly one `kind` (`internal`|`entry`|`exit`) **plus** a separate `loop`
  bool; optional `target` = `{zone?, action?, pose?}`.
- The action editor is a **modal** (`#actionEditor`) reached from the zones tab (action
  header) and the flow tab (action row). It edits one action: poses (bounded by the zone
  safezone), kind, loop, target, hydra patch, and per-pose MIDI sound.
- Save writes **three** files in parallel: `zones.json` (kind/loop/target/poses),
  `sound.json` (`actions[zone][action]`), `patches.json` (`actions[zone][action]`).
- Flow tab renders actions **inside** zone nodes: entry left-aligned (dot on left edge),
  exit right-aligned (dot on right edge), internal centered; loop marker on looping actions;
  exit→entry edges drawn between zones.

---

## TODO List

> Rule for every step: make the change, run the validation listed, then append a new entry
> at the **top** of `.kilo/task-handoff.md` (format from that file) before starting the next.

### Phase A — Step 3: Action editor (`editor.html`)

**A1 — Modal skeleton (HTML + CSS).**
- In the `#actionEditor` overlay (lines 243–254), after `#aeBody`, add the **static** patch
  section + save status (these are *not* rebuilt by `buildActionEditor`):
  ```html
  <div class="aeSec"><h3>patch</h3>
    <textarea id="aePatchCode" class="aePatch" spellcheck="false"></textarea>
    <div id="aePreview" class="aePreview"></div>
    <div id="aePatchStatus" class="aeStatus"></div></div>
  <div id="aeSaveStatus" class="aeStatus"></div>
  ```
- CSS line 110: change `.aePoseRow` last grid column `64px` → `84px`
  (`repeat(6, 1fr) 56px 84px`) so the `▲ ▼ ×` buttons fit.
- Validate: HTML well-formed; `node --check` on extracted JS (unchanged). Handoff.

**A2 — `AE` state + open/close + patch preview.**
- Add state near `FLOW` (line 332): `const AE = { zone: null, action: null, hydra: null, timer: null };`
  and `const ACTION_KINDS = ["internal", "entry", "exit"];`
- `ensureAeHydra()`: lazily create a **second** `Hydra({canvas, detectAudio:false,
  makeGlobal:false, autoLoop:true})` sized from `Robot3D.screenResolution()` else `[640,360]`
  into `#aePreview` (mirror `ensurePatchHydra`, line 780, but `makeGlobal:false`).
- `evalAePatchPreview(code)`: `AE.hydra.eval(code || PATCHES_FALLBACK.default)`, update
  `#aePatchStatus` (mirror `evalPatchPreview`, line 796).
- `openActionEditor(zn, an)`: `commitPatchEditor()`; set `AE.zone`/`AE.action`; add `.open`
  to `#actionEditor`; `#aeTitle` = `zn + " / " + an`; `#aePatchCode.value =
  getPatchCode("actions."+zn+"."+an)`; call `buildActionEditor()`; `evalAePatchPreview(...)`.
- `closeActionEditor()`: clear `AE.timer`; remove `.open`; if `flowView` → `flowRender()` +
  `buildFlowDetail()`, else `buildZoneForm()`.
- Init wiring (init block, ~line 1800): `#aeClose.onclick = closeActionEditor`; background
  click on `#actionEditor` (when `e.target === e.currentTarget`) closes;
  `#aePatchCode.oninput` → `setPatchCode("actions."+AE.zone+"."+AE.action, value)` immediately,
  then debounce `evalAePatchPreview` 300 ms via `AE.timer`. `#aeSave` = temporary stub that
  sets `#aeSaveStatus` to "save wired in A5" (replaced in A5).
- Add an **edit** button to the `buildActionList` action header (lines 1121–1140) that calls
  `openActionEditor(name, an)` (stop propagation so it doesn't hit the play button).
- Validate: modal opens from the zones tab, shows title + patch preview, closes cleanly.
  `node --check`. Handoff.

**A3 — `buildActionEditor`: kind/loop/enabled + poses.**
- `buildActionEditor()`: rebuild `#aeBody` for `AE.zone`/`AE.action` (read from
  `TABLE.zones[zn].actions[an]`):
  - Section **action**: `kind` `<select>` over `ACTION_KINDS`; `loop` + `enabled`
    checkboxes (checked unless `a.loop === false` / `a.enabled === false`). Changing `kind`
    rebuilds the editor (target section visibility changes).
  - Section **poses**: one row per pose — 6 number inputs (step 0.1) + speed input (default
    10) + `▲` `▼` `×` controls in an `.aePoseCtl`. `+ pose row` button. Pose values outside
    the zone safezone get the `oob` class (reuse the `saveZones` bounds logic, lines 1300–1308).
  - `aeSwapPoses(zn, an, i, j)`: swap `a.pos`, `a.speed`, **and**
    `SOUND.actions[zn][an].poses` at `i`/`j` (keep sound aligned with poses).
- Validate: edit/reorder/delete poses; oob highlights; `node --check`. Handoff.

**A4 — `buildActionEditor`: target + sound.**
- Section **target** (render only when kind is **not** `internal`):
  - `exit`: target `<select>` over all zones with a `(none)` placeholder, plus an action
    `<select>` populated from the selected target zone's actions.
  - `entry`: zone fixed to the current zone; action `<select>` from the current zone.
  - **hand-off pose** checkbox: unchecked → `delete t.pose`; checked →
    `t.pose = (isExit ? z.exitpos : z.startpos).slice()`. Store `t = {zone, action, pose?}`
    on `a.target` (delete `a.target` entirely when kind is `internal`).
- Section **sound**: `reconcileActionSound(zn, an)` ensures `SOUND.actions[zn][an]` exists
  with a `start` message and a `poses` array whose length matches the action pose count
  (pad/truncate). Render a `msgEditor` for `start` and one per pose (reuse `msgEditor`,
  line 490).
- Validate: target shows/hides by kind; sound rows track pose count; `node --check`. Handoff.

**A5 — Save + validation.**
- Extend `validate()` (line 1245): per action, `kind` (if present) must be in
  `ACTION_KINDS`; `loop` (if present) must be a bool; `target` (if present) must be an
  object whose `zone` names an existing zone, `action` is a string, `pose` is 6 numbers.
- `saveActionEditor()`: `reconcileActionSound(AE.zone, AE.action)`; validate with
  `validate()` + `validateSound()` + `validatePatches()`; if clean, POST `/api/zones`,
  `/api/sound`, `/api/patches` in parallel via `Promise.all` (bodies: `TABLE`, `SOUND`,
  `PATCHES`); report success/failure in `#aeSaveStatus`.
- Wire `#aeSave.onclick = saveActionEditor` (replaces the A2 stub).
- Validate: saving persists all three files (manual, or confirm the three POSTs fire);
  validation blocks a bad kind/target; `node --check`. Handoff.

**A6 — Step 3 verification.**
- `node --check` on the extracted inline JS of `editor.html`.
- Run the full Python suite (`python3 tests/test_*.py` directly; pytest is not in the venv)
  to confirm no core regression.
- Handoff (mark Step 3 complete).

### Phase B — Step 4: Flow-tab action rendering (`editor.html`)

**B1 — Node height + layout helpers.**
- Add constants near line 332: `const FA_Y0 = 54, FA_ROW = 16;`
- `nodeH(name) = Math.max(NODE_H, FA_Y0 + n * FA_ROW + 8)` where `n` = the zone's action
  count; `rowY(name, an) = FA_Y0 + idx * FA_ROW + FA_ROW / 2`.
- Replace the fixed `NODE_H` with `nodeH(name)` in: `flowAutoLayout` (1405/1416/1422),
  `flowFitView` (1436–1437), `flowNodeAt` (1575), `flowAddEdge` (1491–1492), `flowDragMove`
  (1606), `addZone` (1712). Keep `NODE_H` as the *minimum*.
- Validate: layout no longer overlaps as action counts grow; `node --check`. Handoff.

**B2 — Action rows inside nodes.**
- In `flowAddNode` (line 1519): after the body rect/labels, render one row per action at
  `rowY(name, an)`:
  - **entry**: left-aligned label; connector dot (`.faDot`) at `cx=4`.
  - **exit**: right-aligned label; dot at `cx=NODE_W-4`.
  - **internal**: centered label, no dot.
  - Looping actions append a loop marker `<tspan class="faLoop" dx="3">↻</tspan>`.
  - Each row: a full-width transparent hit `<rect>`; `mousedown` → `stopPropagation` (so
    node drag doesn't start); `click` → `openActionEditor(name, an)`.
- Validate: rows render with correct alignment/dots/markers; clicking opens the editor;
  `node --check`. Handoff.

**B3 — Target edges (exit→entry).**
- `flowBuildDefs` (1355): add a `flowArrowTarget` marker (orange `#e0a030`).
- `flowRenderTargetEdges(layer)`: for each action with kind `exit` whose `target.zone`
  differs from the source zone and exists, draw an edge (class `fedgeTarget`, marker
  `flowArrowTarget`) from the source action row's right side (`rowY`) to the target entry
  action's left edge (or the target zone center when no entry action is named).
- Call `flowRenderTargetEdges(layer)` from `flowRenderEdges` (1508) after the normal zone
  edges.
- Validate: exit→entry arrows appear between the right nodes; `node --check`. Handoff.

**B4 — Step 4 verification.**
- `node --check` on extracted JS; full Python suite (no regression).
- Update `realtimecontrol/rtr/README.md`: action kinds, loop/single-run, `target` semantics,
  the action editor modal, and the flow-tab action rendering (rows, dots, loop markers,
  exit→entry edges).
- Handoff (mark Step 4 complete).

### Phase C — Step 5: Tests + README

**C1 — Schema validation tests.**
- Extend `tests/test_zone_scoped_actions.py` (or add a focused test) for the new action
  schema: `kind`/`loop`/`target` round-trip through `validate_state_data` (accept valid,
  reject bad `kind` / bad `target.zone` / bad `loop` type / bad `target.pose`).
- Validate: `python3 tests/test_zone_scoped_actions.py` → ALL TESTS PASSED. Handoff.

**C2 — Final validation + README.**
- Run the **full** suite (`test_transition`, `test_state_machine`, `test_patch_override`,
  `test_patch_push`, `test_zone_scoped_actions`) → confirm no regression.
- Confirm the README covers the new behaviour (from B4); fix anything stale.
- Handoff (mark the action-editor plan complete).

---

## Validation Plan (per step + final)

- **JS:** `node --check` on the extracted inline `<script>` of `editor.html` after every
  step that touches JS (A2–A5, B1–B3, A6, B4).
- **Python (no core regression):** run all five test files directly (`python3
  tests/test_*.py`) after A6, B4, C1, C2.
- **Manual (browser, when available):** open `editor.html` over HTTP; edit an action from
  the zones tab and the flow tab; verify poses (bounded), reorder, kind, loop, target,
  patch preview, sound; save; confirm `zones.json` + `sound.json` + `patches.json` persist
  and hot-reload. Verify flow rows (entry/exit/internal alignment, dots, loop markers) and
  exit→entry arrows. (No browser in this environment — JS is syntax-checked and mirrors the
  core; note this in the handoff.)

## Risks / Caveats

- **Two Hydra instances:** the action editor uses a second, `makeGlobal:false` Hydra so it
  doesn't clobber the patches tab's global preview. Create it lazily; tear down is not
  required (one instance lives for the session).
- **Sound/pose alignment:** `aeSwapPoses` must swap the sound `poses` array in lockstep, or
  per-pose MIDI drifts from the motion.
- **`target` cleanup:** switching an action to `internal` must delete `a.target` (the core
  treats a missing target as "no hand-off"); leaving a stale target would create a spurious
  edge in the flow tab.
- **Node growth:** `nodeH` must be used in *every* layout/hit-test site (B1 list) or nodes
  with many actions will overlap or mis-hit during drag.
- **Brain wiring gap:** `GOTO_ZONE` does not yet pass `entry_action`; the editor + machine
  are complete, but a `goto_zone` from the brain won't trigger an entry action until that
  mapping is added (separate follow-up, noted above).
