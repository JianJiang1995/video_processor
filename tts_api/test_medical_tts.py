import requests
import io
import scipy.io.wavfile
import numpy as np

# Create dummy prompt wav
sr = 16000
t = np.linspace(0, 1, sr, endpoint=False)
x = 0.5 * np.sin(2 * np.pi * 440 * t)
scipy.io.wavfile.write("prompt_medical.wav", sr, (x * 32767).astype(np.int16))

url = "http://localhost:50000/inference_zero_shot"
tts_text = "这是一段腹腔镜胆囊切除术的视频片段。片段伊始处于胆囊牵引阶段，视野中主要呈现肝下区，胆囊与肝床的关系清楚，未见任何器械进入画面。随后整个片段中视野保持稳定，胆囊保持良好暴露状态，便于继续识别与评估手术解剖层面。至片段末端，仍处于同一阶段与视野构图，场景无明显操作变化，持续维持暴露，为后续进一步在Calot三角区域的解剖与处理做好准备。"
prompt_text = "Prompt."

files = {
    'prompt_wav': ('prompt_medical.wav', open('prompt_medical.wav', 'rb'), 'audio/wav')
}
data = {
    'tts_text': tts_text,
    'prompt_text': prompt_text
}

print(f"Sending request with text length: {len(tts_text)}")
try:
    response = requests.post(url, files=files, data=data, stream=True)
    if response.status_code == 200:
        with open("output_medical.wav", "wb") as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        print("Success! output_medical.wav saved.")
        import os
        print(f"File size: {os.path.getsize('output_medical.wav')} bytes")
    else:
        print(f"Error: {response.status_code} - {response.text}")
except Exception as e:
    print(f"Exception: {e}")
