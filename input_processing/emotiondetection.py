from transformers import AutoImageProcessor, AutoModelForImageClassification
import torch
from PIL import Image

processor = AutoImageProcessor.from_pretrained("./vit-micro-facial-expressions")
model = AutoModelForImageClassification.from_pretrained("./vit-micro-facial-expressions")

image = Image.open("face.jpg")
inputs = processor(images=image, return_tensors="pt")
with torch.no_grad():
    outputs = model(**inputs)
predicted_class = outputs.logits.argmax(dim=-1).item()
print(predicted_class)