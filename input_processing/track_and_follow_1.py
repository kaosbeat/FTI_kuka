import cv2
import math
from collections import defaultdict
from ultralytics import YOLO

class InteractivePersonTracker:
    def __init__(self, model_path: str, video_path: str):
        self.model = YOLO(model_path)
        self.cap = cv2.VideoCapture(video_path)
        
        # Store recent centroids for direction calculation
        self.centroid_history = defaultdict(list)
        self.current_id = None
        self.frame_idx = 0
        
        # 8-point compass directions (clockwise from East)
        self.directions = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
        self.last_printed_dir = None  # To avoid console spam

    def calculate_direction(self, points: list) -> str:
        """Calculate walking direction from a list of (x, y) centroids."""
        if len(points) < 3:
            return "Unknown"
        
        # Use last 10 points for smoothing
        pts = points[-10:]
        x = [p[0] for p in pts]
        y = [p[1] for p in pts]
        
        # Calculate slope (dy/dx). Note: image Y increases downward, so we invert for math coords
        dx = x[-1] - x[0]
        dy = y[0] - y[-1]
        
        if abs(dx) < 1e-3 and abs(dy) < 1e-3:
            return "Unknown"
            
        angle = math.degrees(math.atan2(dy, dx))
        if angle < 0:
            angle += 360
            
        idx = int(angle / 45) % 8
        return self.directions[idx]

    def run(self):
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                break

            # Run YOLO tracking on the current frame (persist keeps IDs stable)
            results = self.model.track(frame, persist=True, verbose=False)[0]
            
            # Update centroid history for all currently tracked people
            if results.boxes is not None and results.boxes.id is not None:
                ids = results.boxes.id.cpu().tolist()
                xyxy = results.boxes.xyxy.cpu().numpy()
                
                for i, track_id in enumerate(ids):
                    if track_id not in self.centroid_history:
                        self.centroid_history[track_id] = []
                    
                    cx = (xyxy[i][0] + xyxy[i][2]) / 2
                    cy = (xyxy[i][1] + xyxy[i][3]) / 2
                    self.centroid_history[track_id].append((cx, cy))
                    
                    # Keep only last 20 points for memory efficiency
                    if len(self.centroid_history[track_id]) > 20:
                        self.centroid_history[track_id].pop(0)

                # Spacebar: switch to next valid tracked ID
                key = cv2.waitKey(1) & 0xFF
                if key == 32:  # Space
                    available = [tid for tid in ids if len(self.centroid_history[tid]) >= 5]
                    if available:
                        if self.current_id in available:
                            idx = available.index(self.current_id)
                            self.current_id = available[(idx + 1) % len(available)]
                        else:
                            self.current_id = available[0]

                # Draw tracks
                for i, track_id in enumerate(ids):
                    xyxy_int = xyxy[i].astype(int)
                    color = (0, 255, 0) if track_id == self.current_id else (0, 100, 255)
                    cv2.rectangle(frame, (xyxy_int[0], xyxy_int[1]), 
                                  (xyxy_int[2], xyxy_int[3]), color, 2)
                    cv2.putText(frame, f"ID:{track_id}", (xyxy_int[0], xyxy_int[1] - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                # Calculate & print direction for current ID
                if self.current_id is not None and self.current_id in ids:
                    history = self.centroid_history[self.current_id]
                    if len(history) >= 3:
                        direction = self.calculate_direction(history)
                        # Only print when direction changes to avoid console flooding
                        if direction != self.last_printed_dir:
                            print(f"[Frame {self.frame_idx}] Tracked ID {self.current_id} -> Walking: {direction}")
                            self.last_printed_dir = direction

            self.frame_idx += 1
            cv2.imshow("YOLO Interactive Tracker (Space=Switch ID, Q=Quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        self.cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    # Replace with your model & video path
    MODEL = "yolo26n.pt"  # or "yolov8n.pt", "yolov10n.pt", etc.
    VIDEO = "data/terrace1-c1.avi"   # Replace with your video file
    
    tracker = InteractivePersonTracker(MODEL, VIDEO)
    tracker.run()