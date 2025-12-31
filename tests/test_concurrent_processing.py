#!/usr/bin/env python3
"""
Test Concurrent Processing for SurgR1 and GLM
测试 SurgR1 和 GLM 的并发处理功能

测试内容:
1. SurgR1 并发帧分析 - analyze_frames_concurrent
2. GLM 并发窗口摘要 - summarize_windows_concurrent
3. 任务队列 - AsyncTaskQueue
4. 性能对比 - 串行 vs 并发
"""
import os
import sys
import asyncio
import time
import json
import requests
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "video_stream_app" / "backend"))

from PIL import Image
import io
import base64

# Configuration
SURGR1_API_URL = "http://localhost:9003"
GLM_API_URL = "http://localhost:8000/v1"
BACKEND_API_URL = "http://localhost:8001"

# Test data paths
JSONL_PATH = "/data/jj/proj/Laparo/data_json/renji/first_1000_quest_merged.jsonl"
OUTPUT_DIR = Path("/data2/jj/proj/video_processor/tests/outputs_concurrent")


@dataclass
class TestResult:
    """测试结果"""
    name: str
    success: bool
    elapsed_seconds: float
    items_processed: int
    fps: float = 0.0
    details: Dict = field(default_factory=dict)
    error: Optional[str] = None


def check_services():
    """检查所有服务是否可用"""
    services = {
        "SurgR1": f"{SURGR1_API_URL}/health",
        "GLM": f"{GLM_API_URL}/models",
        "Backend": f"{BACKEND_API_URL}/api/health"
    }
    
    results = {}
    print("\n" + "="*60)
    print("服务状态检查")
    print("="*60)
    
    for name, url in services.items():
        try:
            resp = requests.get(url, timeout=10)
            available = resp.status_code == 200
            results[name] = available
            status = "✓ 可用" if available else "✗ 不可用"
            print(f"  {name}: {status}")
        except Exception as e:
            results[name] = False
            print(f"  {name}: ✗ 错误 - {e}")
    
    return results


def load_test_images(num_images: int = 10) -> List[str]:
    """从 JSONL 加载测试图片路径"""
    images = []
    
    if not os.path.exists(JSONL_PATH):
        print(f"JSONL 文件不存在: {JSONL_PATH}")
        return images
    
    with open(JSONL_PATH, 'r') as f:
        for line in f:
            if len(images) >= num_images:
                break
            try:
                data = json.loads(line)
                for img in data.get('images', []):
                    if os.path.exists(img):
                        images.append(img)
                        if len(images) >= num_images:
                            break
            except:
                continue
    
    return images


def image_to_base64(image_path: str) -> str:
    """将图片转换为 base64"""
    with open(image_path, 'rb') as f:
        return base64.b64encode(f.read()).decode()


# ==================== 测试 1: SurgR1 串行 vs 并发 ====================

def test_surgr1_serial(image_paths: List[str]) -> TestResult:
    """测试 SurgR1 串行分析"""
    print("\n" + "-"*60)
    print("测试 1a: SurgR1 串行分析")
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
            print(f"  [{i+1}/{len(image_paths)}] 完成")
        except Exception as e:
            print(f"  [{i+1}/{len(image_paths)}] 失败: {e}")
    
    elapsed = time.time() - start_time
    fps = len(image_paths) / elapsed if elapsed > 0 else 0
    
    print(f"\n  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {fps:.2f} fps")
    
    return TestResult(
        name="SurgR1 串行分析",
        success=len(results) == len(image_paths),
        elapsed_seconds=elapsed,
        items_processed=len(results),
        fps=fps
    )


def test_surgr1_batch(image_paths: List[str]) -> TestResult:
    """测试 SurgR1 批量分析（API 原生批量）"""
    print("\n" + "-"*60)
    print("测试 1b: SurgR1 批量分析")
    print("-"*60)
    
    start_time = time.time()
    
    try:
        resp = requests.post(
            f"{SURGR1_API_URL}/analyze",
            json={"image_paths": image_paths},
            timeout=120
        )
        resp.raise_for_status()
        result = resp.json()
        results = result.get("results", [])
        success = True
    except Exception as e:
        print(f"  失败: {e}")
        results = []
        success = False
    
    elapsed = time.time() - start_time
    fps = len(image_paths) / elapsed if elapsed > 0 else 0
    
    print(f"  处理 {len(results)} 张图片")
    print(f"  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {fps:.2f} fps")
    
    return TestResult(
        name="SurgR1 批量分析",
        success=success,
        elapsed_seconds=elapsed,
        items_processed=len(results),
        fps=fps
    )


def test_surgr1_concurrent_backend(image_paths: List[str], max_concurrent: int = 3) -> TestResult:
    """测试后端并发分析 API"""
    print("\n" + "-"*60)
    print(f"测试 1c: 后端并发分析 (max_concurrent={max_concurrent})")
    print("-"*60)
    
    # 准备帧数据
    frames = []
    for i, path in enumerate(image_paths):
        frames.append({
            "frame_idx": i,
            "timestamp": i * 1.0,
            "image_base64": image_to_base64(path)
        })
    
    start_time = time.time()
    
    try:
        resp = requests.post(
            f"{BACKEND_API_URL}/api/analysis/analyze-frames-concurrent",
            json={
                "session_id": "test_concurrent",
                "frames": frames,
                "max_concurrent": max_concurrent
            },
            timeout=180
        )
        resp.raise_for_status()
        result = resp.json()
        results = result.get("results", [])
        success = result.get("success", False)
        
        print(f"  处理 {len(results)} 帧")
        print(f"  成功: {result.get('success_count', 0)}")
        print(f"  API 返回 FPS: {result.get('fps', 0)}")
        
    except Exception as e:
        print(f"  失败: {e}")
        results = []
        success = False
    
    elapsed = time.time() - start_time
    fps = len(image_paths) / elapsed if elapsed > 0 else 0
    
    print(f"  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {fps:.2f} fps")
    
    return TestResult(
        name=f"后端并发分析 (n={max_concurrent})",
        success=success,
        elapsed_seconds=elapsed,
        items_processed=len(results),
        fps=fps
    )


# ==================== 测试 2: GLM 并发摘要 ====================

def test_glm_serial(num_windows: int = 5) -> TestResult:
    """测试 GLM 串行摘要"""
    print("\n" + "-"*60)
    print("测试 2a: GLM 串行摘要")
    print("-"*60)
    
    # 模拟帧分析结果
    mock_frame_analyses = [
        {"phase": "CalotTriangleDissection", "action": "Dissecting tissue", "tools": "Grasper, Hook"},
        {"phase": "CalotTriangleDissection", "action": "Exposing cystic duct", "tools": "Grasper"},
        {"phase": "CalotTriangleDissection", "action": "Clearing Calot triangle", "tools": "Hook"},
    ]
    
    start_time = time.time()
    results = []
    
    for i in range(num_windows):
        try:
            resp = requests.post(
                f"{GLM_API_URL}/chat/completions",
                json={
                    "model": "GLM-4.6V-Flash",
                    "messages": [
                        {"role": "system", "content": "请直接回答问题，不需要思考过程。"},
                        {"role": "user", "content": f"请用中文总结这个手术窗口：{mock_frame_analyses}"}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 500
                },
                timeout=60
            )
            resp.raise_for_status()
            results.append(resp.json())
            print(f"  [窗口 {i+1}/{num_windows}] 完成")
        except Exception as e:
            print(f"  [窗口 {i+1}/{num_windows}] 失败: {e}")
    
    elapsed = time.time() - start_time
    wps = num_windows / elapsed if elapsed > 0 else 0
    
    print(f"\n  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {wps:.2f} 窗口/秒")
    
    return TestResult(
        name="GLM 串行摘要",
        success=len(results) == num_windows,
        elapsed_seconds=elapsed,
        items_processed=len(results),
        fps=wps
    )


async def test_glm_concurrent_async(num_windows: int = 5, max_concurrent: int = 3) -> TestResult:
    """测试 GLM 并发摘要（使用 asyncio）"""
    print("\n" + "-"*60)
    print(f"测试 2b: GLM 并发摘要 (max_concurrent={max_concurrent})")
    print("-"*60)
    
    import aiohttp
    
    mock_frame_analyses = [
        {"phase": "CalotTriangleDissection", "action": "Dissecting tissue", "tools": "Grasper, Hook"},
        {"phase": "CalotTriangleDissection", "action": "Exposing cystic duct", "tools": "Grasper"},
    ]
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def summarize_window(session, window_id: int):
        async with semaphore:
            try:
                async with session.post(
                    f"{GLM_API_URL}/chat/completions",
                    json={
                        "model": "GLM-4.6V-Flash",
                        "messages": [
                            {"role": "system", "content": "请直接回答问题，不需要思考过程。"},
                            {"role": "user", "content": f"请用中文总结这个手术窗口 {window_id}：{mock_frame_analyses}"}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 500
                    },
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        return window_id, await resp.json(), None
                    return window_id, None, f"Status {resp.status}"
            except Exception as e:
                return window_id, None, str(e)
    
    start_time = time.time()
    results = []
    
    async with aiohttp.ClientSession() as session:
        tasks = [summarize_window(session, i) for i in range(num_windows)]
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        
        for item in completed:
            if isinstance(item, Exception):
                print(f"  异常: {item}")
            else:
                window_id, result, error = item
                if result:
                    results.append(result)
                    print(f"  [窗口 {window_id}] 完成")
                else:
                    print(f"  [窗口 {window_id}] 失败: {error}")
    
    elapsed = time.time() - start_time
    wps = num_windows / elapsed if elapsed > 0 else 0
    
    print(f"\n  总耗时: {elapsed:.2f}s")
    print(f"  处理速度: {wps:.2f} 窗口/秒")
    
    return TestResult(
        name=f"GLM 并发摘要 (n={max_concurrent})",
        success=len(results) == num_windows,
        elapsed_seconds=elapsed,
        items_processed=len(results),
        fps=wps
    )


# ==================== 测试 3: 队列状态 ====================

def test_queue_status() -> TestResult:
    """测试队列状态获取"""
    print("\n" + "-"*60)
    print("测试 3: 队列状态")
    print("-"*60)
    
    try:
        resp = requests.get(f"{BACKEND_API_URL}/api/analysis/queue-status", timeout=10)
        resp.raise_for_status()
        result = resp.json()
        
        print(f"  SurgR1 队列: {result.get('surgr1_queue', {})}")
        print(f"  GLM 队列: {result.get('glm_queue', {})}")
        
        return TestResult(
            name="队列状态",
            success=result.get("success", False),
            elapsed_seconds=0,
            items_processed=1,
            details=result
        )
    except Exception as e:
        print(f"  失败: {e}")
        return TestResult(
            name="队列状态",
            success=False,
            elapsed_seconds=0,
            items_processed=0,
            error=str(e)
        )


# ==================== 测试 4: 配置并发数 ====================

def test_queue_config(surgr1_concurrent: int = 5, glm_concurrent: int = 3) -> TestResult:
    """测试配置队列并发数"""
    print("\n" + "-"*60)
    print(f"测试 4: 配置并发数 (SurgR1={surgr1_concurrent}, GLM={glm_concurrent})")
    print("-"*60)
    
    try:
        resp = requests.post(
            f"{BACKEND_API_URL}/api/analysis/queue-config",
            json={
                "surgr1_max_concurrent": surgr1_concurrent,
                "glm_max_concurrent": glm_concurrent
            },
            timeout=10
        )
        resp.raise_for_status()
        result = resp.json()
        
        print(f"  结果: {result}")
        
        return TestResult(
            name="配置并发数",
            success=result.get("success", False),
            elapsed_seconds=0,
            items_processed=1,
            details=result
        )
    except Exception as e:
        print(f"  失败: {e}")
        return TestResult(
            name="配置并发数",
            success=False,
            elapsed_seconds=0,
            items_processed=0,
            error=str(e)
        )


# ==================== 主测试函数 ====================

def run_performance_comparison(image_paths: List[str]):
    """运行性能对比测试"""
    print("\n" + "="*60)
    print("性能对比测试")
    print("="*60)
    
    results = []
    
    # 1. SurgR1 串行
    result = test_surgr1_serial(image_paths[:5])
    results.append(result)
    
    # 2. SurgR1 批量
    result = test_surgr1_batch(image_paths)
    results.append(result)
    
    # 3. 后端并发（不同并发数）
    for n in [2, 3, 5]:
        result = test_surgr1_concurrent_backend(image_paths, max_concurrent=n)
        results.append(result)
    
    return results


async def run_glm_comparison(num_windows: int = 5):
    """运行 GLM 性能对比"""
    print("\n" + "="*60)
    print("GLM 性能对比测试")
    print("="*60)
    
    results = []
    
    # 1. GLM 串行
    result = test_glm_serial(num_windows)
    results.append(result)
    
    # 2. GLM 并发（不同并发数）
    for n in [2, 3]:
        result = await test_glm_concurrent_async(num_windows, max_concurrent=n)
        results.append(result)
    
    return results


def print_summary(results: List[TestResult]):
    """打印测试总结"""
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    print(f"\n{'测试名称':<35} {'状态':<8} {'耗时':<10} {'处理数':<8} {'速度':<10}")
    print("-"*75)
    
    for r in results:
        status = "✓ 成功" if r.success else "✗ 失败"
        elapsed = f"{r.elapsed_seconds:.2f}s" if r.elapsed_seconds > 0 else "-"
        fps = f"{r.fps:.2f}/s" if r.fps > 0 else "-"
        print(f"{r.name:<35} {status:<8} {elapsed:<10} {r.items_processed:<8} {fps:<10}")
    
    # 计算加速比
    print("\n" + "-"*60)
    print("加速比分析")
    print("-"*60)
    
    serial_results = [r for r in results if "串行" in r.name]
    concurrent_results = [r for r in results if "并发" in r.name or "批量" in r.name]
    
    for serial in serial_results:
        if serial.elapsed_seconds > 0:
            for concurrent in concurrent_results:
                if concurrent.elapsed_seconds > 0:
                    speedup = serial.elapsed_seconds / concurrent.elapsed_seconds
                    print(f"  {concurrent.name} vs {serial.name}: {speedup:.2f}x 加速")


def main():
    """主函数"""
    print("="*60)
    print("并发处理测试")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # 创建输出目录
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 检查服务
    service_status = check_services()
    
    all_results = []
    
    # 测试队列状态
    result = test_queue_status()
    all_results.append(result)
    
    # 测试配置并发数
    result = test_queue_config(5, 3)
    all_results.append(result)
    
    # 如果 SurgR1 可用，运行性能对比
    if service_status.get("SurgR1"):
        print("\n加载测试图片...")
        image_paths = load_test_images(10)
        print(f"加载了 {len(image_paths)} 张图片")
        
        if image_paths:
            surgr1_results = run_performance_comparison(image_paths)
            all_results.extend(surgr1_results)
    else:
        print("\n⚠️ SurgR1 服务不可用，跳过 SurgR1 测试")
    
    # 如果 GLM 可用，运行 GLM 测试
    if service_status.get("GLM"):
        glm_results = asyncio.run(run_glm_comparison(5))
        all_results.extend(glm_results)
    else:
        print("\n⚠️ GLM 服务不可用，跳过 GLM 测试")
    
    # 打印总结
    print_summary(all_results)
    
    # 保存结果
    summary = {
        "timestamp": datetime.now().isoformat(),
        "service_status": service_status,
        "results": [
            {
                "name": r.name,
                "success": r.success,
                "elapsed_seconds": r.elapsed_seconds,
                "items_processed": r.items_processed,
                "fps": r.fps,
                "error": r.error
            }
            for r in all_results
        ]
    }
    
    summary_path = OUTPUT_DIR / "test_results.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"\n结果已保存到: {summary_path}")


if __name__ == "__main__":
    main()



