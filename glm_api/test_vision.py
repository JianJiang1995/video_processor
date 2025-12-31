#!/usr/bin/env python3
"""
GLM-4.6V-Flash 图像理解测试
"""
import asyncio
import httpx
import base64
import sys
import argparse
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))
from config import load_config


def encode_image(image_path: str) -> str:
    """将图片编码为base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


async def test_vision(api_url: str, model: str, image_path: str, question: str, config=None):
    """测试图像理解"""
    print("=" * 60)
    print("GLM-4.6V-Flash 图像理解测试")
    print("=" * 60)
    print(f"API: {api_url}")
    print(f"Model: {model}")
    print(f"Image: {image_path}")
    print(f"Question: {question}")
    print("-" * 60)
    
    if not Path(image_path).exists():
        print(f"❌ 图片不存在: {image_path}")
        return False
    
    # 编码图片
    image_data = encode_image(image_path)
    
    # 检测图片类型
    suffix = Path(image_path).suffix.lower()
    if suffix in ['.jpg', '.jpeg']:
        mime_type = 'image/jpeg'
    elif suffix == '.png':
        mime_type = 'image/png'
    elif suffix == '.gif':
        mime_type = 'image/gif'
    elif suffix == '.webp':
        mime_type = 'image/webp'
    else:
        mime_type = 'image/jpeg'
    
    image_url = f"data:{mime_type};base64,{image_data}"
    
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
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": question}
                ]
            }
        ],
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens
    }
    
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
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
        import traceback
        traceback.print_exc()
        return False


async def main():
    parser = argparse.ArgumentParser(description="GLM Vision Test")
    parser.add_argument('--url', default=None, help='API URL')
    parser.add_argument('--model', default=None, help='Model name')
    parser.add_argument('--image', '-i', required=True, help='Image path')
    parser.add_argument('--question', '-q', default="描述这张图片的内容。", help='Question')
    args = parser.parse_args()
    
    # 加载配置
    config = load_config()
    
    api_url = args.url or config.api_url
    model = args.model or config.model.served_model_name
    
    success = await test_vision(api_url, model, args.image, args.question, config)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())

