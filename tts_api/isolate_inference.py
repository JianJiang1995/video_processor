import sys
import os
import torch
import librosa
from cosyvoice.cli.cosyvoice import AutoModel

# Mock args
class Args:
    model_dir = "pretrained_models/FunAudioLLM/Fun-CosyVoice3-0___5B-2512"

def main():
    print("Initializing model...")
    try:
        cosyvoice = AutoModel(model_dir=Args.model_dir, load_vllm=True, load_trt=False, fp16=True)
        print("Model initialized.")
    except Exception as e:
        print(f"Init failed: {e}")
        return

    print("Loading prompt audio...")
    try:
        # Create dummy prompt if not exists
        if not os.path.exists("test_prompt.wav"):
            import numpy as np
            import scipy.io.wavfile
            sr = 16000
            t = np.linspace(0, 1, sr, endpoint=False)
            x = 0.5 * np.sin(2 * np.pi * 440 * t)
            scipy.io.wavfile.write("test_prompt.wav", sr, (x * 32767).astype(np.int16))

        prompt_speech_16k, _ = librosa.load("test_prompt.wav", sr=16000, mono=True)
        prompt_speech_16k = torch.from_numpy(prompt_speech_16k)
        print(f"Audio loaded. Shape: {prompt_speech_16k.shape}")
    except Exception as e:
        print(f"Audio load failed: {e}")
        return

    print("Starting inference...")
    tts_text = "This is a test."
    prompt_text = "Prompt text."
    
    try:
        # Run inference
        output = cosyvoice.inference_zero_shot(tts_text, prompt_text, prompt_speech_16k, stream=True)
        print("Inference generator created. Iterating...")
        
        for i, chunk in enumerate(output):
            print(f"Got chunk {i}")
            
        print("Inference completed successfully.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Inference failed: {e}")

if __name__ == "__main__":
    main()
