import torch
import sys

print(f"Python: {sys.version}")
print(f"Torch: {torch.__version__}")
print(f"Torch CUDA available: {torch.cuda.is_available()}")

try:
    import onnxruntime
    print(f"ONNX Runtime: {onnxruntime.__version__}")
    print(f"ONNX Runtime Device: {onnxruntime.get_device()}")
except Exception as e:
    print(f"ONNX Runtime import failed: {e}")

try:
    from cosyvoice.cli.cosyvoice import AutoModel
    print("CosyVoice import success")
except Exception as e:
    print(f"CosyVoice import failed: {e}")
