import torch
import sys
import traceback

print(f"Python: {sys.version}")
print(f"Torch: {torch.__version__}")

try:
    import numpy
    print(f"Numpy: {numpy.__version__}")
except ImportError as e:
    print(f"Numpy import failed: {e}")

try:
    import scipy
    print(f"Scipy: {scipy.__version__}")
except ImportError as e:
    print(f"Scipy import failed: {e}")
    traceback.print_exc()
except Exception:
    traceback.print_exc()

try:
    from cosyvoice.cli.cosyvoice import AutoModel
    print("CosyVoice import success")
except Exception as e:
    print(f"CosyVoice import failed: {e}")
    traceback.print_exc()
