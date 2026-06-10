import cv2
import torch
from transformers import AutoImageProcessor, AutoModelForImageClassification

# 1. Load model and processor
model_path = "./vit-micro-facial-expressions"
processor = AutoImageProcessor.from_pretrained(model_path)
model = AutoModelForImageClassification.from_pretrained(model_path)

# 2. Setup for inference
model.eval()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)

# 3. Robust label extraction (fixes the AttributeError)
label_map = getattr(processor, 'id2label', None) or getattr(model.config, 'id2label', None)
if label_map:
    label_list = [label_map[i] for i in range(len(label_map))]
else:
    label_list = None  # Will be populated after first inference if needed
print(label_list)
# Label Emotion 0 Angry 1 Disgust 2 Fear 3 Happy 4 Sad 5 Surprise 6 Neutral 📂 Dataset
label_list  = ["Angry","Disgust","Fear",'Happy','Sad', 'Surprise', 'Neutral', "new emotion"]
# 4. Initialize webcam
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Error: Could not open webcam. Try cv2.VideoCapture(1) or another index.")
    exit()

print(f"Using device: {device}")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame")
            break

        # Dynamic label mapping (handles cases where labels aren't in config)
        if label_list is None:
            num_classes = outputs.logits.shape[-1] if 'outputs' in locals() else 7
            label_list = [f"Class_{i}" for i in range(num_classes)]

        # OpenCV loads in BGR, convert to RGB
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Optional but recommended for CPU speed: resize to model input size
        frame_rgb = cv2.resize(frame_rgb, (224, 224))

        # Process image
        inputs = processor(images=frame_rgb, return_tensors="pt").to(device)

        # Run inference
        with torch.no_grad():
            outputs = model(**inputs)

        # Get predicted class
        predicted_class = outputs.logits.argmax(dim=-1).item()
        predicted_label = label_list[predicted_class]

        # Draw result on frame
        cv2.putText(frame, f"Emotion: {predicted_label}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        cv2.imshow('Real-time Emotion Detection', frame)

        # Press 'q' to quit
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    cap.release()
    cv2.destroyAllWindows()