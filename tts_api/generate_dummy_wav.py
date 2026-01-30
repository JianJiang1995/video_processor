import numpy as np
import scipy.io.wavfile
import os

# Create dummy wav
sr = 16000
t = np.linspace(0, 1, sr, endpoint=False)
x = 0.5 * np.sin(2 * np.pi * 440 * t)
scipy.io.wavfile.write('/data2/jj/proj/video_processor/tts/runtime/python/fastapi/prompt.wav', sr, (x * 32767).astype(np.int16))
print("Created prompt.wav")
