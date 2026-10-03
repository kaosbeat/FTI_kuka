# input_processing

Prototype vision scripts for the camera side: person detection + tracking (YOLO +
ByteTrack), face detection, and emotion analysis. These run **standalone** (their own
OpenCV window, no core / WebSocket) and were the stepping stones for the production
camera pipeline in `realtimecontrol/rtr/rtr/camera/` (see `camera_remote.py` /
`pipeline.py`).

They are **prototypes**, not a CLI: there is no `argparse`. Each script's settings live
in the `if __name__ == "__main__":` block at the bottom — edit the constants there, then
run the file directly.

## Dependencies

Only the pieces actually used (the `requirements.txt` in this dir is a full `pip freeze`
of the RPi environment, not a curated list):

```bash
pip install opencv-python numpy ultralytics picamera2
# face (track_and_follow_2 / _3 / _4):
pip install deepface
# emotion (track_and_follow_3 / _4): VIT micro-facial-expressions model
pip install torch transformers
```

`picamera2` + `libcamera` are Raspberry-Pi-only (physical cameras). The video-file
variants (`_1`, `_2`, `_3`, `_4_emo_vid`) run on any machine.

## Weights

The `.pt` weights live in this directory (untracked — copy them to the RPi, or let
`ultralytics` auto-download on first run):

| File | Used for |
| ---- | -------- |
| `yolo26n.pt` | person detector / tracker (default) |
| `yolo11n.pt` | alternate person detector |
| `yolov12n-face.pt` | face detector (downloads automatically on first run) |
| `vit-micro-facial-expressions/` | emotion classifier (HuggingFace, `emotiondetection.py`) |

## Scripts

### `track_and_follow_1.py` — interactive person tracker (video)

Detects persons in a video and tracks a chosen ID. **Keys:** `Space` = switch to next
ID, `Q` = quit.

```bash
python track_and_follow_1.py
```

Config (in the `__main__` block): `MODEL` (default `yolo26n.pt`), `VIDEO` (default
`data/terrace1-c1.avi`).

### `track_and_follow_2.py` — face-focus tracker (video)

Person tracking + face detection, focuses on the tracked person's face. **Keys:**
`Space` = switch, `Q` = quit.

```bash
python track_and_follow_2.py
```

Config: `PERSON_MODEL` (`yolo26n.pt`), `FACE_MODEL` (`yolov12n-face.pt`), `VIDEO`.

### `track_and_follow_3_deepface.py` — emotion face tracker (video)

Adds deepface emotion recognition on the tracked face, labelled with the emotion + face
area. **Keys:** `Space` = switch, `Q` = quit.

```bash
python track_and_follow_3_deepface.py
```

Config: `PERSON_MODEL`, `FACE_MODEL`, `VIDEO` (default `data/bronx2.webm`).

### `track_and_follow_4_emo.py` — emotion tracker (live camera)

Same as `_3` but reads a **physical camera** (`picamera2`) instead of a video file.

```bash
python track_and_follow_4_emo.py
```

Config: `PERSON_MODEL`, `FACE_MODEL`, plus `camera_num` (default `1`), `width`/`height`
(default `640`x`480`).

### `track_and_follow_4_emo_vid.py` — emotion tracker (video variant of `_4`)

```bash
python track_and_follow_4_emo_vid.py
```

Config: `PERSON_MODEL`, `FACE_MODEL`, `VIDEO`.

### `trackfollowpi.py` — person fix tracker (live camera)

The direct ancestor of the production `pipeline.py` `Camera` class: tracks a locked
person on a physical camera and reports the pixel offset from frame center. **Keys:**
`Space` = lock a random target, `Q` = quit.

```bash
python trackfollowpi.py
```

Config (in the `__main__` block): `PERSON_MODEL` (default `yolo26n_ncnn_model` — change
to `yolo26n.pt` unless you have the ncnn export), `CAM_NUM` (default `1`), and the
`PersonFixTracker(...)` args: `width`/`height` (`640`x`480`), `frame_rate` (`30`),
`tracker_cfg` (`bytetrack.yaml`), `center_smooth_frames` (`5`).

## Helper / scratch scripts

These are not trackers — they are camera/setup utilities used while prototyping:

| Script | Purpose |
| ------ | ------- |
| `testcamyolo.py` | Opens camera `1` via `picamera2`, prints its controls, runs YOLO. |
| `multicam.py` | Opens cameras `0` + `1` side-by-side (QTGL preview) to verify the two-cam rig. |
| `emotiondetection.py` | Loads the VIT micro-facial-expressions model and runs inference on a frame. |
| `camcontrols.py` | `picamera2` controls demo (locks AGC/AEC after 1 s). |
| `trackingstate.py` | A sample shared tracking-state dict (`id`, `lastseen`, `currentcam`, speeds). |

## Sample video

`data/` holds the test clips (`terrace1-c1.avi`, `bronx2.webm`, etc.) referenced by the
`VIDEO` constants. See `data/readme.md`.

## Moving to production

For the live system, use the RPi camera remote instead of these prototypes:

```bash
# from realtimecontrol/rtr — connects to the core, runs the two-cam pipeline
python -m rtr.camera.camera_remote \
  --core-host 192.168.1.185 --core-port 8765 \
  --wide-cam 0 --close-cam 1 --show
```

The production pipeline resolves its weights from `realtimecontrol/rtr/rtr/camera/models/`
(see the camera README in `realtimecontrol/rtr/`).
