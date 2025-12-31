import pytest
import requests
import numpy as np
import scipy.io.wavfile
import os
import io

BASE_URL = "http://localhost:50000"

@pytest.fixture(scope="module")
def audio_file():
    """Generates a dummy 16kHz wav file for testing"""
    sr = 16000
    t = np.linspace(0, 1, sr, endpoint=False)
    # Sine wave
    x = 0.5 * np.sin(2 * np.pi * 440 * t)
    
    # Create an in-memory byte buffer
    byte_io = io.BytesIO()
    scipy.io.wavfile.write(byte_io, sr, (x * 32767).astype(np.int16))
    byte_io.seek(0)
    
    return byte_io

def test_server_health():
    """Basic check to see if port is listening (using a GET on docs or just connection)"""
    try:
        # FastAPI usually provides /docs or /openapi.json
        resp = requests.get(f"{BASE_URL}/docs", timeout=5)
        assert resp.status_code == 200
    except requests.exceptions.ConnectionError:
        pytest.fail("Server is not reachable. Is it running?")

def test_inference_zero_shot_success(audio_file):
    """Test successful audio generation"""
    files = {
        'prompt_wav': ('test_prompt.wav', audio_file, 'audio/wav')
    }
    data = {
        'tts_text': 'This is a test of the emergency broadcast system.',
        'prompt_text': 'This is the prompt text used for cloning.'
    }
    
    # reset buffer pointer just in case
    audio_file.seek(0)
    
    resp = requests.post(f"{BASE_URL}/inference_zero_shot", files=files, data=data, stream=True)
    
    assert resp.status_code == 200, f"Inference failed with {resp.status_code}: {resp.text}"
    
    # Check if we got bytes back
    content = b""
    for chunk in resp.iter_content(chunk_size=1024):
        content += chunk
        
    assert len(content) > 0, "Received empty audio response"
    # Basic header check for raw PCM or just size (since response is raw bytes in our server implementation)
    # The server returns raw PCM bytes (int16), not a WAV file structure based on `server_vllm.py` logic.
    
def test_inference_missing_text(audio_file):
    """Test validation error for missing field"""
    files = {
        'prompt_wav': ('test_prompt.wav', audio_file, 'audio/wav')
    }
    data = {
        # 'tts_text': MISSING
        'prompt_text': 'Prompt text.'
    }
    audio_file.seek(0)
    
    resp = requests.post(f"{BASE_URL}/inference_zero_shot", files=files, data=data)
    assert resp.status_code == 422 # Standard FastAPI validation error

def test_inference_empty_audio():
    """Test handling of empty or invalid audio file"""
    files = {
        'prompt_wav': ('empty.wav', io.BytesIO(b""), 'audio/wav')
    }
    data = {
        'tts_text': 'Hello',
        'prompt_text': 'Prompt'
    }
    
    resp = requests.post(f"{BASE_URL}/inference_zero_shot", files=files, data=data)
    # Librosa might fail or server might catch it. Expecting 500 or 422 depending on implementation
    # Our simple implementation wraps in try/except 500
    assert resp.status_code in [422, 500] 
