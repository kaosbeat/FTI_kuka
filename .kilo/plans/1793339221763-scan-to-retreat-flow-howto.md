# How-To: Build the "Scan → Pick → Follow 30 s → Approach → Inspect → Retreat" Loop

Goal: configure `realtimecontrol/rtr/editor.html` so the robot walks a loop —
**scan → pick a person → follow 30 s → move closer → check on the close camera →
move back → loop** — using the Flow tab's zone network plus the brain's camera
engagement config.

> This is a how-to (a guide for building the flow in the editor UI). No source
> code changes; everything is config data edited through the tabs and persisted
> via the existing POST endpoints.

---

## 1. The model (read this first)

Behavior is driven by **two layers** you configure in the editor:

- **Layer A — the physical zone graph (Flow tab).** Zones hold 6-axis poses; each
  action has an ordered `next` list of `{zone, action}` targets. With autonomy on,
  the brain dwells on a looping action for `dwell_s`, then walks the robot to a
  random valid `next` (`brain.py` `_autonomy_tick` → `_random_valid_next`). This is
  what physically moves the robot's **body** between poses. **This is the "flow" you
  build in the Flow tab.**

- **Layer B — the engagement ladder (zone classes, brain-driven by the camera).**
  Every zone has a `class` (`scan / track / farfocus / approach / facefocus /
  retreat / ignore`). The brain's `tick()` runs a camera-driven state machine
  (`_hunt_tick`) that escalates/de-escalates the current class based on what the
  cameras see (person found/lost, distance, face visible). Each class maps to a
  camera preset (`zone_classes[class].camera` in `brain.json`). **This is what makes
  the camera "scan → pick → follow → approach → inspect → retreat."**

The pipeline maps 1:1 onto the ladder:

| Pipeline step        | Zone class      | Camera preset (`brain.json` default) | What the brain does                          |
| -------------------- | --------------- | ------------------------------------ | -------------------------------------------- |
| scan                 | `scan`          | wide / idle / none                   | Looks around for a person                    |
| pick a person        | (transition)    | wide / track / auto                  | Hunt lock acquires a candidate (`detect`)    |
| follow 30 s          | `track`→`farfocus` | wide / track / auto               | Tracks the locked person; dwells 30 s        |
| move closer          | `approach`      | wide / track / auto                  | Escalates when close enough (`engage_dist`)  |
| check on close camera| `facefocus`     | close / analyze / auto               | Cycles face actions (`wink/inspect/call`)    |
| move back            | `retreat`       | wide / idle / none                   | Breaks off for `break_off_s`                 |
| loop                 | → `scan`        | wide / idle / none                   | Person lost → rescan                         |

**Key point:** the Flow-tab flow is the *physical skeleton* (one zone per stage,
wired into a loop). The *camera behavior* at each stage comes from the zone's
`class` + the brain's `zone_classes` / `hunt` / `facefocus` config. You need both.

---

## 2. The five zones

Create one zone per stage (or reuse an existing one — see §8):

| Zone          | class       | pose (6 axes)            | behavior       | dwell_s | next              |
| ------------- | ----------- | ------------------------ | -------------- | ------- | ----------------- |
| `scan_room`   | `scan`      | wide, head up            | `spontaneous`  | 30      | → `follow_p`      |
| `follow_p`    | `track`     | standing, head up        | `locked`       | **30**  | → `approach_p`    |
| `approach_p`  | `approach`  | leaning in               | `intent`       | 10      | → `inspect_p`     |
| `inspect_p`   | `facefocus` | close, head level        | `gentle`       | 15      | → `retreat_p`     |
| `retreat_p`   | `retreat`   | stepping back            | `soothing`     | 3       | → `scan_room`     |

Names are arbitrary; pick ones that don't clash with the existing `scan` /
`scanning` / `lock` / `inspect` zones.

---

## 3. Build the zones (Flow tab)

1. **`+ add zone`** (top bar) → name it `scan_room`. Repeat for `follow_p`,
   `approach_p`, `inspect_p`, `retreat_p`.
2. Select each zone; in the zone form set:
   - **enabled** ✓
   - **speed** (e.g. 20)
   - **class** → the class from the table (the `class` dropdown at the top of the
     zone form — one of `ignore/scan/track/farfocus/approach/facefocus/retreat`).
   - **safezone** → the 6 `[lo,hi]` pairs the pose must stay within (the zone's
     reachable box). Consecutive zones' safezones should overlap so the loop can
     physically walk (see §8 safety gate).
3. **+ add action** in each zone → name it (`look`, `track`, `lean`, `face`, `back`).
   In the action editor modal set:
   - **loop** ✓ (required — the autonomy next-follower only advances looping actions).
   - **enabled** ✓
   - **behavior** → from the table.
   - **speed** → from the table.
   - **poses** → one or more 6-axis anchor poses. An axis set to `x` is a free
     (behavior/camera-driven) axis, not clamped — use it for the axis that follows
     the person.
   - **camera** → the per-action baseline (active/mode/lock). Match the class preset
     (wide/track/auto for the tracking stages; close/analyze/auto for `inspect_p`).
4. **Wire the loop (next-links).** In each action's editor, set the ordered **next**
   list:
   - `scan_room/look` → `follow_p/track`
   - `follow_p/track` → `approach_p/lean`
   - `approach_p/lean` → `inspect_p/face`
   - `inspect_p/face` → `retreat_p/back`
   - `retreat_p/back` → `scan_room/look`

   (In the Flow tab you can also drag one node onto another to draw the arrow; the
   underlying `next` field is the same.)
5. **auto layout** / **fit view** to tidy the canvas.

---

## 4. Set the 30 s follow (dwell)

"Follow 30 s" is the **autonomy dwell** on the follow action. Precedence
(`brain.py` `_autonomy_dwell_s`, highest first):

1. per-action `dwell_s` (the `dwell_s` field in the action editor),
2. the zone's mode-group `params.dwell_s`,
3. global `autonomy.dwell_s` (currently **30** in `brain.json`).

Set `follow_p/track` `dwell_s = 30` explicitly so it is robust to group overrides.
Caveat: the existing `active` group has `params.dwell_s = 35`, which would beat the
global `30` if your zone is in that group — the per-action value wins.

---

## 5. Configure the brain (Brain tab + Cam tab)

**zone_classes** (one card per class, Brain tab):
- `scan`: `scan_s` (how long a scan pose holds).
- `approach`: `engage_dist` (bbox-size threshold for "close enough" → facefocus),
  `approach_speed`.
- `facefocus`: `actions` (comma list, e.g. `wink, inspect, call`), `action_s` (per
  face action), `face_lost_s` (face stale → retreat).
- `retreat`: `break_off_s` (retreat → scan).

**hunt** (Candidates card, Cam tab) — the "pick a person":
- `enabled` ✓
- `min_conf` (lock sensitivity; lower = more sensitive; default 0.1).
- `lost_s` (how long a locked target may go stale before "person lost" → scan).
- `detect.zone` / `detect.action` — the action that runs on a new lock (the "pick").
- `scan.zone` / `scan.action` — the action that runs when the person is lost (rescan).
- ⚠️ The current `brain.json` points these at zone `hunting`, which **does not exist**
  in `zones.json`. Repoint them at your real zones (e.g. `scan_room/look` for scan,
  `follow_p/track` for detect).

**facefocus** (facefocus card, Cam tab) — the "check on the close camera":
- `enabled` ✓
- `zones` — the zone(s) where face actions run (currently `stretching`, which also
  doesn't exist — set to `inspect_p`).
- `actions`, `action_s`, `face_lost_s`.

**autonomy** (global card, Brain tab):
- `enabled` ✓
- `dwell_s` = 30 (the default follow dwell).
- `events.camera` ✓ (a detect/hunt lock triggers an immediate next link).

---

## 6. Enable the zone group (Groups tab)

Navigation runs across the **union of the enabled groups' zones**. Create a group
(e.g. `engage`) containing all five new zones, **enabled** ✓, and set
`params.dwell_s` if you want a group-level dwell. (The existing `active` group holds
`scan`/`scanning`/`lock`/`inspect` + the task bridges.)

---

## 7. Validate + test

1. **Flow tab → `validate`** — checks: zones with no enabled action (dead links),
   zero speed, poses outside safezone/hardware, `axis_bands` not overlapping the
   safezone, red links (`next` pointing at invalid targets), and dead ends
   (non-looping action with no valid next). Click a problem to jump to it.
2. **Cam tab → camera inject buttons** — `cam_candidates` (feed wide-camera
   detections so the hunt lock picks a person) and `cam_face` (feed a close-camera
   face so `facefocus` runs). `proceed` fires the websocket autonomy event.
3. Watch the **hunt state** in the state frame (`state` = current class, `lock_id`,
   `feral`) to confirm the ladder walks scan → track → … → retreat → scan.

---

## 8. Caveats & gotchas

- **Stale references in `brain.json`:** `hunt.params.detect/scan` → zone `hunting`,
  `facefocus.zones` → `stretching`, and the `scan` zone's `mode_group` →
  `event-sit-back` all point at names that don't exist in the current
  `zones.json` / `zone_groups`. The brain warns (not crashes) on an invalid zone
  class, but a hunt/facefocus action that can't resolve to a real zone won't fire.
  Repoint them as part of this build.
- **Reuse vs new zones:** the existing `scan` / `scanning` (class `scan`), `lock`,
  and `inspect` zones already model parts of this pipeline. You can point the
  hunt/facefocus config at them instead of creating five new zones — this how-to
  assumes five dedicated zones for clarity.
- **Dwell precedence:** per-action `dwell_s` > group `params.dwell_s` > global
  `autonomy.dwell_s`. The `active` group's `dwell_s = 35` beats the global `30`
  unless you set a per-action value.
- **The ladder is camera-driven, not link-driven:** the
  track→farfocus→approach→facefocus→retreat escalation happens on camera state
  (distance/face), *independently* of the physical `next` links. The `next` links
  move the robot's **body** between zones; the classes drive the **camera**. Keep
  them consistent (a zone's class should match the stage its pose is for).
- **Safety gate:** a hunt move to a zone is only made when the current zone's
  safezone overlaps the target's; otherwise the hunt state runs in place (no move).
  Make sure consecutive zones' safezones overlap so the loop can physically walk.
- **`x` (free) axes:** a pose axis set to `x` is driven by behavior/camera tracking
  at runtime, not clamped to a fixed value.
