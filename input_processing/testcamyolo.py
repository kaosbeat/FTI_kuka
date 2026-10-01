import cv2
from picamera2 import Picamera2
from libcamera import Transform 
from ultralytics import YOLO

# Initialize the Picamera2
picam2 = Picamera2(1)
print(picam2.controls)
picam2.preview_configuration.main.size = (1280, 720)
picam2.preview_configuration.main.format = "RGB888"
picam2.preview_configuration.align()

config = picam2.create_video_configuration()
config["transform"] = Transform(hflip=True, vflip=True)  # or rotation if supported
picam2.configure(config)
print(config.keys())
print(config)
#picam2.configure("preview")

#picam2.set_controls({'ExposureTime': 200000, "AnalogueGain": 200})
#picam2.set_controls({"AeEnable": True, "AeExposureMode": "Long"})

picam2.start()

# Load the YOLO26 model
model = YOLO("yolo26n.pt")
#model = YOLO("yolo26n_ncnn_model")

while True:
    # Capture frame-by-frame
    frame = picam2.capture_array()

    # Run YOLO26 inference on the frame
    results = model(frame)

    # Visualize the results on the frame
    annotated_frame = results[0].plot()

    # Display the resulting frame
    cv2.imshow("Camera", annotated_frame)

    # Break the loop if 'q' is pressed
    if cv2.waitKey(1) == ord("q"):
        break

# Release resources and close windows
cv2.destroyAllWindows()
