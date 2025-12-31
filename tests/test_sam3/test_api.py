#!/usr/bin/env python3
"""
测试SAM3 FastAPI服务的脚本
读取test_data中的样本，发送请求并保存结果到test_output
"""
import os
import json
import time
import requests
from pathlib import Path

# 配置
API_URL = "http://127.0.0.1:8000/sam3"
SCRIPT_DIR = Path(__file__).parent.absolute()
TEST_DATA_DIR = SCRIPT_DIR / "test_data"
OUTPUT_DIR = SCRIPT_DIR / "test_output"
SAMPLES_FILE = TEST_DATA_DIR / "samples.json"

# 并发数（设为1以便更好地观察结果）
MAX_WORKERS = 1

# 可视化参数
ALPHA = 0.4  # mask透明度 (0.0-1.0)
CONTOUR_THICKNESS = 2  # 边缘粗细


def fix_image_path(old_path: str) -> str:
    """修复图片路径，将旧路径转换为当前test_data目录下的路径"""
    # 提取文件名
    filename = os.path.basename(old_path)
    # 返回当前test_data目录下的路径
    new_path = TEST_DATA_DIR / filename
    return str(new_path)


def test_single_sample(sample, idx):
    """测试单个样本"""
    try:
        # 修复图片路径
        image_path = fix_image_path(sample["image_input_path"])
        
        # 检查图片是否存在
        if not os.path.exists(image_path):
            return {
                "idx": idx,
                "success": False,
                "elapsed": -1,
                "error": f"图片不存在: {image_path}"
            }
        
        request_data = {
            "image_input_path": image_path,
            "bboxes": sample["bboxes"],
            "output_dir": str(OUTPUT_DIR),
            "alpha": ALPHA,
            "contour_thickness": CONTOUR_THICKNESS
        }
        
        start_time = time.time()
        response = requests.post(API_URL, json=request_data, timeout=120)
        elapsed = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            return {
                "idx": idx,
                "success": True,
                "elapsed": elapsed,
                "output_path": result.get("output_path"),
                "num_objects": result.get("num_objects"),
                "masks": result.get("masks", [])
            }
        else:
            return {
                "idx": idx,
                "success": False,
                "elapsed": elapsed,
                "error": f"HTTP {response.status_code}: {response.text}"
            }
            
    except requests.exceptions.Timeout:
        return {
            "idx": idx,
            "success": False,
            "elapsed": -1,
            "error": "请求超时"
        }
    except requests.exceptions.ConnectionError:
        return {
            "idx": idx,
            "success": False,
            "elapsed": -1,
            "error": "连接失败，请确保服务已启动"
        }
    except Exception as e:
        return {
            "idx": idx,
            "success": False,
            "elapsed": -1,
            "error": str(e)
        }


def main():
    print("=" * 60)
    print("SAM3 API 测试脚本")
    print("=" * 60)
    print(f"测试数据目录: {TEST_DATA_DIR}")
    print(f"输出目录: {OUTPUT_DIR}")
    print(f"API地址: {API_URL}")
    print(f"透明度(alpha): {ALPHA}")
    print(f"边缘粗细: {CONTOUR_THICKNESS}")
    print("=" * 60)
    
    # 检查测试数据
    if not SAMPLES_FILE.exists():
        print(f"错误: 样本文件不存在: {SAMPLES_FILE}")
        return
    
    # 读取样本
    with open(SAMPLES_FILE, 'r') as f:
        samples = json.load(f)
    
    print(f"读取 {len(samples)} 个测试样本")
    
    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 检查服务是否可用
    try:
        health_response = requests.get("http://127.0.0.1:8000/health", timeout=5)
        if health_response.status_code != 200:
            print("警告: 服务健康检查失败")
    except:
        print("错误: 无法连接到服务，请确保服务已启动")
        print("启动命令: cd /data2/jj/proj/video_processor/sam3_api && ./start.sh")
        return
    
    print(f"服务已就绪，开始测试...")
    print("-" * 60)
    
    # 统计有多少图片实际存在
    available_samples = []
    for idx, sample in enumerate(samples):
        image_path = fix_image_path(sample["image_input_path"])
        if os.path.exists(image_path):
            available_samples.append((idx, sample))
    
    print(f"可用样本: {len(available_samples)}/{len(samples)}")
    print("-" * 60)
    
    if not available_samples:
        print("错误: 没有可用的测试图片!")
        print(f"请确保测试图片存在于: {TEST_DATA_DIR}")
        return
    
    # 测试所有样本
    results = []
    success_count = 0
    fail_count = 0
    total_time = 0
    
    for i, (idx, sample) in enumerate(available_samples):
        result = test_single_sample(sample, idx)
        results.append(result)
        
        if result["success"]:
            success_count += 1
            total_time += result["elapsed"]
            print(f"[{i+1:3d}/{len(available_samples)}] ✅ 成功 | "
                  f"耗时: {result['elapsed']:.2f}s | "
                  f"检测: {result['num_objects']}个对象")
        else:
            fail_count += 1
            print(f"[{i+1:3d}/{len(available_samples)}] ❌ 失败 | 错误: {result['error']}")
    
    # 保存测试结果
    results_file = OUTPUT_DIR / "test_results.json"
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    # 打印统计
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)
    print(f"成功: {success_count}/{len(available_samples)} ({100*success_count/len(available_samples):.1f}%)")
    print(f"失败: {fail_count}/{len(available_samples)}")
    if success_count > 0:
        print(f"平均耗时: {total_time/success_count:.2f}s")
    print(f"\n结果保存到: {OUTPUT_DIR}")
    print(f"详细结果: {results_file}")


if __name__ == "__main__":
    main()
