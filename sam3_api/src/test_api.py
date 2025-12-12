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
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置
API_URL = "http://127.0.0.1:8000/sam3"
TEST_DATA_DIR = "./test_data"
OUTPUT_DIR = "./test_output"
SAMPLES_FILE = os.path.join(TEST_DATA_DIR, "samples.json")

# 并发数（设为1以便更好地观察结果）
MAX_WORKERS = 1

def test_single_sample(sample, idx):
    """测试单个样本"""
    try:
        request_data = {
            "image_input_path": sample["image_input_path"],
            "bboxes": sample["bboxes"],
            "output_dir": os.path.abspath(OUTPUT_DIR)
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
    # 检查测试数据
    if not os.path.exists(SAMPLES_FILE):
        print(f"错误: 样本文件不存在: {SAMPLES_FILE}")
        print("请先运行 sample_data.py 生成测试数据")
        return
    
    # 读取样本
    with open(SAMPLES_FILE, 'r') as f:
        samples = json.load(f)
    
    print(f"读取 {len(samples)} 个测试样本")
    
    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 检查服务是否可用
    try:
        health_response = requests.get("http://127.0.0.1:8000/health", timeout=5)
        if health_response.status_code != 200:
            print("警告: 服务健康检查失败")
    except:
        print("错误: 无法连接到服务，请确保服务已启动")
        print("启动命令: cd fastapi && python main.py")
        return
    
    print(f"服务已就绪，开始测试...")
    print(f"结果将保存到: {OUTPUT_DIR}")
    print("-" * 60)
    
    # 测试所有样本
    results = []
    success_count = 0
    fail_count = 0
    total_time = 0
    
    for idx, sample in enumerate(samples):
        result = test_single_sample(sample, idx)
        results.append(result)
        
        if result["success"]:
            success_count += 1
            total_time += result["elapsed"]
            print(f"[{idx+1:3d}/{len(samples)}] ✅ 成功 | "
                  f"耗时: {result['elapsed']:.2f}s | "
                  f"检测: {result['num_objects']}个对象")
        else:
            fail_count += 1
            print(f"[{idx+1:3d}/{len(samples)}] ❌ 失败 | 错误: {result['error']}")
    
    # 保存测试结果
    results_file = os.path.join(OUTPUT_DIR, "test_results.json")
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    # 打印统计
    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)
    print(f"成功: {success_count}/{len(samples)} ({100*success_count/len(samples):.1f}%)")
    print(f"失败: {fail_count}/{len(samples)}")
    if success_count > 0:
        print(f"平均耗时: {total_time/success_count:.2f}s")
    print(f"\n结果保存到: {OUTPUT_DIR}")
    print(f"详细结果: {results_file}")


if __name__ == "__main__":
    main()

