#!/usr/bin/env python3
"""
测试动态批处理功能
Test Dynamic Batch Processing for SurgR1

测试内容:
1. 帧缓冲区基本功能
2. 动态 batch size 调整
3. 与 SurgR1 API 的集成
4. 性能对比：串行 vs 批量
"""
import asyncio
import sys
import time
import json
import requests
from pathlib import Path
from typing import List, Dict

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "video_stream_app" / "backend"))

SURGR1_API_URL = "http://localhost:9003"
JSONL_PATH = "/data/jj/proj/Laparo/data_json/renji/first_1000_quest_merged.jsonl"


def load_test_images(num_images: int = 10) -> List[str]:
    """从 JSONL 加载测试图片路径"""
    images = []
    
    if not Path(JSONL_PATH).exists():
        print(f"⚠️ JSONL 文件不存在: {JSONL_PATH}")
        return images
    
    with open(JSONL_PATH, 'r') as f:
        for line in f:
            if len(images) >= num_images:
                break
            try:
                data = json.loads(line)
                for img in data.get('images', []):
                    if Path(img).exists():
                        images.append(img)
                        if len(images) >= num_images:
                            break
            except:
                continue
    
    return images


def test_surgr1_health():
    """检查 SurgR1 服务"""
    print("\n" + "="*60)
    print("检查 SurgR1 服务")
    print("="*60)
    
    try:
        resp = requests.get(f"{SURGR1_API_URL}/health", timeout=10)
        data = resp.json()
        print(f"✓ SurgR1 服务正常")
        print(f"  状态: {data.get('status')}")
        print(f"  模型: {data.get('model_path', 'N/A')[-50:]}")
        return True
    except Exception as e:
        print(f"✗ SurgR1 服务不可用: {e}")
        return False


def test_serial_processing(image_paths: List[str]) -> Dict:
    """测试串行处理（每次 1 张图片）"""
    print("\n" + "-"*60)
    print(f"测试 1: 串行处理 ({len(image_paths)} 张图片)")
    print("-"*60)
    
    start_time = time.time()
    results = []
    
    for i, path in enumerate(image_paths):
        try:
            resp = requests.post(
                f"{SURGR1_API_URL}/analyze",
                json={"image_paths": [path]},
                timeout=60
            )
            resp.raise_for_status()
            results.append(resp.json())
            print(f"  [{i+1}/{len(image_paths)}] ✓")
        except Exception as e:
            print(f"  [{i+1}/{len(image_paths)}] ✗ {e}")
    
    elapsed = time.time() - start_time
    fps = len(image_paths) / elapsed if elapsed > 0 else 0
    
    print(f"\n  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {fps:.2f} fps")
    print(f"  每帧耗时: {elapsed/len(image_paths):.2f}s")
    
    return {
        "method": "serial",
        "count": len(image_paths),
        "elapsed": elapsed,
        "fps": fps
    }


def test_batch_processing(image_paths: List[str], batch_sizes: List[int] = [2, 5, 10]) -> List[Dict]:
    """测试不同 batch size 的批处理"""
    results = []
    
    for batch_size in batch_sizes:
        if batch_size > len(image_paths):
            continue
            
        print(f"\n" + "-"*60)
        print(f"测试 2: 批处理 (batch_size={batch_size})")
        print("-"*60)
        
        start_time = time.time()
        processed = 0
        batch_count = 0
        
        for i in range(0, len(image_paths), batch_size):
            batch = image_paths[i:i+batch_size]
            try:
                resp = requests.post(
                    f"{SURGR1_API_URL}/analyze",
                    json={"image_paths": batch},
                    timeout=120
                )
                resp.raise_for_status()
                data = resp.json()
                batch_count += 1
                processed += len(data.get("results", []))
                print(f"  批次 {batch_count}: {len(batch)} 张 ✓")
            except Exception as e:
                print(f"  批次 {batch_count}: ✗ {e}")
        
        elapsed = time.time() - start_time
        fps = processed / elapsed if elapsed > 0 else 0
        
        print(f"\n  总耗时: {elapsed:.2f}s")
        print(f"  处理速度: {fps:.2f} fps")
        print(f"  批次数: {batch_count}")
        
        results.append({
            "method": f"batch_{batch_size}",
            "batch_size": batch_size,
            "count": processed,
            "elapsed": elapsed,
            "fps": fps
        })
    
    return results


async def test_frame_buffer_integration():
    """测试帧缓冲区与批处理集成"""
    print("\n" + "-"*60)
    print("测试 3: 帧缓冲区动态批处理")
    print("-"*60)
    
    from services.frame_buffer_service import get_frame_buffer, BufferConfig
    from PIL import Image
    
    # 加载图片
    image_paths = load_test_images(10)
    if not image_paths:
        print("⚠️ 没有可用的测试图片")
        return None
    
    # 创建缓冲区
    buffer = get_frame_buffer("test_dynamic")
    print(f"✓ 创建缓冲区: batch_size={buffer.config.min_batch_size}-{buffer.config.max_batch_size}")
    
    # 模拟视频流：快速添加帧
    print(f"\n添加 {len(image_paths)} 帧到缓冲区...")
    for i, path in enumerate(image_paths):
        img = Image.open(path)
        buffer.add_frame(
            image=img,
            frame_idx=i,
            timestamp=i * 1.0
        )
    
    print(f"  待处理: {buffer.stats.pending_count}")
    print(f"  当前批大小: {buffer.stats.current_batch_size}")
    
    # 获取并处理批次
    start_time = time.time()
    total_processed = 0
    batch_count = 0
    
    while True:
        batch = await buffer.get_batch(wait=False)
        if not batch:
            break
        
        batch_count += 1
        batch_size = len(batch)
        
        # 准备图片路径（这里用原始路径代替）
        batch_paths = image_paths[total_processed:total_processed+batch_size]
        
        try:
            batch_start = time.time()
            resp = requests.post(
                f"{SURGR1_API_URL}/analyze",
                json={"image_paths": batch_paths},
                timeout=120
            )
            resp.raise_for_status()
            batch_elapsed = time.time() - batch_start
            
            buffer.record_processing_time(batch_elapsed, batch_size)
            total_processed += batch_size
            
            print(f"  批次 {batch_count}: {batch_size} 张, {batch_elapsed:.2f}s")
        except Exception as e:
            print(f"  批次 {batch_count}: ✗ {e}")
            total_processed += batch_size
    
    elapsed = time.time() - start_time
    fps = total_processed / elapsed if elapsed > 0 else 0
    
    print(f"\n✓ 动态批处理完成")
    print(f"  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {fps:.2f} fps")
    print(f"  平均批大小: {total_processed/batch_count:.1f}" if batch_count > 0 else "N/A")
    
    # 清理
    buffer.clear()
    
    return {
        "method": "dynamic_buffer",
        "count": total_processed,
        "elapsed": elapsed,
        "fps": fps,
        "batch_count": batch_count
    }


def print_comparison(results: List[Dict]):
    """打印性能对比"""
    print("\n" + "="*60)
    print("性能对比")
    print("="*60)
    
    print(f"\n{'方法':<20} {'耗时':<10} {'FPS':<10} {'加速比':<10}")
    print("-"*50)
    
    baseline = None
    for r in results:
        if r["method"] == "serial":
            baseline = r["elapsed"]
    
    for r in results:
        speedup = baseline / r["elapsed"] if baseline and r["elapsed"] > 0 else 1.0
        print(f"{r['method']:<20} {r['elapsed']:.2f}s     {r['fps']:.2f}      {speedup:.2f}x")


def main():
    print("="*60)
    print("动态批处理测试")
    print("="*60)
    
    # 检查服务
    if not test_surgr1_health():
        print("\n⚠️ SurgR1 服务不可用，跳过集成测试")
        return
    
    # 加载测试图片
    image_paths = load_test_images(10)
    if not image_paths:
        print("\n⚠️ 没有可用的测试图片")
        return
    
    print(f"\n加载了 {len(image_paths)} 张测试图片")
    
    all_results = []
    
    # 测试 1: 串行处理
    result = test_serial_processing(image_paths[:5])  # 只用 5 张避免太慢
    all_results.append(result)
    
    # 测试 2: 批处理
    batch_results = test_batch_processing(image_paths, batch_sizes=[2, 5, 10])
    all_results.extend(batch_results)
    
    # 测试 3: 动态缓冲区
    dynamic_result = asyncio.run(test_frame_buffer_integration())
    if dynamic_result:
        all_results.append(dynamic_result)
    
    # 对比结果
    print_comparison(all_results)


if __name__ == "__main__":
    main()





