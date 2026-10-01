import random
import cv2
import numpy as np
from ultralytics import YOLO
from picamera2 import Picamera2
from libcamera import Transform
from collections import deque
import rtmidi
from trackingstate import trackingstate

class MidiInputHandler:
    def __init__(self, port, state):
        self.port = port
        self._wallclock = time.time()
        self.state = state
        self.ch1poses = state.state["poses"]["ch1"]
        print(self.ch1poses)
        print("*"*80)
        # print(self.state.get("wandermode"))

    def __call__(self, event, data=None):
        message, deltatime = event
        self._wallclock += deltatime
        print("[%s] @%0.6f %r" % (self.port, self._wallclock, message))

        if message[0] == 176:
            if message[1] == 1: 
                if message[2] == 1: # conditions
                    print("init")

        else:
            # print("unkown command")
            pass
        # print(self.state.state)



class PersonFixTracker:
    def __init__(
        self,
        person_model_path="yolov8n.pt",
        camera_num=0,
        width=640,
        height=480,
        frame_rate=30,
        tracker_cfg="bytetrack.yaml",  # "botsort.yaml" or "bytetrack.yaml"
        center_smooth_frames=5,
    ):
        self.model = YOLO(person_model_path)

        # self.model.export(format="ncnn")

        

        self.W = width
        self.H = height
        self.person_class = 0  # COCO person

        self.center_hist = deque(maxlen=center_smooth_frames)

        # Camera (PiCamera2)
        self.picam2 = Picamera2(camera_num=camera_num)
        config = self.picam2.create_video_configuration(
            main={"format": "RGB888", "size": (self.W, self.H)},
            controls={"FrameRate": frame_rate},
        )
        # Incorporate your flip transform
        config["transform"] = Transform(hflip=True, vflip=True)
        self.picam2.configure(config)
        self.picam2.start()

        # Locked target state
        self.fixed_id = None
        self.fixed_active = False
        self.fixed_last_seen_frame = None

        self.frame_idx = 0
        self.tracker_cfg = tracker_cfg

    def grab_frame_bgr(self):
        arr = self.picam2.capture_array()  # usually uint8 HxWx3

        # # Debug once (first frame): print shape/dtype and a few pixels
        # if self.frame_idx == 0:
        #     print("capture_array shape:", arr.shape, "dtype:", arr.dtype)
        #     print("sample pixel [0,0]:", arr[0, 0].tolist())

        # # If the camera output is actually BGR already, we should NOT convert.
        # # If it's RGB, we SHOULD convert RGB->BGR.
        # #
        # # We'll use a simple heuristic: look at the "red-dominant" pixel.
        # # You may adjust this if your scene is unusual.
        # b0, g0, r0 = arr[0, 0][0], arr[0, 0][1], arr[0, 0][2]
        # # If channel 0 looks like red and channel 2 looks like blue, swap is needed.
        # # Heuristic: whichever looks larger between arr[...,0] and arr[...,2] we treat as red.
        # # We'll convert only when it makes sense.
        # if r0 < b0:
        #     # likely RGB swapped (channel 0 is actually R and channel 2 is B) => convert RGB->BGR
        #     frame_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        # else:
        #     # likely already BGR
        #     frame_bgr = arr
        frame_bgr = arr

        return frame_bgr


    def get_tracked_ids_and_boxes(self, results):
        if results.boxes is None or results.boxes.id is None:
            return None, None
        ids = results.boxes.id.cpu().numpy().astype(int)
        xyxy = results.boxes.xyxy.cpu().numpy()
        return ids, xyxy

    def try_acquire_random_target(self, results):
        ids, _ = self.get_tracked_ids_and_boxes(results)
        if ids is None or len(ids) == 0:
            return None
        return int(random.choice(ids.tolist()))

    def get_fixed_bbox_center(self, results, fixed_id):
        ids, xyxy = self.get_tracked_ids_and_boxes(results)
        if ids is None:
            return None

        for i, tid in enumerate(ids):
            if tid == fixed_id:
                x1, y1, x2, y2 = xyxy[i]
                cx = 0.5 * (x1 + x2)
                cy = 0.5 * (y1 + y2)
                return cx, cy, (x1, y1, x2, y2)

        return None

    def run(self):
        while True:
            frame = self.grab_frame_bgr()

            # YOLO tracking on the BGR frame
            results = self.model.track(
                frame,
                persist=True,
                classes=[self.person_class],
                tracker=self.tracker_cfg,
                verbose=False,
            )[0]

            key = cv2.waitKey(1) & 0xFF

            # Q = quit
            if key == ord("q"):
                break

            # Space = lock random person currently in view
            if key == 32:
                new_id = self.try_acquire_random_target(results)
                if new_id is None:
                    self.fixed_id = None
                    self.fixed_active = False
                    self.fixed_last_seen_frame = None
                    self.center_hist.clear()
                    cv2.putText(
                        frame,
                        "No person visible to lock",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2,
                    )
                else:
                    self.fixed_id = new_id
                    self.fixed_active = True
                    self.fixed_last_seen_frame = self.frame_idx
                    self.center_hist.clear()
                    print(f"[Frame {self.frame_idx}] Locked random ID = {self.fixed_id}")

                cv2.imshow("Fix tracker (Space=Lock Random, Q=Quit)", frame)
                self.frame_idx += 1
                continue

            # Default overlay text + center marker
            img_cx, img_cy = self.W / 2.0, self.H / 2.0
            cv2.drawMarker(
                frame,
                (int(img_cx), int(img_cy)),
                (255, 255, 255),
                markerType=cv2.MARKER_CROSS,
                markerSize=12,
                thickness=2,
            )

            # Per-frame reporting for fixed person
            if self.fixed_id is None:
                cv2.putText(
                    frame,
                    "Press Space to lock a random person",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (255, 255, 255),
                    2,
                )
            else:
                fixed = self.get_fixed_bbox_center(results, self.fixed_id)

                if fixed is None:
                    # Person went out of view
                    if self.fixed_active:
                        print(f"[Frame {self.frame_idx}] Fixed ID {self.fixed_id} LOST (out of view)")
                    self.fixed_active = False
                    cv2.putText(
                        frame,
                        f"Fixed ID {self.fixed_id} lost. Press Space to re-lock.",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 0, 255),
                        2,
                    )
                    # Clear smoothing history so next re-lock doesn't jump
                    self.center_hist.clear()
                else:
                    cx, cy, bbox = fixed
                    x1, y1, x2, y2 = bbox

                    self.fixed_active = True
                    self.fixed_last_seen_frame = self.frame_idx

                    self.center_hist.append((cx, cy))
                    sx = sum(p[0] for p in self.center_hist) / len(self.center_hist)
                    sy = sum(p[1] for p in self.center_hist) / len(self.center_hist)

                    dx = sx - img_cx
                    dy = sy - img_cy

                    # Report back every frame
                    print(f"ID {self.fixed_id} offset_px dx={dx:.1f}, dy={dy:.1f}")

                    # Draw fixed bbox/center
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    cv2.circle(frame, (int(cx), int(cy)), 4, (0, 255, 0), -1)

                    cv2.putText(
                        frame,
                        f"LOCK ID {self.fixed_id} dx={dx:.1f} dy={dy:.1f}",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2,
                    )

                    # Visual line from image center to smoothed center
                    cv2.line(frame, (int(img_cx), int(img_cy)), (int(sx), int(sy)), (255, 0, 0), 2)

            cv2.imshow("Fix tracker (Space=Lock Random, Q=Quit)", frame)
            self.frame_idx += 1

        self.picam2.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    # PERSON_MODEL = "yolov8n.pt"  # person detector/tracker weight
    # PERSON_MODEL = "yolo12s.pt" # smaller model
    # PERSON_MODEL = "yolo26n.pt" # newer model
    PERSON_MODEL = "yolo26n_ncnn_model"
    CAM_NUM = 1




    tracker = PersonFixTracker(
        person_model_path=PERSON_MODEL,
        camera_num=CAM_NUM,
        width=640,
        height=480,
        frame_rate=30,
        tracker_cfg="bytetrack.yaml",
        center_smooth_frames=5,
    )
    tracker.run()
