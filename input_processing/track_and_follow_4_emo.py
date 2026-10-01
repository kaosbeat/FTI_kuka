import cv2
import math
import numpy as np
from collections import defaultdict
from ultralytics import YOLO
from deepface import DeepFace

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from libcamera import controls
from libcamera import Transform 

class EmotionFaceTracker:
    def __init__(self, person_model_path: str, face_model_path: str,
                 camera_num: int = 0, width: int = 640, height: int = 480):
        self.person_model = YOLO(person_model_path)
        self.face_model = YOLO(face_model_path)

        # --- PiCamera2 setup ---
        self.width = width
        self.height = height

        self.picam2 = Picamera2(camera_num=camera_num)

        # Configure to get frames (RGB or BGR).
        # We'll use "RGB888" then convert to BGR for OpenCV.
        config = self.picam2.create_video_configuration(
            main={"format": "RGB888", "size": (self.width, self.height)},
            controls={"FrameRate": 30}
        )
        config["transform"] = Transform(hflip=True, vflip=True)  # or rotation if supported

        self.picam2.configure(config)

        self.picam2.start()

        # --- Your state ---
        self.face_history = defaultdict(list)
        self.last_face_bbox = {}
        self.emotion_cache = {}
        self.last_printed = {}

        self.current_id = None
        self.frame_idx = 0

        self.directions = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]

        self.MIN_FACE_AREA = 3000
        self.EMOTION_CACHE_INTERVAL = 5
        self.emotion_colors = {
            "happy": (0, 255, 0), "sad": (150, 0, 255), "angry": (0, 0, 255),
            "fear": (128, 0, 128), "surprise": (255, 255, 0), "disgust": (0, 128, 128),
            "neutral": (150, 150, 150), "Unknown": (100, 100, 100), "Small/NA": (100, 100, 100)
        }

    def extract_face_in_roi(self, frame, person_bbox):
        x1, y1, x2, y2 = map(int, person_bbox)
        h = y2 - y1
        y1_crop, y2_crop = y1, int(y1 + h * 0.55)
        y2_crop = min(y2_crop, frame.shape[0])
        if y2_crop <= y1_crop:
            return None

        roi = frame[y1_crop:y2_crop, x1:x2]
        if roi.size == 0:
            return None

        results = self.face_model(roi, verbose=False, classes=[0])[0]
        if results.boxes is None or len(results.boxes) == 0:
            return None

        best_box = results.boxes.xyxy.cpu().numpy()[0]
        fx, fy, fx2, fy2 = best_box
        return [x1 + fx, y1_crop + fy, x1 + fx2, y1_crop + fy2]

    def get_emotion(self, face_roi):
        face_resized = cv2.resize(face_roi, (48, 48))
        try:
            res = DeepFace.analyze(
                face_resized,
                actions=["emotion"],
                enforce_detection=False,
                backend="opencv"
            )
            emo_dict = res[0] if isinstance(res, list) else res
            dominant = max(emo_dict["emotion"], key=emo_dict["emotion"].get)
            confidence = emo_dict["emotion"][dominant]
            return dominant, confidence
        except Exception as e:
            print(f"[Emotion Warning] {e}")
            return "Unknown", 0.0

    def run(self):
        while True:
            # --- Grab a frame from PiCamera2 ---
            frame_rgb = self.picam2.capture_array()  # RGB888 uint8 (H, W, 3)
            frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

            # 1. Detect people
            results = self.person_model.track(
                frame, persist=True, classes=[0], verbose=False
            )[0]

            # 2. Switch focus on Spacebar
            if key == 32:
                available = []
                if results.boxes is not None and results.boxes.id is not None:
                    available = results.boxes.id.cpu().tolist()

                if available:
                    self.current_id = available[0]
                    self.face_history[self.current_id] = []
                    self.last_face_bbox[self.current_id] = None
                    self.emotion_cache[self.current_id] = ("Unknown", 0.0)
                    self.last_printed[self.current_id] = (None, None, None, None)
                    print(
                        f"[Frame {self.frame_idx}] 🎯 Focused on ID {self.current_id}.\n"
                        f"💡 Faces <{self.MIN_FACE_AREA}px² will skip emotion detection."
                    )
                continue

            # 3. Process tracking if an ID is selected
            if (
                self.current_id is not None
                and results.boxes is not None
                and results.boxes.id is not None
            ):
                ids = results.boxes.id.cpu().tolist()
                xyxy = results.boxes.xyxy.cpu().numpy()

                current_person_bbox = None
                for i, tid in enumerate(ids):
                    if tid == self.current_id:
                        current_person_bbox = xyxy[i]
                        break

                if current_person_bbox is not None:
                    face_bbox = self.extract_face_in_roi(frame, current_person_bbox)

                    if face_bbox is not None:
                        self.face_history[self.current_id].append(face_bbox)
                        self.last_face_bbox[self.current_id] = face_bbox
                        if len(self.face_history[self.current_id]) > 15:
                            self.face_history[self.current_id].pop(0)
                    elif self.current_id in self.last_face_bbox and self.last_face_bbox[self.current_id] is not None:
                        self.face_history[self.current_id].append(self.last_face_bbox[self.current_id])
                        if len(self.face_history[self.current_id]) > 15:
                            self.face_history[self.current_id].pop(0)

                history = self.face_history.get(self.current_id, [])
                if history and history[-1] is not None:
                    last_bbox = history[-1]

                    # Direction
                    dir_str = "Unknown"
                    if len(history) >= 2:
                        cx_start = (history[0][0] + history[0][2]) / 2
                        cy_start = (history[0][1] + history[0][3]) / 2
                        cx_end = (history[-1][0] + history[-1][2]) / 2
                        cy_end = (history[-1][1] + history[-1][3]) / 2

                        dx = cx_end - cx_start
                        dy = cy_start - cy_end
                        angle = math.degrees(math.atan2(dy, dx))
                        if angle < 0:
                            angle += 360
                        dir_str = self.directions[int(angle / 45) % 8]

                    area = int((last_bbox[2] - last_bbox[0]) * (last_bbox[3] - last_bbox[1]))

                    # Emotion caching
                    emotion, conf = "Unknown", 0.0
                    if area >= self.MIN_FACE_AREA:
                        if self.frame_idx % self.EMOTION_CACHE_INTERVAL == 0:
                            x1, y1, x2, y2 = map(int, last_bbox)
                            face_roi = frame[y1:y2, x1:x2]
                            self.emotion_cache[self.current_id] = self.get_emotion(face_roi)

                        emotion, conf = self.emotion_cache.get(self.current_id, ("Unknown", 0.0))
                    else:
                        self.emotion_cache.pop(self.current_id, None)

                    current_print = (dir_str, area, emotion, conf)
                    if self.last_printed.get(self.current_id) != current_print:
                        self.last_printed[self.current_id] = current_print
                        print(
                            f"[Frame {self.frame_idx}] ID {self.current_id} | "
                            f"Area: {area}px² | Heading: {dir_str} | "
                            f"Emotion: {emotion} ({conf:.1%})"
                        )

                    # Draw focused face only
                    x1, y1, x2, y2 = map(int, last_bbox)
                    color = self.emotion_colors.get(emotion.lower(), (100, 100, 100))
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    label = f"{emotion}\n{area}px²"
                    cv2.putText(
                        frame, label, (x1, max(y1 - 10, 15)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1
                    )
                else:
                    # Person disappeared
                    self.face_history.pop(self.current_id, None)
                    self.last_face_bbox.pop(self.current_id, None)

            self.frame_idx += 1
            cv2.imshow("Emotion Face Tracker (Space=Switch, Q=Quit)", frame_rgb)

        self.picam2.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    PERSON_MODEL = "yolo26n.pt"
    FACE_MODEL = "yolov12n-face.pt"

    tracker = EmotionFaceTracker(PERSON_MODEL, FACE_MODEL, camera_num=1, width=640, height=480)
    tracker.run()
