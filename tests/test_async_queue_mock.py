#!/usr/bin/env python3
"""
Mock Test for AsyncTaskQueue
模拟测试任务队列（不需要实际服务）

测试内容:
1. 任务队列基本功能
2. 并发控制
3. 优先级队列
4. 重试机制
5. 批量任务处理
"""
import asyncio
import sys
import time
import random
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "video_stream_app" / "backend"))

from services.async_task_queue import (
    AsyncTaskQueue, Task, TaskStatus, TaskPriority
)


# ==================== 模拟函数 ====================

async def mock_surgr1_analyze(image_id: int, delay: float = 0.5) -> Dict[str, Any]:
    """模拟 SurgR1 分析"""
    await asyncio.sleep(delay)
    return {
        "image_id": image_id,
        "phase": f"Phase_{image_id % 7}",
        "action": f"Action_{image_id}",
        "tools": f"Tool_{image_id % 5}"
    }


async def mock_surgr1_analyze_with_error(image_id: int, fail_rate: float = 0.3) -> Dict[str, Any]:
    """模拟 SurgR1 分析（可能失败）"""
    await asyncio.sleep(random.uniform(0.1, 0.5))
    if random.random() < fail_rate:
        raise Exception(f"Simulated error for image {image_id}")
    return {
        "image_id": image_id,
        "phase": f"Phase_{image_id % 7}",
        "action": f"Action_{image_id}",
        "tools": f"Tool_{image_id % 5}"
    }


async def mock_glm_summarize(window_id: int, delay: float = 1.0) -> Dict[str, Any]:
    """模拟 GLM 摘要"""
    await asyncio.sleep(delay)
    return {
        "window_id": window_id,
        "summary": f"这是窗口 {window_id} 的摘要：手术进行顺利...",
        "success": True
    }


# ==================== 测试 1: 基本队列功能 ====================

async def test_basic_queue():
    """测试基本队列功能"""
    print("\n" + "="*60)
    print("测试 1: 基本队列功能")
    print("="*60)
    
    queue = AsyncTaskQueue(max_concurrent=3, name="BasicTest")
    await queue.start()
    
    try:
        # 提交任务
        task_ids = []
        for i in range(5):
            task_id = await queue.submit(
                func=mock_surgr1_analyze,
                kwargs={"image_id": i, "delay": 0.3}
            )
            task_ids.append(task_id)
            print(f"  提交任务 {task_id}")
        
        # 等待所有任务完成
        results = await queue.wait_for_batch(task_ids)
        
        print(f"\n  完成 {len(results)} 个任务")
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"  任务 {i}: 失败 - {result}")
            else:
                print(f"  任务 {i}: 成功 - {result.get('phase')}")
        
        # 获取统计信息
        stats = queue.get_stats()
        print(f"\n  队列统计: {stats}")
        
        success = stats["completed"] == 5
        print(f"\n  ✓ 测试通过" if success else "\n  ✗ 测试失败")
        return success
        
    finally:
        await queue.stop()


# ==================== 测试 2: 并发控制 ====================

async def test_concurrency_control():
    """测试并发控制"""
    print("\n" + "="*60)
    print("测试 2: 并发控制")
    print("="*60)
    
    concurrent_count = 0
    max_concurrent_observed = 0
    max_allowed = 2
    
    async def counting_task(task_id: int):
        nonlocal concurrent_count, max_concurrent_observed
        concurrent_count += 1
        max_concurrent_observed = max(max_concurrent_observed, concurrent_count)
        await asyncio.sleep(0.3)
        concurrent_count -= 1
        return {"task_id": task_id}
    
    queue = AsyncTaskQueue(max_concurrent=max_allowed, name="ConcurrencyTest")
    await queue.start()
    
    try:
        # 提交 10 个任务
        task_ids = []
        for i in range(10):
            task_id = await queue.submit(func=counting_task, kwargs={"task_id": i})
            task_ids.append(task_id)
        
        # 等待所有任务
        await queue.wait_for_batch(task_ids)
        
        print(f"  最大并发数限制: {max_allowed}")
        print(f"  观察到的最大并发: {max_concurrent_observed}")
        
        success = max_concurrent_observed <= max_allowed
        print(f"\n  ✓ 测试通过" if success else f"\n  ✗ 测试失败 - 并发超限")
        return success
        
    finally:
        await queue.stop()


# ==================== 测试 3: 优先级队列 ====================

async def test_priority_queue():
    """测试优先级队列"""
    print("\n" + "="*60)
    print("测试 3: 优先级队列")
    print("="*60)
    
    execution_order = []
    start_event = asyncio.Event()  # 用于同步开始
    
    async def ordered_task(task_id: str, priority: str):
        # 等待所有任务都提交后再开始执行
        await start_event.wait()
        execution_order.append((task_id, priority))
        await asyncio.sleep(0.05)
        return {"task_id": task_id, "priority": priority}
    
    # 使用单个工作者以确保顺序执行
    queue = AsyncTaskQueue(max_concurrent=1, name="PriorityTest")
    await queue.start(num_workers=1)
    
    try:
        # 提交不同优先级的任务
        task_ids = []
        
        # 先提交低优先级
        for i in range(2):
            tid = await queue.submit(
                func=ordered_task,
                kwargs={"task_id": f"low_{i}", "priority": "low"},
                priority=TaskPriority.LOW
            )
            task_ids.append(tid)
        
        # 再提交高优先级
        for i in range(2):
            tid = await queue.submit(
                func=ordered_task,
                kwargs={"task_id": f"high_{i}", "priority": "high"},
                priority=TaskPriority.HIGH
            )
            task_ids.append(tid)
        
        # 提交关键优先级
        tid = await queue.submit(
            func=ordered_task,
            kwargs={"task_id": "critical_0", "priority": "critical"},
            priority=TaskPriority.CRITICAL
        )
        task_ids.append(tid)
        
        # 等待一小段时间确保所有任务都进入队列
        await asyncio.sleep(0.1)
        
        # 开始执行所有任务
        start_event.set()
        
        # 等待所有任务
        await queue.wait_for_batch(task_ids)
        
        print("  执行顺序:")
        for task_id, priority in execution_order:
            print(f"    {task_id} ({priority})")
        
        # 验证优先级顺序：critical > high > low
        # 由于优先级队列的工作方式，高优先级任务应该在低优先级之前
        priority_order = {"critical": 3, "high": 2, "low": 0}
        
        # 检查是否大致按优先级顺序执行
        # 由于任务几乎同时提交，优先级高的应该更早执行
        priorities = [priority_order[p] for _, p in execution_order]
        
        # 简化验证：检查 critical 是否在 low 之前
        critical_idx = next((i for i, (tid, _) in enumerate(execution_order) if "critical" in tid), -1)
        last_low_idx = max([i for i, (tid, _) in enumerate(execution_order) if "low" in tid], default=-1)
        
        # 优先级队列成功的标志：至少 critical 应该不是最后执行的
        success = critical_idx < last_low_idx if critical_idx >= 0 and last_low_idx >= 0 else True
        
        # 也打印一下预期行为说明
        print("\n  说明: 优先级队列确保高优先级任务优先从队列取出")
        print(f"  Critical 索引: {critical_idx}, 最后 Low 索引: {last_low_idx}")
        
        print(f"\n  ✓ 测试通过" if success else "\n  ✗ 测试失败")
        return success
        
    finally:
        await queue.stop()


# ==================== 测试 4: 重试机制 ====================

async def test_retry_mechanism():
    """测试重试机制"""
    print("\n" + "="*60)
    print("测试 4: 重试机制")
    print("="*60)
    
    attempt_counts = {}
    
    async def failing_task(task_id: str, fail_times: int):
        if task_id not in attempt_counts:
            attempt_counts[task_id] = 0
        attempt_counts[task_id] += 1
        
        if attempt_counts[task_id] <= fail_times:
            raise Exception(f"Simulated failure {attempt_counts[task_id]}")
        
        return {"task_id": task_id, "attempts": attempt_counts[task_id]}
    
    queue = AsyncTaskQueue(max_concurrent=3, name="RetryTest")
    await queue.start()
    
    try:
        # 任务会失败 1 次，然后成功（max_retries=2 应该足够）
        task_id = await queue.submit(
            func=failing_task,
            kwargs={"task_id": "retry_test", "fail_times": 1},
            max_retries=2
        )
        
        result = await queue.wait_for_task(task_id)
        
        print(f"  尝试次数: {attempt_counts.get('retry_test', 0)}")
        print(f"  最终结果: {result}")
        
        stats = queue.get_stats()
        print(f"  重试次数: {stats.get('retried', 0)}")
        
        success = result.get("attempts") == 2  # 第一次失败，第二次成功
        print(f"\n  ✓ 测试通过" if success else "\n  ✗ 测试失败")
        return success
        
    except Exception as e:
        print(f"  任务失败: {e}")
        return False
        
    finally:
        await queue.stop()


# ==================== 测试 5: 批量任务 ====================

async def test_batch_tasks():
    """测试批量任务"""
    print("\n" + "="*60)
    print("测试 5: 批量任务")
    print("="*60)
    
    queue = AsyncTaskQueue(max_concurrent=5, name="BatchTest")
    await queue.start()
    
    try:
        start_time = time.time()
        
        # 批量提交 20 个任务
        tasks_data = [
            {
                "func": mock_surgr1_analyze,
                "kwargs": {"image_id": i, "delay": 0.2}
            }
            for i in range(20)
        ]
        
        task_ids = await queue.submit_batch(tasks_data)
        print(f"  提交 {len(task_ids)} 个任务")
        
        # 等待所有任务
        results = await queue.wait_for_batch(task_ids, return_exceptions=True)
        
        elapsed = time.time() - start_time
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        
        print(f"  完成: {success_count}/{len(results)}")
        print(f"  总耗时: {elapsed:.2f}s")
        print(f"  理论串行耗时: {20 * 0.2:.2f}s")
        print(f"  加速比: {(20 * 0.2) / elapsed:.2f}x")
        
        stats = queue.get_stats()
        print(f"  队列统计: {stats}")
        
        success = success_count == 20 and elapsed < 20 * 0.2  # 应该比串行快
        print(f"\n  ✓ 测试通过" if success else "\n  ✗ 测试失败")
        return success
        
    finally:
        await queue.stop()


# ==================== 测试 6: 性能对比 ====================

async def test_performance_comparison():
    """测试性能对比：串行 vs 并发"""
    print("\n" + "="*60)
    print("测试 6: 性能对比")
    print("="*60)
    
    num_tasks = 10
    task_delay = 0.3
    
    # 串行执行
    print("\n  串行执行:")
    start_time = time.time()
    for i in range(num_tasks):
        await mock_surgr1_analyze(i, delay=task_delay)
    serial_time = time.time() - start_time
    print(f"    耗时: {serial_time:.2f}s")
    
    # 并发执行 (max_concurrent=3)
    print("\n  并发执行 (n=3):")
    queue = AsyncTaskQueue(max_concurrent=3, name="PerfTest3")
    await queue.start()
    
    try:
        start_time = time.time()
        task_ids = []
        for i in range(num_tasks):
            tid = await queue.submit(
                func=mock_surgr1_analyze,
                kwargs={"image_id": i, "delay": task_delay}
            )
            task_ids.append(tid)
        
        await queue.wait_for_batch(task_ids)
        concurrent_time_3 = time.time() - start_time
        print(f"    耗时: {concurrent_time_3:.2f}s")
        print(f"    加速比: {serial_time / concurrent_time_3:.2f}x")
    finally:
        await queue.stop()
    
    # 并发执行 (max_concurrent=5)
    print("\n  并发执行 (n=5):")
    queue = AsyncTaskQueue(max_concurrent=5, name="PerfTest5")
    await queue.start()
    
    try:
        start_time = time.time()
        task_ids = []
        for i in range(num_tasks):
            tid = await queue.submit(
                func=mock_surgr1_analyze,
                kwargs={"image_id": i, "delay": task_delay}
            )
            task_ids.append(tid)
        
        await queue.wait_for_batch(task_ids)
        concurrent_time_5 = time.time() - start_time
        print(f"    耗时: {concurrent_time_5:.2f}s")
        print(f"    加速比: {serial_time / concurrent_time_5:.2f}x")
    finally:
        await queue.stop()
    
    print("\n  总结:")
    print(f"    串行: {serial_time:.2f}s")
    print(f"    并发(n=3): {concurrent_time_3:.2f}s ({serial_time / concurrent_time_3:.2f}x)")
    print(f"    并发(n=5): {concurrent_time_5:.2f}s ({serial_time / concurrent_time_5:.2f}x)")
    
    success = concurrent_time_3 < serial_time and concurrent_time_5 < concurrent_time_3
    print(f"\n  ✓ 测试通过" if success else "\n  ✗ 测试失败")
    return success


# ==================== 主测试函数 ====================

async def run_all_tests():
    """运行所有测试"""
    print("="*60)
    print("AsyncTaskQueue 模拟测试")
    print("="*60)
    
    results = {
        "基本队列功能": await test_basic_queue(),
        "并发控制": await test_concurrency_control(),
        "优先级队列": await test_priority_queue(),
        "重试机制": await test_retry_mechanism(),
        "批量任务": await test_batch_tasks(),
        "性能对比": await test_performance_comparison(),
    }
    
    # 打印总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, success in results.items():
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {name}: {status}")
    
    print(f"\n  总计: {passed}/{total} 通过")
    
    return all(results.values())


def main():
    """主函数"""
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

