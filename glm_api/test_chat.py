#!/usr/bin/env python3
"""
GLM-4.6V-Flash 聊天测试
"""
import asyncio
import httpx
import sys
import argparse
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from config import load_config


async def test_chat(api_url: str, model: str, message: str, config=None):
    """测试文本聊天"""
    print("=" * 60)
    print("GLM-4.6V-Flash 聊天测试")
    print("=" * 60)
    print(f"API: {api_url}")
    print(f"Model: {model}")
    print(f"Message: {message}")
    print("-" * 60)
    
    # 使用配置中的推理参数
    if config:
        temperature = config.inference.temperature
        top_p = config.inference.top_p
        max_tokens = config.inference.max_tokens
    else:
        temperature = 0.8
        top_p = 0.6
        max_tokens = 1000
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens
    }
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # 检查服务
            try:
                resp = await client.get(f"{api_url}/models")
                if resp.status_code == 200:
                    print("✅ 服务可用")
                    models = resp.json().get("data", [])
                    print(f"可用模型: {[m['id'] for m in models]}")
            except Exception as e:
                print(f"❌ 服务不可用: {e}")
                return False
            
            print("\n发送请求...")
            resp = await client.post(f"{api_url}/chat/completions", json=payload)
            resp.raise_for_status()
            
            data = resp.json()
            if "choices" in data:
                content = data["choices"][0]["message"]["content"]
                print("\n✅ 回复:")
                print("-" * 60)
                print(content)
                
                if "usage" in data:
                    u = data["usage"]
                    print("\n" + "-" * 60)
                    print(f"Tokens: prompt={u.get('prompt_tokens',0)}, completion={u.get('completion_tokens',0)}")
                return True
            else:
                print(f"❌ 无回复: {data}")
                return False
                
    except Exception as e:
        print(f"❌ 错误: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser(description="GLM Chat Test")
    parser.add_argument('--url', default=None, help='API URL')
    parser.add_argument('--model', default=None, help='Model name')
    parser.add_argument('--message', '-m', default="你好，请介绍一下你自己。", help='Message')
    args = parser.parse_args()
    
    # 加载配置
    config = load_config()
    
    api_url = args.url or config.api_url
    model = args.model or config.model.served_model_name
    
    success = await test_chat(api_url, model, args.message, config)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())

