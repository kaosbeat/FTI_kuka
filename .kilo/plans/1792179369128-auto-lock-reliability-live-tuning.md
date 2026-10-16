# Auto-lock reliability + real-time tuning

## Goal
Make the camera auto-lock actually fire on the real pipeline, and make the hunt/lock
parameters tunable live (without a restart).

## Root cause (why auto-lock never fires)

The lock heuristic in `brain.py:_handle_cam_candidates` (lines 351–389) scores each
candidate as:

```
cx   = (x1 + x2) / 2
cy   = (y1 + y2) / 2
dist = (cx - 0.5)**2 + (cy - 0.5)**2
score = conf - dist * centrality_weight     # centrality_weight = 0.1
```

This assumes **normalized (0–1) coordinates**. The real RPI pipeline
(`pipeline.py:Camera.detect` → `to_telemetry`) sends **pixel** coordinates (a person
near center is at `cx≈320, cy≈240`). So:

```
dist ≈ (320-0.5)² + (240-0.5)² ≈ 159,000
score ≈ conf - 159,000*0.1 ≈ -15,900   (always far below the initial best_score = -1)
```

`best` is never assigned → `_apply_lock` is never called → **auto-lock never fires on
real hardware.**

The mock's random-walk mode (`camera_mock.py:_send_mock_telemetry`, lines 236–248)
sends **normalized** coords, so auto-lock *does* fire in sim — which is why the bug is
invisible until the real pipeline runs. Force-lock (`cam_force_lock` → `_apply_lock`)
bypasses the score entirely, so it still works. That matches the observed behavior
exactly.

### Contradiction to call out
Lowering `min_conf` will **not** fix this. `min_conf=0.1` is already near-zero, and the
score is ~-15,000 regardless of confidence because the centrality term dominates. The
fix is coordinate normalization, not a threshold change.

## Fix (coordinate contract)

Standardize on **pixel coords + explicit frame dims** in the candidates telemetry, and
normalize at the decision point (the brain).

1. `pipeline.py` — `to_telemetry` (lines 439–444): add the frame dims to the candidates
   payload:
   ```
   out["candidates"] = {"camera": result.camera, "w": self.wide.W, "h": self.wide.H,
                        "candidates": result.candidates}
   ```
   (Both cameras share W/H; `self.wide.W/H` is the frame size.)

2. `brain.py` — `_handle_cam_candidates` (lines 351–389): read `w`/`h` from the payload
   and normalize before centrality. Fall back to assuming normalized when `w`/`h` are
   absent (keeps the current mock working):
   ```
   w = p.get("w"); h = p.get("h")
   ...
   cx = (c["x1"] + c["x2"]) / 2
   cy = (c["y1"] + c["y2"]) / 2
   if w and h:
       cx /= w; cy /= h
   ```

3. `camera_mock.py` — random-walk mode `_send_mock_telemetry` (lines 236–265): send
   **pixel** coords + `w`/`h` to match the real contract (convert the current 0–1
   positions to pixels: `x_px = x * 640`, `y_px = y * 480`, with `w=640, h=480`). This
   makes sim and real hardware agree, so sim-validation of the lock is meaningful.
   (The brain's fallback in step 2 still accepts the old normalized mock, so this is
   safe to land in either order.)

The debug UI already handles both coordinate spaces (`camera_debug.html:162-163`,
`detectScale`), so no change needed there for drawing.

## Real-time tuning

The hot-reload path already exists: `POST /api/brain` validates, writes `brain.json`
atomically, and calls `brain.reload()` (`httpserver.py:40-42,194,215`; `engine.py:88-89`).
The state frame already carries `hunt` (state/lock_id/feral) and `cam_telem`
(candidates/track/face) (`display.py:132-138`). So the missing piece is a tuning/visibility
panel on the camera debug page.

Add to `camera_debug.html`:

1. **Live hunt state** — read `frame.hunt` and show `state` / `lock_id` / `feral`.
2. **Per-candidate score breakdown** — the page already has the candidates (`cam_telem`)
   and the hunt params (from `GET /api/brain`). Recompute `conf`, normalized `dist`,
   `score`, and pass/fail against `min_conf` for each candidate, and highlight the one
   that would win. This makes the lock decision visible in real time (the direct answer
   to "why did/didn't it lock?").
3. **Editable hunt params** — inputs for `min_conf`, `centrality_weight`, `lost_s`
   (and optionally `attention_guard`, `detect`/`scan`) with a **Save** button that merges
   into the current config and `POST`s to `/api/brain`. Takes effect immediately via the
   existing hot-reload.

## Files to change
- `realtimecontrol/rtr/rtr/camera/pipeline.py` — add `w`/`h` to candidates telemetry.
- `realtimecontrol/rtr/rtr/brain/brain.py` — normalize candidate coords in
  `_handle_cam_candidates`.
- `realtimecontrol/rtr/rtr/camera/camera_mock.py` — random-walk mode sends pixels + `w`/`h`.
- `realtimecontrol/rtr/camera_debug.html` — hunt-state + per-candidate score panel +
  editable params with `POST /api/brain` save.

## Validation
1. Unit/sim: run core in `--sim` with the random-walk mock (now pixels). Confirm
   `_handle_cam_candidates` picks a lock when a candidate is present and near center,
   and that the winning candidate is the highest-scoring one.
2. Confirm the real pipeline path: run the mock with `--video` (real `Pipeline`, pixel
   coords) and confirm auto-lock now engages (previously it could not).
3. Live tuning: on the camera debug page, change `min_conf` / `centrality_weight` and
   confirm the per-candidate scores update and `POST /api/brain` hot-reloads (no
   restart).
4. Regression: force-lock still works; the mock's normalized fallback still locks
   (backward compat).

## Out of scope
Face-tracking feasibility and A1-dominant movement tuning (separate threads; not part of
this plan). The attention-grab guards in `_apply_lock` (robot busy / guarded behavior /
transitioning) still gate the *visible* robot reaction — that is by design and unchanged;
the lock itself (camera switch + hunt state) now fires reliably.
