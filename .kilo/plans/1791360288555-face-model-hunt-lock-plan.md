# Face Model + Hunt Lock Fix (Two-Camera, Re-Lock on Close)

## Root cause (verified in code)

- Wide and close are **separate YOLO + ByteTrack trackers with independent id
  spaces** (`pipeline.py:331-338`). The brain locks a **wide** id, switches to
  `close/analyze` (`brain.py:258`), but the close tracker never contains that wide
  id → `track_offset` returns None (`pipeline.py:203-237`) → no `CAM_TRACK` → the
  hunt lost-check fires (`brain.py:386+`) → rescan → infinite loop.
- In sim it is total: `CameraMock` sets only `wide_video` (`camera_mock.py:67`);
  the close camera has no source → produces nothing → same loop.
- **The re-lock-on-close already exists**: `brain.py:_handle_cam_candidates`
  re-runs on the *active* camera's candidates and re-locks on the best one
  (`brain.py:250-265`), re-seeding the lost timer on a new lock. So the lock
  **does stick once the close camera produces candidates**. The fix is to (a) give
  the close camera a real source in sim, and (b) make the close camera produce
  **real face boxes**.
- The face is currently **faked**: `Pipeline._analyze_face` (`pipeline.py:382-389`)
  builds a bbox from the track's `cx/cy/w/h`; `FaceDetector`
  (`pipeline.py:285-310`) is a stub that never loads a model.
- **The brain needs no changes.** The re-lock and the face telemetry path
  (`brain.py:_handle_cam_face` reads `bbox/dx/dy/id`) already work; the fix is
  entirely in the camera layer + the model.

## Decisions (resolved with user)

- **Architecture**: keep two cameras (wide detect + close face). Re-lock on the
  close tracker's id during the wide→close switch (already in
  `_handle_cam_candidates`).
- **Face model**: a small YOLO-**n** face model with **person + face** classes,
  placed in `rtr/camera/models/` as `yolo26n-face.pt`. Runs on the RPi5 **CPU**
  via `ultralytics` (the RPi5 has no discrete GPU/VRAM; the VideoCore GPU shares
  RAM). One face-capable model on the close camera (person for tracking, face for
  analysis) — one inference per close frame, RPi5-friendly.

## Tasks

### 1. Source + place the face model (user action)
- Get a YOLO-**n** model that detects **person** (class 0) and **face** (class 1),
  small enough for RPi5 CPU (~10-30 FPS at 640x480). Sourcing options:
  1. A pre-trained YOLO face model (e.g. from the Roboflow model zoo).
  2. Fine-tune `yolo26n.pt` on a face dataset (Wider Face / a Roboflow face
     dataset) to add a `face` class — reuses the proven weights.
  3. A community YOLO face model from GitHub.
- Drop it in `rtr/camera/models/` as **`yolo26n-face.pt`**.
- Verify: the model's `names.txt` lists `[person, face]` (person=0, face=1). If the
  indices differ, adjust the `classes` values in Tasks 2-3 accordingly.

### 2. Make `Camera.detect()` class-aware — `pipeline.py`
- `Camera.__init__` (`pipeline.py:81-108`): add `classes` param (default `(0,)`);
  store `self.classes`.
- `Camera.detect()` (`pipeline.py:170-201`): pass `classes=self.classes` to
  `.track()`; read `results.boxes.cls` and add `"cls": int(cls[i])` to each
  candidate dict. (Backward-compatible: the wide camera's candidates gain a
  `cls=0` field the brain ignores.)

### 3. Wire the face model into the close `Camera` — `pipeline.py`
- `Pipeline.__init__` (`pipeline.py:321-344`): add `face_model_path` param
  (default `"yolo26n-face.pt"`). Pass the close `Camera`
  `model_path=face_model_path, classes=(0, 1)`; the wide `Camera` keeps
  `model_path=model_path, classes=(0,)`.
- `Pipeline.face`: the close `Camera` now runs the face model, so
  `FaceDetector` no longer loads its own model (see Task 5).

### 4. Split person/face in `Camera.process()` — `pipeline.py`
- `FrameResult` (`pipeline.py:45-55`): add `faces: Optional[List[Dict]] = None`.
- `Camera.process()` (`pipeline.py:245-271`): after `candidates = self.detect(frame)`,
  split by `cls` — `persons = [c for c in candidates if c["cls"] == 0]`,
  `faces = [c for c in candidates if c["cls"] >= 1]`. Run `track_offset(persons)`
  (so a face box is never treated as a person), and return
  `FrameResult(..., candidates=persons, track=track, faces=faces)`.

### 5. Rewrite `FaceDetector` to pick the real face box — `pipeline.py`
- `FaceDetector` (`pipeline.py:285-310`): drop the model loading; `analyze(face_boxes,
  person_bbox)` returns the face box that best overlaps (IoU) the locked person's
  bbox as `{"bbox": [x1, y1, x2, y2]}`, else None.
- `Pipeline._analyze_face` (`pipeline.py:382-389`): call
  `self.face.analyze(result.faces, <locked person bbox from result.track>)`.
- `to_telemetry` (`pipeline.py:414-429`) already computes `dx/dy` from `face["bbox"]`
  and tags `id` from the track — no change needed there.

### 6. Give the close camera a source in sim — `camera_mock.py`
- `CameraMock.__init__` (`camera_mock.py:33-79`): add `close_video` param.
- Video mode (`camera_mock.py:60-72`): pass `close_video=(close_video or video)` to
  the `Pipeline` so the close camera runs on the same video as the wide (enough to
  test the two-tracker re-lock + face flow).
- CLI: add `--close-video` (default None → reuses `--video`).

### 7. Expose the face model on the real RPI — `camera_remote.py`
- `CameraRemote.__init__` (`camera_remote.py:32-52`): add `face_model_path`
  (default `"yolo26n-face.pt"`); pass it to `Pipeline`.
- CLI (`camera_remote.py:187-201`): add `--face-model` (default `yolo26n-face.pt`).
- The wide camera keeps `--model` (yolo26n); the close camera uses `--face-model`.

### 8. Validation
- **Static**: `python -m py_compile` on `pipeline.py`, `camera_mock.py`,
  `camera_remote.py`; `node --check` unchanged (no JS touched).
- **Dynamic** (venv `/tmp/kilo/rtrvenv`): run the existing harnesses
  (`brain_config_test.py`, `hunt_test.py`, `facefocus_test.py`,
  `brain_http_test.py`, `hunt_integ.py`) — the random-walk mock (single id space)
  must still pass.
- **Re-lock-on-close (video mode)**: run the mock with `--video <test.mp4>`
  (both cameras now get the video) + facefocus enabled; confirm the lock sticks
  (a stable `CAM_TRACK` for the locked **close** person id) and a real face box
  appears (`CAM_FACE`). Add a test video with a person if none exists.
- **Face model**: run the close `Camera` on the test video; confirm face boxes
  appear with `cls=1` and the debug page shows the face.

## Risks / edge cases

- **Class mapping**: the face model must have person=0, face=1. Verify
  `names.txt`; if different, adjust the `classes` tuples (Tasks 2-3) and the
  `cls` filters (Task 4).
- **RPi5 CPU speed**: a YOLO-n face model at 640x480 on RPi5 CPU should hit
  ~10-30 FPS. If too slow, lower the close camera's `frame_rate` or export the
  model to ONNX/OpenCV-DNN (follow-up optimization).
- **Tracker freeze**: an inactive camera's ByteTrack freezes (no frames). This is
  inherent to the two-camera design; the re-lock on the active camera's candidates
  handles it (the brain re-locks on a new id after a rescan).
- **Sim fidelity**: reusing the same video for both cameras means the close
  "camera" sees the wide's scene — fine for testing the pipeline logic, not the
  real close-up hardware.

## Open questions

- None blocking. The specific face-model file is a "source this" item (Task 1);
  any YOLO-n with person+face classes works once placed as `yolo26n-face.pt`.
