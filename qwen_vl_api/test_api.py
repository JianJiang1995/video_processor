#!/usr/bin/env python3
"""
Qwen3-VL API 测试脚本
测试文本和图片输入
"""
import httpx
import base64
import asyncio
from pathlib import Path

API_URL = "http://localhost:8000/v1"
MODEL_NAME = "Qwen3-VL-8B"


async def test_text_only():
    """测试纯文本对话"""
    print("=" * 50)
    print("测试1: 纯文本对话")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{API_URL}/chat/completions",
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": "你是一个腹腔镜手术分析专家。"},
                    {"role": "user", "content": "请简单介绍腹腔镜胆囊切除术的主要步骤。"}
                ],
                "max_tokens": 500,
                "temperature": 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            print(f"✅ 成功! Tokens: {tokens}")
            print(f"回复: {text[:200]}...")
        else:
            print(f"❌ 失败: {response.status_code}")
            print(response.text)


async def test_image_input():
    """测试图片输入"""
    print("\n" + "=" * 50)
    print("测试2: 图片分析")
    print("=" * 50)
    
    # 查找测试图片
    test_images = list(Path("/data2/jj/proj/video_processor").rglob("*.jpg"))[:1]
    if not test_images:
        test_images = list(Path("/data2/jj/proj/video_processor").rglob("*.png"))[:1]
    
    if not test_images:
        print("⚠️ 未找到测试图片，跳过图片测试")
        return
    
    image_path = test_images[0]
    print(f"使用测试图片: {image_path}")
    
    # 读取并编码图片
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()
    
    # 判断图片类型
    suffix = image_path.suffix.lower()
    if suffix in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"
    elif suffix == ".png":
        mime_type = "image/png"
    else:
        mime_type = "image/jpeg"
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{API_URL}/chat/completions",
            json={
                "model": MODEL_NAME,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}",
                                    "detail": "low"
                                }
                            },
                            {
                                "type": "text",
                                "text": "请描述这张图片的内容。"
                            }
                        ]
                    }
                ],
                "max_tokens": 500,
                "temperature": 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            print(f"✅ 成功! Tokens: {tokens}")
            print(f"回复: {text[:300]}...")
        else:
            print(f"❌ 失败: {response.status_code}")
            print(response.text)


async def test_health():
    """测试服务健康状态"""
    print("=" * 50)
    print("测试0: 服务健康检查")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{API_URL}/models")
            if response.status_code == 200:
                data = response.json()
                models = [m["id"] for m in data.get("data", [])]
                print(f"✅ 服务正常，可用模型: {models}")
                return True
            else:
                print(f"❌ 服务异常: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ 无法连接服务: {e}")
            return False


async def main():
    print("\n" + "=" * 60)
    print("  Qwen3-VL-8B API 测试")
    print("=" * 60 + "\n")
    
    if not await test_health():
        print("\n请先启动 Qwen3-VL 服务:")
        print("  bash /data2/jj/proj/video_processor/qwen_vl_api/start.sh")
        return
    
    await test_text_only()
    await test_image_input()
    
    print("\n" + "=" * 50)
    print("测试完成!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())

