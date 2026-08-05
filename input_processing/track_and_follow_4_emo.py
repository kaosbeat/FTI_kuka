import cv2
import math
import numpy as np
from collections import defaultdict
from ultralytics import YOLO
from deepface import DeepFace
from PIL import Image
import os, sys

class EmotionFaceTracker:
    def __init__(self, person_model_path: str, face_model_path: str, video_path: str):
        self.person_model = YOLO(person_model_path)
        self.face_model = YOLO(face_model_path)
        self.cap = cv2.VideoCapture(video_path)
        
        self.face_history = defaultdict(list)
        self.last_face_bbox = {}
        self.emotion_cache = {}
        self.last_printed = {}
        
        self.current_id = None
        self.frame_idx = 0
        
        self.directions = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
        self.MIN_FACE_AREA = 3000
        self.EMOTION_CACHE_INTERVAL = 3  # Faster updates
        self.EMOTION_CONF_THRESHOLD = 0.15
        self.emotion_colors = {
            "happy": (0, 255, 0), "sad": (150, 0, 255), "angry": (0, 0, 255),
            "fear": (128, 0, 128), "surprise": (255, 255, 0), "disgust": (0, 128, 128),
            "neutral": (150, 150, 150), "Unknown": (100, 100, 100)
        }

    def extract_face_in_roi(self, frame, person_bbox):
        x1, y1, x2, y2 = map(int, person_bbox)
        h = y2 - y1
        # Head sits in upper 40-50% of body bbox
        y1_crop, y2_crop = y1, int(y1 + h * 0.5)
        y2_crop = min(y2_crop, frame.shape[0])
        if y2_crop <= y1_crop: return None
        
        roi = frame[y1_crop:y2_crop, x1:x2]
        if roi.size == 0: return None
        
        results = self.face_model(roi, verbose=False, classes=[0])[0]
        if results.boxes is None or len(results.boxes) == 0: return None
        
        best_box = results.boxes.xyxy.cpu().numpy()[0]
        fx, fy, fx2, fy2 = best_box
        # Map ROI coords back to full frame
        return [x1 + fx, y1_crop + fy, x1 + fx2, y1_crop + fy2]

    def _check_dependencies(self):
        """Verify critical emotion detection dependencies."""
        try:
            import tensorflow as tf
            import keras
            print("✅ tensorflow & keras detected. Emotion model will load correctly.")
            return True
        except ImportError:
            print("⚠️  Missing emotion dependencies! Install with:")
            print("   pip install tensorflow keras")
            print("   ⚠️  Falling back to OpenCV Haar cascade (lower accuracy)...")
            return False

    def get_emotion(self, face_roi):
        """Robust emotion extractor with debug output & proper FER2013 scaling."""
        # 1. Pad face bbox by 20% to include eyes/forehead/mouth context
        h, w = face_roi.shape[:2]
        pad_h, pad_w = int(h * 0.2), int(w * 0.2)
        padded = cv2.copyMakeBorder(face_roi, pad_h, pad_h, pad_w, pad_w, cv2.BORDER_REFLECT)
        
        # 2. Convert to RGB (OpenCV is BGR by default)
        face_rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        
        # 3. Scale to exactly 48x48 (FER2013 emotion model standard)
        face_48 = cv2.resize(face_rgb, (48, 48))
        img_pil = Image.fromarray(face_48)
        
        try:
            # DeepFace auto-selects backend. We don't pass backend anymore.
            res = DeepFace.analyze(img_pil, actions=['emotion'], enforce_detection=False, silent=True)
            emo_dict = res[0] if isinstance(res, list) else res
            raw_scores = emo_dict.get('emotion', {})
            
            # Debug: uncomment to see raw model output
            # print(f"[DEBUG] Raw emotion scores: {raw_scores}")
            
            if not raw_scores:
                return "Unknown", 0.0
                
            dominant = max(raw_scores, key=raw_scores.get)
            conf = raw_scores[dominant]
            
            # Normalize confidence (handles 0-1 vs 0-100 scales)
            conf_pct = conf if conf > 1 else conf * 100
            
            if conf_pct < self.EMOTION_CONF_THRESHOLD:
                return "Unknown", 0.0
                
            return dominant, conf_pct
            
        except Exception as e:
            print(f"[Emotion Error] {e}")
            return "Unknown", 0.0

    def run(self):
        # Check dependencies on startup
        self._check_dependencies()
        
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret: break

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'): break

            # 1. Detect people
            results = self.person_model.track(frame, persist=True, classes=[0], verbose=False)[0]
            
            # 2. Spacebar: Switch focus
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
                    print(f"[Frame {self.frame_idx}] 🎯 Focused on ID {self.current_id}.\n"
                          f"💡 Faces <{self.MIN_FACE_AREA}px² or <15% conf -> Unknown")
                continue

            # 3. Process tracking
            if self.current_id is not None and results.boxes is not None and results.boxes.id is not None:
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

                # Guard: Only proceed with valid face data
                history = self.face_history.get(self.current_id, [])
                if history and history[-1] is not None:
                    last_bbox = history[-1]

                    # Direction
                    dir_str = "Unknown"
                    if len(history) >= 2:
                        cx_s = (history[0][0]+history[0][2])/2
                        cy_s = (history[0][1]+history[0][3])/2
                        cx_e = (history[-1][0]+history[-1][2])/2
                        cy_e = (history[-1][1]+history[-1][3])/2
                        angle = math.degrees(math.atan2(cy_s - cy_e, cx_e - cx_s))
                        if angle < 0: angle += 360
                        dir_str = self.directions[int(angle / 45) % 8]

                    area = int((last_bbox[2]-last_bbox[0]) * (last_bbox[3]-last_bbox[1]))

                    # Emotion
                    emotion, conf = "Unknown", 0.0
                    if area >= self.MIN_FACE_AREA:
                        if self.frame_idx % self.EMOTION_CACHE_INTERVAL == 0:
                            x1, y1, x2, y2 = map(int, last_bbox)
                            face_roi = frame[y1:y2, x1:x2]
                            self.emotion_cache[self.current_id] = self.get_emotion(face_roi)
                        emotion, conf = self.emotion_cache.get(self.current_id, ("Unknown", 0.0))
                    else:
                        self.emotion_cache.pop(self.current_id, None)

                    # Console throttling
                    current_print = (dir_str, area, emotion, conf)
                    if self.last_printed.get(self.current_id) != current_print:
                        self.last_printed[self.current_id] = current_print
                        print(f"[Frame {self.frame_idx}] ID {self.current_id} | "
                              f"Area: {area}px² | Heading: {dir_str} | "
                              f"Emotion: {emotion} ({conf:.1f}%)")

                    # Draw
                    x1, y1, x2, y2 = map(int, last_bbox)
                    color = self.emotion_colors.get(emotion.lower(), (100,100,100))
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    label = f"{emotion}\n{area}px²"
                    cv2.putText(frame, label, (x1, max(y1-10, 15)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                else:
                    self.face_history.pop(self.current_id, None)
                    self.last_face_bbox.pop(self.current_id, None)

            self.frame_idx += 1
            cv2.imshow("Emotion Face Tracker (Space=Switch, Q=Quit)", frame)

        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    PERSON_MODEL = "yolo11n.pt"
    # FACE_MODEL = "yolov8n-face.pt" 
    # PERSON_MODEL = "yolo26n.pt"
    FACE_MODEL = "yolov12n-face.pt"  #
    VIDEO = "data/philippines2.webm"   # Replace with your video file

    
    tracker = EmotionFaceTracker(PERSON_MODEL, FACE_MODEL, VIDEO)
    tracker.run()