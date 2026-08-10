import cv2
import math
from collections import defaultdict
from ultralytics import YOLO
import numpy as np

class FaceFocusTracker:
    def __init__(self, person_model_path: str, face_model_path: str, video_path: str):
        # Load person tracker & dedicated face detector
        self.person_model = YOLO(person_model_path)
        self.face_model = YOLO(face_model_path)
        self.cap = cv2.VideoCapture(video_path)

        # History stores face bounding boxes for the currently focused ID
        self.face_history = defaultdict(list)
        self.current_id = None
        self.last_face_bbox = {}  # Fallback bbox when face detection temporarily fails
        
        # 8-point compass directions (clockwise from East)
        self.directions = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
        self.frame_idx = 0

        # Per-ID print throttling to avoid console flooding
        self.last_printed = {}

    def extract_face_in_roi(self, frame, person_bbox):
        """Crops the upper portion of the person and runs face detection."""
        x1, y1, x2, y2 = map(int, person_bbox)
        h = y2 - y1
        # Heads typically sit in the top ~50-60% of a standing person bbox
        y1_crop = y1
        y2_crop = int(y1 + h * 0.6)
        
        # Safe cropping (prevents index errors at image edges)
        y2_crop = min(y2_crop, frame.shape[0])
        if y2_crop <= y1_crop:
            return None
            
        roi = frame[y1_crop:y2_crop, x1:x2]
        if roi.size == 0:
            return None

        # Run face detection on ROI (class 0 is face in yolov8n-face.pt)
        results = self.face_model(roi, verbose=False, classes=[0])[0]
        if results.boxes is None or len(results.boxes) == 0:
            return None

        # Pick the largest face (usually closest to camera)
        best_box = results.boxes.xyxy.cpu().numpy()[0]
        fx, fy, fx2, fy2 = best_box
        
        # Map ROI coordinates back to full frame
        return [x1 + fx, y1_crop + fy, x1 + fx2, y1_crop + fy2]

    def calculate_face_metrics(self, track_id: int):
        """Returns (direction, area_px2) from recent face bboxes."""
        history = self.face_history[track_id]
        if len(history) < 2:
            return None, None

        first_bbox = history[0]
        last_bbox = history[-1]

        # Compute centers
        cx1, cy1 = (first_bbox[0] + first_bbox[2]) / 2, (first_bbox[1] + first_bbox[3]) / 2
        cx2, cy2 = (last_bbox[0] + last_bbox[2]) / 2, (last_bbox[1] + last_bbox[3]) / 2

        dx = cx2 - cx1
        dy = cy1 - cy2  # Invert Y because image coords increase downward
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360

        dir_idx = int(angle / 45) % 8
        direction = self.directions[dir_idx]

        area = int((last_bbox[2] - last_bbox[0]) * (last_bbox[3] - last_bbox[1]))
        return direction, area

    def run(self):
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                break

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break

            # 1. Detect all people (background tracking, not drawn)
            results = self.person_model.track(frame, persist=True, classes=[0], verbose=False)[0]
            
            if self.current_id is not None and results.boxes is not None and results.boxes.id is not None:
                ids = results.boxes.id.cpu().tolist()
                xyxy = results.boxes.xyxy.cpu().numpy()

                # 2. Find the body bbox of the currently focused ID
                current_bbox = None
                for i, tid in enumerate(ids):
                    if tid == self.current_id:
                        current_bbox = xyxy[i]
                        break

                if current_bbox is not None:
                    # 3. Extract face in ROI
                    face_bbox = self.extract_face_in_roi(frame, current_bbox)

                    if face_bbox is not None:
                        self.face_history[self.current_id].append(face_bbox)
                        if len(self.face_history[self.current_id]) > 15:
                            self.face_history[self.current_id].pop(0)
                        self.last_face_bbox[self.current_id] = face_bbox
                    else:
                        # Fallback: maintain tracking using last known face bbox
                        if self.current_id in self.last_face_bbox:
                            self.face_history[self.current_id].append(self.last_face_bbox[self.current_id])
                            if len(self.face_history[self.current_id]) > 15:
                                self.face_history[self.current_id].pop(0)

                    # 4. Calculate metrics
                    dir_str, area = self.calculate_face_metrics(self.current_id)
                    if dir_str and area > 0:
                        current_print = (dir_str, area)
                        if self.last_printed.get(self.current_id) != current_print:
                            self.last_printed[self.current_id] = current_print
                            print(f"[Frame {self.frame_idx}] ID {self.current_id} | "
                                  f"Face Area: {area}px² | Heading: {dir_str}")

                    # 5. Draw ONLY the focused face rectangle
                    if len(self.face_history[self.current_id]) > 0:
                        _, _, x1, y1, x2, y2 = (0,0) + tuple(self.face_history[self.current_id][-1])
                        x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.putText(frame, f"Area:{area}px²", (x1, max(y1-10, 15)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                else:
                    # Person temporarily lost
                    self.face_history.pop(self.current_id, None)
                    self.last_face_bbox.pop(self.current_id, None)

            # 6. Switch focus on Spacebar
            if key == 32:
                available = results.boxes.id.cpu().tolist() if results.boxes is not None and results.boxes.id is not None else []
                if available:
                    self.current_id = available[0]
                    self.face_history[self.current_id] = []
                    self.last_face_bbox[self.current_id] = None
                    self.last_printed[self.current_id] = (None, None)
                    print(f"[Frame {self.frame_idx}] 🎯 Focused on ID {self.current_id}. Press Space again to switch.")

            self.frame_idx += 1
            cv2.imshow("Face Focus Tracker (Space=Switch, Q=Quit)", frame)

        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    PERSON_MODEL = "yolo26n.pt"
    FACE_MODEL = "yolov12n-face.pt"  # Downloads automatically on first run
    VIDEO = "data/terrace1-c1.avi"   # Replace with your video file
    
    tracker = FaceFocusTracker(PERSON_MODEL, FACE_MODEL, VIDEO)
    tracker.run()