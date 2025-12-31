import requests
import os

url = "http://localhost:50000/inference_zero_shot"

# Mock data
tts_text = "你好，我是CosyVoice3，很高兴认识你。"
prompt_text = "你好，我是CosyVoice3，很高兴认识你。"
# Use an existing wav file as prompt. 
# We need to find one. 
# Check asset directory for zero_shot_prompt.wav
prompt_wav_path = "asset/zero_shot_prompt.wav"

if not os.path.exists(prompt_wav_path):
    print(f"Warning: {prompt_wav_path} not found. Test might fail.")
    # create a dummy wav if needed or find another one
else:
    files = {
        'prompt_wav': ('prompt.wav', open(prompt_wav_path, 'rb'), 'audio/wav')
    }
    data = {
        'tts_text': tts_text,
        'prompt_text': prompt_text
    }

    try:
        response = requests.post(url, files=files, data=data, stream=True)
        if response.status_code == 200:
            with open('test_output.wav', 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
            print("Success: Generated test_output.wav")
        else:
            print(f"Error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Connection failed: {e}")
