#!/usr/bin/env python3
"""
测试 SAM3 API 的 base64 返回功能
"""
import os
import json
import base64
import requests
from pathlib import Path

# 配置
API_URL = "http://127.0.0.1:8000/sam3"
CONFIG_URL = "http://127.0.0.1:8000/config"
SCRIPT_DIR = Path(__file__).parent.absolute()
TEST_DATA_DIR = SCRIPT_DIR / "test_data"
OUTPUT_DIR = SCRIPT_DIR / "test_output"
SAMPLES_FILE = TEST_DATA_DIR / "samples.json"


def decode_base64_image(base64_string: str, output_path: str) -> bool:
    """
    解码 base64 图片并保存到文件
    
    Args:
        base64_string: base64 编码的图片字符串
        output_path: 输出文件路径
    
    Returns:
        是否成功保存
    """
    try:
        # 解码 base64
        image_data = base64.b64decode(base64_string)
        
        # 保存到文件
        with open(output_path, 'wb') as f:
            f.write(image_data)
        
        print(f"  ✅ 图片已保存: {output_path}")
        print(f"  文件大小: {len(image_data):,} bytes")
        return True
        
    except Exception as e:
        print(f"  ❌ 解码失败: {e}")
        return False


def test_config():
    """测试获取配置"""
    print("=" * 60)
    print("测试 1: 获取当前配置")
    print("=" * 60)
    
    try:
        response = requests.get(CONFIG_URL, timeout=5)
        if response.status_code == 200:
            config = response.json()
            print("当前配置:")
            print(f"  visualization.alpha: {config['visualization'].get('alpha')}")
            print(f"  visualization.contour_thickness: {config['visualization'].get('contour_thickness')}")
            print(f"  visualization.return_base64: {config['visualization'].get('return_base64')}")
            return True
        else:
            print(f"❌ 获取配置失败: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return False


def test_base64_return():
    """测试 base64 返回功能"""
    print("\n" + "=" * 60)
    print("测试 2: base64 返回功能")
    print("=" * 60)
    
    # 读取第一个样本
    with open(SAMPLES_FILE, 'r') as f:
        samples = json.load(f)
    
    sample = samples[0]
    filename = os.path.basename(sample["image_input_path"])
    image_path = str(TEST_DATA_DIR / filename)
    
    if not os.path.exists(image_path):
        print(f"❌ 测试图片不存在: {image_path}")
        return False
    
    print(f"测试图片: {filename}")
    print(f"Bboxes: {len(sample['bboxes'])} 个")
    
    # 发送请求，要求返回 base64
    # 不指定 alpha 和 contour_thickness，使用配置文件的默认值
    request_data = {
        "image_input_path": image_path,
        "bboxes": sample["bboxes"],
        "output_dir": str(OUTPUT_DIR),
        # alpha 和 contour_thickness 不指定，使用 config.yaml 的默认值
        "return_base64": True  # 关键参数
    }
    
    try:
        print("\n发送请求 (return_base64=true)...")
        response = requests.post(API_URL, json=request_data, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            
            print(f"\n响应结果:")
            print(f"  success: {result.get('success')}")
            print(f"  num_objects: {result.get('num_objects')}")
            print(f"  output_path: {result.get('output_path')}")
            print(f"  image_format: {result.get('image_format')}")
            
            # 检查是否返回了 base64
            if result.get("image_base64"):
                base64_str = result["image_base64"]
                print(f"  image_base64: {len(base64_str):,} 字符")
                
                # 解码并保存
                output_path = OUTPUT_DIR / f"{Path(filename).stem}_from_base64.png"
                if decode_base64_image(base64_str, str(output_path)):
                    # 验证文件
                    import cv2
                    img = cv2.imread(str(output_path))
                    if img is not None:
                        print(f"  图片尺寸: {img.shape[1]}x{img.shape[0]}")
                        return True
                    else:
                        print("  ❌ 无法读取保存的图片")
                        return False
            else:
                print("  ❌ 响应中没有 image_base64 字段!")
                return False
        else:
            print(f"❌ 请求失败: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_without_base64():
    """测试不返回 base64 的情况"""
    print("\n" + "=" * 60)
    print("测试 3: 不返回 base64 (仅返回路径)")
    print("=" * 60)
    
    # 读取第二个样本
    with open(SAMPLES_FILE, 'r') as f:
        samples = json.load(f)
    
    sample = samples[1]
    filename = os.path.basename(sample["image_input_path"])
    image_path = str(TEST_DATA_DIR / filename)
    
    if not os.path.exists(image_path):
        print(f"❌ 测试图片不存在: {image_path}")
        return False
    
    print(f"测试图片: {filename}")
    
    # 发送请求，不要求返回 base64
    request_data = {
        "image_input_path": image_path,
        "bboxes": sample["bboxes"],
        "output_dir": str(OUTPUT_DIR),
        "return_base64": False
    }
    
    try:
        print("\n发送请求 (return_base64=false)...")
        response = requests.post(API_URL, json=request_data, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            
            print(f"\n响应结果:")
            print(f"  success: {result.get('success')}")
            print(f"  num_objects: {result.get('num_objects')}")
            print(f"  output_path: {result.get('output_path')}")
            
            # 确认没有返回 base64
            if result.get("image_base64") is None:
                print("  image_base64: None (正确，未请求)")
                return True
            else:
                print(f"  ⚠️ 意外返回了 base64: {len(result.get('image_base64', '')):,} 字符")
                return False
        else:
            print(f"❌ 请求失败: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ 错误: {e}")
        return False


def main():
    print("=" * 60)
    print("SAM3 API Base64 功能测试")
    print("=" * 60)
    
    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 检查服务是否可用
    try:
        health_response = requests.get("http://127.0.0.1:8000/health", timeout=5)
        if health_response.status_code != 200:
            print("❌ 服务健康检查失败")
            return
    except:
        print("❌ 无法连接到服务，请确保服务已启动")
        print("启动命令: cd /data2/jj/proj/video_processor/sam3_api && ./start.sh")
        return
    
    print("✅ 服务已就绪\n")
    
    # 运行测试
    results = []
    
    results.append(("获取配置", test_config()))
    results.append(("Base64 返回", test_base64_return()))
    results.append(("无 Base64 返回", test_without_base64()))
    
    # 打印总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 所有测试通过!")
    else:
        print("\n⚠️ 部分测试失败")


if __name__ == "__main__":
    main()

