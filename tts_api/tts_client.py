#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CosyVoice TTS 客户端
支持从 config.yaml 读取服务器配置

使用方式:
# 最简单：使用配置文件中的默认设置
python tts_client.py --text "你好世界" --output output.wav

# 换个音色
python tts_client.py --text "测试语音" --speaker "中文男" --output test.wav

# 指定服务器 (覆盖配置文件)
python tts_client.py --host 192.168.1.100 --port 50000 --text "远程调用"

# 声音克隆模式
python tts_client.py --text "你好" --mode zero_shot --prompt_text "示例文本" --prompt_wav prompt.wav
"""

import os
import sys
import argparse
import requests
import numpy as np
import torch
import torchaudio
from pathlib import Path

# 添加项目根目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    from config_loader import load_config
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False


class CosyVoiceTTSClient:
    """CosyVoice TTS 客户端"""
    
    def __init__(self, host='localhost', port=50000):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        
    @classmethod
    def from_config(cls, config_path=None):
        """从配置文件创建客户端"""
        if not HAS_CONFIG:
            return cls()
        config = load_config(config_path)
        return cls(host=config.server.host, port=config.server.port)
        
    def text_to_speech(self, text, speaker='中文女', output_file='output.wav'):
        """
        文字转语音（预训练音色模式）
        
        Args:
            text: 要合成的文字
            speaker: 说话人音色，如 '中文女', '中文男', '英文女', '英文男', '日语男', '粤语女', '韩语女'
            output_file: 输出音频文件路径
        """
        url = f"{self.base_url}/inference_sft"
        payload = {
            'tts_text': text,
            'spk_id': speaker
        }
        
        print(f"服务器: {self.base_url}")
        print(f"正在合成: {text}")
        print(f"音色: {speaker}")
        
        response = requests.post(url, data=payload, stream=True)
        
        if response.status_code != 200:
            raise Exception(f"请求失败: {response.status_code} - {response.text}")
        
        # 接收音频数据
        tts_audio = b''
        for chunk in response.iter_content(chunk_size=16000):
            tts_audio += chunk
        
        # 转换并保存（保持int16格式，避免精度损失和噪声）
        tts_speech = torch.from_numpy(
            np.frombuffer(tts_audio, dtype=np.int16)
        ).unsqueeze(dim=0)
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用 scipy 保存 wav（兼容性更好）
        try:
            from scipy.io import wavfile
            wavfile.write(str(output_path), 22050, tts_speech.squeeze().numpy())
        except ImportError:
            # 回退到 torchaudio
            torchaudio.save(str(output_path), tts_speech, 22050, format="wav")
        print(f"音频已保存到: {output_path.absolute()}")
        
        return str(output_path)
    
    def clone_voice(self, text, prompt_text, prompt_wav, output_file='output.wav'):
        """
        克隆声音（3s极速复刻模式）
        
        Args:
            text: 要合成的文字
            prompt_text: prompt音频对应的文本
            prompt_wav: prompt音频文件路径
            output_file: 输出音频文件路径
        """
        url = f"{self.base_url}/inference_zero_shot"
        payload = {
            'tts_text': text,
            'prompt_text': prompt_text
        }
        files = [
            ('prompt_wav', ('prompt.wav', open(prompt_wav, 'rb'), 'application/octet-stream'))
        ]
        
        print(f"服务器: {self.base_url}")
        print(f"正在克隆声音合成: {text}")
        
        response = requests.post(url, data=payload, files=files, stream=True)
        
        if response.status_code != 200:
            raise Exception(f"请求失败: {response.status_code} - {response.text}")
        
        # 接收音频数据
        tts_audio = b''
        for chunk in response.iter_content(chunk_size=16000):
            tts_audio += chunk
        
        # 转换并保存（保持int16格式，避免精度损失和噪声）
        tts_speech = torch.from_numpy(
            np.frombuffer(tts_audio, dtype=np.int16)
        ).unsqueeze(dim=0)
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用 scipy 保存 wav（兼容性更好）
        try:
            from scipy.io import wavfile
            wavfile.write(str(output_path), 22050, tts_speech.squeeze().numpy())
        except ImportError:
            # 回退到 torchaudio
            torchaudio.save(str(output_path), tts_speech, 22050, format="wav")
        print(f"音频已保存到: {output_path.absolute()}")
        
        return str(output_path)
    
    def instruct_tts(self, text, speaker, instruct_text, output_file='output.wav'):
        """
        自然语言控制模式（需要Instruct模型）
        
        Args:
            text: 要合成的文字
            speaker: 说话人音色
            instruct_text: 控制指令，如 "用四川话说"
            output_file: 输出音频文件路径
        """
        url = f"{self.base_url}/inference_instruct"
        payload = {
            'tts_text': text,
            'spk_id': speaker,
            'instruct_text': instruct_text
        }
        
        print(f"服务器: {self.base_url}")
        print(f"正在合成: {text}")
        print(f"控制指令: {instruct_text}")
        
        response = requests.post(url, data=payload, stream=True)
        
        if response.status_code != 200:
            raise Exception(f"请求失败: {response.status_code} - {response.text}")
        
        # 接收音频数据
        tts_audio = b''
        for chunk in response.iter_content(chunk_size=16000):
            tts_audio += chunk
        
        # 转换并保存（保持int16格式，避免精度损失和噪声）
        tts_speech = torch.from_numpy(
            np.frombuffer(tts_audio, dtype=np.int16)
        ).unsqueeze(dim=0)
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用 scipy 保存 wav（兼容性更好）
        try:
            from scipy.io import wavfile
            wavfile.write(str(output_path), 22050, tts_speech.squeeze().numpy())
        except ImportError:
            # 回退到 torchaudio
            torchaudio.save(str(output_path), tts_speech, 22050, format="wav")
        print(f"音频已保存到: {output_path.absolute()}")
        
        return str(output_path)
    
    def check_health(self):
        """检查服务是否可用"""
        try:
            # 尝试 /health 端点（新版服务器）
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                return True
            # 尝试 /inference_sft 端点（原版服务器）
            response = requests.get(f"{self.base_url}/inference_sft", timeout=5)
            return response.status_code in [200, 405, 422]  # 422 = 缺少参数，说明服务在运行
        except:
            return False


def main():
    # 加载默认配置
    default_host = 'localhost'
    default_port = 50000
    
    if HAS_CONFIG:
        try:
            config = load_config()
            default_host = config.server.host if config.server.host != '0.0.0.0' else 'localhost'
            default_port = config.server.port
        except:
            pass
    
    parser = argparse.ArgumentParser(description='CosyVoice TTS客户端')
    parser.add_argument('--config', type=str, default=None, help='配置文件路径')
    parser.add_argument('--host', type=str, default=default_host, help=f'服务器地址 (默认: {default_host})')
    parser.add_argument('--port', type=int, default=default_port, help=f'服务器端口 (默认: {default_port})')
    parser.add_argument('--text', type=str, required=True, help='要合成的文字')
    parser.add_argument('--speaker', type=str, default='中文女', 
                       help='音色: 中文女/中文男/英文女/英文男/日语男/粤语女/韩语女')
    parser.add_argument('--output', type=str, default='output.wav', help='输出文件路径')
    parser.add_argument('--mode', type=str, default='sft', 
                       choices=['sft', 'zero_shot', 'instruct'],
                       help='模式: sft(预训练)/zero_shot(克隆)/instruct(控制)')
    parser.add_argument('--prompt_text', type=str, help='prompt文本(zero_shot模式)')
    parser.add_argument('--prompt_wav', type=str, help='prompt音频(zero_shot模式)')
    parser.add_argument('--instruct_text', type=str, help='控制指令(instruct模式)')
    
    args = parser.parse_args()
    
    # 如果指定了配置文件，重新加载
    if args.config and HAS_CONFIG:
        config = load_config(args.config)
        if args.host == default_host:
            args.host = config.server.host if config.server.host != '0.0.0.0' else 'localhost'
        if args.port == default_port:
            args.port = config.server.port
    
    client = CosyVoiceTTSClient(host=args.host, port=args.port)
    
    try:
        if args.mode == 'sft':
            # 预训练音色模式（最简单）
            client.text_to_speech(args.text, args.speaker, args.output)
            
        elif args.mode == 'zero_shot':
            # 克隆声音模式
            if not args.prompt_text or not args.prompt_wav:
                print("错误: zero_shot模式需要提供 --prompt_text 和 --prompt_wav")
                return
            client.clone_voice(args.text, args.prompt_text, args.prompt_wav, args.output)
            
        elif args.mode == 'instruct':
            # 控制指令模式
            if not args.instruct_text:
                print("错误: instruct模式需要提供 --instruct_text")
                return
            client.instruct_tts(args.text, args.speaker, args.instruct_text, args.output)
            
        print("\n✓ 合成完成！")
        
    except Exception as e:
        print(f"\n✗ 错误: {e}")
        print("\n请确保TTS服务已启动:")
        print(f"  cd {SCRIPT_DIR}")
        print(f"  bash start_vllm_server.sh")


if __name__ == '__main__':
    main()
