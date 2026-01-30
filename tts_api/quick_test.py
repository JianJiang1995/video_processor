#!/usr/bin/env python3
"""快速测试 TTS 服务 - 使用短文本"""
import os
import sys
import time
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 短文本测试
SHORT_TEXT = "你好，这是一个测试。"

def test_tts():
    base_url = "http://localhost:50000"
    
    # 1. 检查服务
    print("1. 检查服务状态...")
    try:
        r = requests.get(f"{base_url}/health", timeout=5)
        print(f"   健康检查: {r.json()}")
    except Exception as e:
        print(f"   ✗ 服务不可用: {e}")
        return False
    
    # 2. 检查可用 speakers
    print("\n2. 检查可用 speakers...")
    try:
        r = requests.get(f"{base_url}/speakers", timeout=5)
        print(f"   Speakers: {r.json()}")
    except:
        print("   speakers 端点不可用（可能需要重启服务）")
    
    # 3. 测试短文本合成
    print(f"\n3. 测试短文本合成: '{SHORT_TEXT}'")
    output_file = os.path.join(SCRIPT_DIR, "test_result", "quick_test.wav")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    start_time = time.time()
    
    try:
        from tts_client import CosyVoiceTTSClient
        client = CosyVoiceTTSClient(host='localhost', port=50000)
        client.text_to_speech(SHORT_TEXT, "默认女声", output_file)
        
        elapsed = time.time() - start_time
        print(f"\n   ✓ 合成完成！")
        print(f"   耗时: {elapsed:.2f} 秒")
        
        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file)
            print(f"   文件大小: {file_size} bytes")
            # 估算音频时长 (22050 Hz, 16bit mono = 44100 bytes/s)
            duration = file_size / 44100
            rtf = elapsed / duration if duration > 0 else 0
            print(f"   估算时长: {duration:.2f} 秒")
            print(f"   RTF: {rtf:.2f}")
            
        return True
        
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n   ✗ 合成失败 ({elapsed:.1f}秒后): {e}")
        return False

if __name__ == '__main__':
    test_tts()



