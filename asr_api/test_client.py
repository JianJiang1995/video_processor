#!/usr/bin/env python3
"""
FunASR 客户端测试脚本 (JSON格式)
"""

import asyncio
import json
import base64
import requests
import websockets
import numpy as np
import soundfile as sf
import argparse
import os


def encode_audio_base64(audio_file: str) -> tuple:
    """读取音频文件并编码为Base64"""
    audio_data, sample_rate = sf.read(audio_file, dtype='float32')
    
    # 转换为单声道
    if len(audio_data.shape) > 1:
        audio_data = audio_data.mean(axis=1)
    
    # 重采样到16kHz
    if sample_rate != 16000:
        import scipy.signal
        audio_data = scipy.signal.resample(audio_data, int(len(audio_data) * 16000 / sample_rate))
        sample_rate = 16000
    
    # 转换为int16并编码
    audio_int16 = (audio_data * 32768).astype(np.int16)
    audio_base64 = base64.b64encode(audio_int16.tobytes()).decode('utf-8')
    
    return audio_base64, sample_rate, audio_data


def test_health(server_url: str = "http://localhost:8765"):
    """测试服务健康状态"""
    print(f"\n{'='*60}")
    print("测试服务健康状态")
    print(f"{'='*60}")
    
    try:
        response = requests.get(f"{server_url}/health")
        if response.status_code == 200:
            result = response.json()
            print(f"状态: {result.get('message', 'unknown')}")
            print(f"数据: {json.dumps(result.get('data', {}), indent=2, ensure_ascii=False)}")
        else:
            print(f"错误: {response.status_code}")
    except Exception as e:
        print(f"连接失败: {e}")


def test_json_transcribe(audio_file: str, server_url: str = "http://localhost:8765"):
    """测试 JSON 格式转写 API"""
    print(f"\n{'='*60}")
    print("测试 JSON 格式转写 API")
    print(f"{'='*60}")
    
    audio_base64, sample_rate, _ = encode_audio_base64(audio_file)
    
    request_data = {
        "audio_data": audio_base64,
        "sample_rate": sample_rate,
        "format": "pcm",
        "hotword": None
    }
    
    print(f"发送请求到: {server_url}/api/transcribe")
    print(f"音频数据长度: {len(audio_base64)} 字符")
    
    response = requests.post(
        f"{server_url}/api/transcribe",
        json=request_data
    )
    
    if response.status_code == 200:
        result = response.json()
        print(f"\n响应:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"错误: {response.status_code} - {response.text}")


def test_file_transcribe(audio_file: str, server_url: str = "http://localhost:8765"):
    """测试文件上传转写 API"""
    print(f"\n{'='*60}")
    print("测试文件上传转写 API")
    print(f"{'='*60}")
    
    with open(audio_file, 'rb') as f:
        files = {'file': (os.path.basename(audio_file), f, 'audio/wav')}
        print(f"上传文件: {audio_file}")
        
        response = requests.post(f"{server_url}/api/transcribe/file", files=files)
        
        if response.status_code == 200:
            result = response.json()
            print(f"\n响应:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"错误: {response.status_code} - {response.text}")


def test_keyword_config(server_url: str = "http://localhost:8765"):
    """测试关键词配置 API"""
    print(f"\n{'='*60}")
    print("测试关键词配置 API")
    print(f"{'='*60}")
    
    # 获取当前配置
    print("\n获取当前配置:")
    response = requests.get(f"{server_url}/api/keyword/config")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    
    # 设置新配置
    print("\n设置新关键词:")
    new_config = {
        "keywords": ["你好小助", "开始识别", "语音助手"],
        "threshold": 0.6
    }
    response = requests.post(
        f"{server_url}/api/keyword/config",
        json=new_config
    )
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))


def test_keyword_detect(audio_file: str, server_url: str = "http://localhost:8765"):
    """测试关键词检测 API"""
    print(f"\n{'='*60}")
    print("测试关键词检测 API")
    print(f"{'='*60}")
    
    audio_base64, sample_rate, _ = encode_audio_base64(audio_file)
    
    request_data = {
        "audio_data": audio_base64,
        "sample_rate": sample_rate,
        "format": "pcm"
    }
    
    response = requests.post(
        f"{server_url}/api/keyword/detect",
        json=request_data
    )
    
    if response.status_code == 200:
        result = response.json()
        print(f"\n响应:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"错误: {response.status_code} - {response.text}")


async def test_websocket_json(audio_file: str, server_url: str = "ws://localhost:8765"):
    """测试 WebSocket JSON格式流式识别"""
    print(f"\n{'='*60}")
    print("测试 WebSocket JSON格式流式识别")
    print(f"{'='*60}")
    
    audio_base64, sample_rate, audio_data = encode_audio_base64(audio_file)
    audio_int16 = (audio_data * 32768).astype(np.int16)
    
    ws_url = f"{server_url}/ws/stream"
    print(f"连接: {ws_url}")
    
    async with websockets.connect(ws_url) as websocket:
        print("连接成功!")
        
        # 发送开始消息
        await websocket.send(json.dumps({
            "action": "start",
            "config": {"hotword": ""}
        }))
        
        # 接收开始确认
        response = await websocket.recv()
        print(f"服务器: {response}")
        
        # 分块发送音频 (每次100ms)
        chunk_size = int(sample_rate * 0.1)
        total_chunks = len(audio_int16) // chunk_size + 1
        
        print(f"\n开始发送音频 ({total_chunks} 块)...")
        
        for i in range(0, len(audio_int16), chunk_size):
            chunk = audio_int16[i:i+chunk_size]
            chunk_base64 = base64.b64encode(chunk.tobytes()).decode('utf-8')
            
            await websocket.send(json.dumps({
                "action": "audio",
                "audio_data": chunk_base64
            }))
            
            # 尝试接收结果
            try:
                while True:
                    response = await asyncio.wait_for(websocket.recv(), timeout=0.01)
                    data = json.loads(response)
                    
                    if data.get("type") == "result":
                        result_data = data.get("data", {})
                        is_final = result_data.get("is_final", False)
                        text = result_data.get("text", "")
                        kw = result_data.get("keyword_detected")
                        
                        prefix = "[最终]" if is_final else "[中间]"
                        print(f"  {prefix} {text}")
                        
                        if kw:
                            print(f"  [唤醒词] {kw}")
                            
            except asyncio.TimeoutError:
                pass
            
            await asyncio.sleep(0.05)
        
        # 发送停止消息
        await websocket.send(json.dumps({"action": "stop"}))
        
        # 接收剩余结果
        print("\n等待最终结果...")
        while True:
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                data = json.loads(response)
                print(f"服务器: {json.dumps(data, ensure_ascii=False)}")
                
                if data.get("type") == "event" and data.get("data", {}).get("event") == "session_completed":
                    print("\n识别完成!")
                    break
                    
            except asyncio.TimeoutError:
                print("超时，结束")
                break


async def test_websocket_kws(audio_file: str, server_url: str = "ws://localhost:8765"):
    """测试带关键词唤醒的 WebSocket"""
    print(f"\n{'='*60}")
    print("测试 WebSocket 关键词唤醒模式")
    print(f"{'='*60}")
    
    audio_base64, sample_rate, audio_data = encode_audio_base64(audio_file)
    audio_int16 = (audio_data * 32768).astype(np.int16)
    
    ws_url = f"{server_url}/ws/stream/kws"
    print(f"连接: {ws_url}")
    
    async with websockets.connect(ws_url) as websocket:
        # 接收连接确认
        response = await websocket.recv()
        data = json.loads(response)
        print(f"连接成功! 当前模式: {data.get('data', {}).get('mode')}")
        print(f"唤醒词: {data.get('data', {}).get('keywords')}")
        
        # 分块发送音频
        chunk_size = int(sample_rate * 0.2)  # 200ms
        
        print("\n开始发送音频...")
        print("(待机模式 - 等待唤醒词...)")
        
        for i in range(0, len(audio_int16), chunk_size):
            chunk = audio_int16[i:i+chunk_size]
            chunk_base64 = base64.b64encode(chunk.tobytes()).decode('utf-8')
            
            await websocket.send(json.dumps({
                "action": "audio",
                "audio_data": chunk_base64
            }))
            
            # 接收结果
            try:
                while True:
                    response = await asyncio.wait_for(websocket.recv(), timeout=0.01)
                    data = json.loads(response)
                    
                    msg_type = data.get("type")
                    msg_data = data.get("data", {})
                    
                    if msg_type == "keyword":
                        print(f"\n  [唤醒] 检测到关键词: {msg_data.get('keyword')}")
                        print(f"         置信度: {msg_data.get('confidence'):.2f}")
                        
                    elif msg_type == "event":
                        event = msg_data.get("event")
                        if event == "mode_changed":
                            mode = msg_data.get("mode")
                            print(f"\n  [模式切换] -> {mode}")
                            
                    elif msg_type == "result":
                        text = msg_data.get("text", "")
                        is_final = msg_data.get("is_final", False)
                        prefix = "[最终]" if is_final else "[识别]"
                        print(f"  {prefix} {text}")
                        
            except asyncio.TimeoutError:
                pass
            
            await asyncio.sleep(0.1)
        
        # 发送停止
        await websocket.send(json.dumps({"action": "stop"}))
        
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
            print(f"\n{response}")
        except:
            pass
        
        print("\n测试完成!")


def main():
    parser = argparse.ArgumentParser(description="FunASR 客户端测试 (JSON格式)")
    parser.add_argument("--audio", "-a", type=str, help="测试音频文件路径")
    parser.add_argument("--server", "-s", type=str, default="localhost:8765", help="服务器地址")
    parser.add_argument("--mode", "-m", type=str, 
                        choices=["health", "json", "file", "ws", "kws", "keyword", "all"], 
                        default="all", help="测试模式")
    
    args = parser.parse_args()
    
    http_url = f"http://{args.server}"
    ws_url = f"ws://{args.server}"
    
    if args.mode == "health" or args.mode == "all":
        test_health(http_url)
    
    if args.mode == "keyword" or args.mode == "all":
        test_keyword_config(http_url)
    
    if args.audio:
        if not os.path.exists(args.audio):
            print(f"错误: 音频文件不存在: {args.audio}")
            return
        
        if args.mode == "json" or args.mode == "all":
            test_json_transcribe(args.audio, http_url)
            
        if args.mode == "file" or args.mode == "all":
            test_file_transcribe(args.audio, http_url)
            
        if args.mode == "keyword" or args.mode == "all":
            test_keyword_detect(args.audio, http_url)
            
        if args.mode == "ws" or args.mode == "all":
            asyncio.run(test_websocket_json(args.audio, ws_url))
            
        if args.mode == "kws":
            asyncio.run(test_websocket_kws(args.audio, ws_url))
    else:
        if args.mode not in ["health", "keyword"]:
            print("\n提示: 使用 --audio 参数指定测试音频文件")
            print("例如: python test_client.py --audio test.wav --mode all")


if __name__ == "__main__":
    main()
