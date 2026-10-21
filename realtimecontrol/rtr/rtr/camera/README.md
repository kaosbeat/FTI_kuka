Given the code, the key insight is: camera tracking only drives the robot's motion when the currently active action/behavior is a camera-servo behavior (locked/intent/curious for track offset, or gentle for face offset), AND that action has variable axes (variable_axes or axis_bands).


Data mode is the default — just omit --video:

python -m rtr.camera.camera_mock --core-host 127.0.0.1 --core-port 8765 --candidates 3 --fps 30
No --video → random-walk candidate telemetry only, no YOLO/pipeline (camera_mock.py:397).
Note: --snapshot-port (the :8080 snapshot server) is video-mode only — there are no real frames in data mode, so nothing to serve.