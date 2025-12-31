#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 服务测试脚本 - 腹腔镜手术解说测试
"""

import os
import sys
import time

# 添加项目路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from tts_client import CosyVoiceTTSClient

# 测试文本 - 腹腔镜胆囊切除术解说
TEST_TEXT = """这是一段腹腔镜胆囊切除术的视频片段。片段伊始处于胆囊牵引阶段，视野中主要呈现肝下区，胆囊与肝床的关系清楚，未见任何器械进入画面。随后整个片段中视野保持稳定，胆囊保持良好暴露状态，便于继续识别与评估手术解剖层面。至片段末端，仍处于同一阶段与视野构图，场景无明显操作变化，持续维持暴露，为后续进一步在Calot三角区域的解剖与处理做好准备。"""

def main():
    print("=" * 60)
    print("  TTS 服务测试 - 腹腔镜手术解说")
    print("=" * 60)
    
    # 从配置加载或使用默认值
    try:
        from config_loader import load_config
        config = load_config()
        host = 'localhost'  # 客户端连接用 localhost
        port = config.server.port
        print(f"从配置文件加载: 端口 {port}")
    except Exception as e:
        host = 'localhost'
        port = 50000
        print(f"使用默认配置: {host}:{port}")
    
    print(f"\n服务地址: http://{host}:{port}")
    print(f"\n测试文本 ({len(TEST_TEXT)} 字):")
    print("-" * 40)
    print(TEST_TEXT)
    print("-" * 40)
    
    # 创建客户端
    client = CosyVoiceTTSClient(host=host, port=port)
    
    # 检查服务状态
    print("\n检查服务状态...")
    if not client.check_health():
        print("✗ 服务不可用，请先启动服务:")
        print(f"  cd {SCRIPT_DIR}")
        print("  bash start_vllm_server.sh")
        return False
    
    print("✓ 服务运行正常")
    
    # 输出文件
    output_file = os.path.join(SCRIPT_DIR, "test_result", "medical_surgery_demo.wav")
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    print(f"\n开始合成...")
    print(f"输出文件: {output_file}")
    
    start_time = time.time()
    
    try:
        # 使用预训练女声音色
        print("\n使用预训练音色: 中文女")
        client.text_to_speech(
            text=TEST_TEXT,
            speaker="中文女",
            output_file=output_file
        )
        
        elapsed = time.time() - start_time
        
        print("\n" + "=" * 60)
        print(f"✓ 合成完成！")
        print(f"  耗时: {elapsed:.2f} 秒")
        print(f"  文件: {output_file}")
        
        # 获取文件信息
        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file) / 1024
            print(f"  大小: {file_size:.1f} KB")
        
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n✗ 合成失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)

