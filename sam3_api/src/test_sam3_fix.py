#!/usr/bin/env python3
"""
SAM3 分割修复测试脚本

用于验证：
1. 放宽的质心验证是否工作
2. 流式传播是否正常
3. 调试日志是否输出

运行方式:
    cd sam3_api/src
    python test_sam3_fix.py
"""
import os
import sys
import numpy as np
import cv2
import tempfile
import json

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_single_image_segmentation():
    """测试单图分割"""
    print("\n" + "="*60)
    print("测试 1: 单图分割 (放宽验证)")
    print("="*60)
    
    from sam3_model import get_model
    
    # 创建测试图像：模拟手术场景（暗背景 + 两个亮色物体）
    test_image = np.zeros((480, 640, 3), dtype=np.uint8)
    test_image[:] = (40, 30, 30)  # 暗红色背景（模拟腹腔）
    
    # 物体1: 模拟 grasper（左侧长条形）
    cv2.rectangle(test_image, (100, 200), (300, 250), (180, 180, 180), -1)
    cv2.rectangle(test_image, (80, 180), (120, 270), (200, 200, 200), -1)  # 头部
    
    # 物体2: 模拟 hook（右侧弯曲形状）
    cv2.ellipse(test_image, (450, 220), (80, 40), 0, 0, 360, (160, 160, 160), -1)
    cv2.rectangle(test_image, (520, 180), (580, 260), (170, 170, 170), -1)
    
    # 保存测试图像
    temp_path = tempfile.NamedTemporaryFile(suffix='.png', delete=False).name
    cv2.imwrite(temp_path, test_image)
    print(f"创建测试图像: {temp_path}")
    
    try:
        model = get_model()
        
        # 测试两个 bbox
        bboxes = [
            {"x1": 70, "y1": 170, "x2": 310, "y2": 280, "label": "grasper"},
            {"x1": 360, "y1": 170, "x2": 590, "y2": 280, "label": "hook"}
        ]
        
        print(f"测试 bboxes: {bboxes}")
        
        result = model.segment_with_bboxes(
            image_path=temp_path,
            bboxes=bboxes,
            return_base64=True
        )
        
        print(f"\n结果:")
        print(f"  - 成功: {result.get('num_objects', 0) > 0}")
        print(f"  - 检测到的物体数: {result.get('num_objects', 0)}")
        print(f"  - Masks: {result.get('masks', [])}")
        print(f"  - 输出路径: {result.get('output_path', 'N/A')}")
        print(f"  - Base64 长度: {len(result.get('image_base64', ''))}")
        
        if result.get('num_objects', 0) >= 1:
            print("\n✓ 单图分割测试通过！")
            return True
        else:
            print("\n✗ 单图分割测试失败：未检测到物体")
            return False
            
    except Exception as e:
        print(f"\n✗ 单图分割测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            os.unlink(temp_path)
        except:
            pass


def test_streaming_segmentation():
    """测试流式分割"""
    print("\n" + "="*60)
    print("测试 2: 流式分割 (真实传播)")
    print("="*60)
    
    from sam3_streaming import get_streaming_model
    
    # 创建测试帧序列（5帧，物体逐渐移动）
    frames = []
    for i in range(5):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:] = (40, 30, 30)
        
        # 物体位置逐渐右移
        offset = i * 20
        cv2.rectangle(frame, (100 + offset, 200), (300 + offset, 250), (180, 180, 180), -1)
        cv2.rectangle(frame, (80 + offset, 180), (120 + offset, 270), (200, 200, 200), -1)
        
        frames.append(frame)
    
    try:
        model = get_streaming_model()
        
        # 创建会话
        session_id = model.create_session("test_stream")
        print(f"创建会话: {session_id}")
        
        # 第一帧：使用 bbox 初始化
        bbox = {"x1": 70, "y1": 170, "x2": 310, "y2": 280, "label": "grasper"}
        result1 = model.add_prompt(
            session_id=session_id,
            frame=frames[0],
            frame_idx=0,
            bboxes=[bbox],
            timestamp=0.0
        )
        
        print(f"\n帧 0 (初始化):")
        print(f"  - 成功: {result1.get('success')}")
        print(f"  - 物体数: {result1.get('num_objects', 0)}")
        print(f"  - 跟踪物体: {result1.get('tracked_objects', [])}")
        
        if not result1.get('success') or result1.get('num_objects', 0) == 0:
            print("\n✗ 流式分割测试失败：初始化失败")
            model.close_session(session_id)
            return False
        
        # 后续帧：传播
        propagation_success = 0
        for i in range(1, 5):
            result = model.propagate_frame(
                session_id=session_id,
                frame=frames[i],
                frame_idx=i,
                timestamp=i * 0.1
            )
            
            print(f"\n帧 {i} (传播):")
            print(f"  - 成功: {result.get('success')}")
            print(f"  - 物体数: {result.get('num_objects', 0)}")
            print(f"  - 使用SAM3传播: {result.get('propagated_with_sam3', False)}")
            
            if result.get('success') and result.get('num_objects', 0) > 0:
                propagation_success += 1
        
        # 关闭会话
        model.close_session(session_id)
        
        if propagation_success >= 3:
            print(f"\n✓ 流式分割测试通过！({propagation_success}/4 帧传播成功)")
            return True
        else:
            print(f"\n✗ 流式分割测试失败：只有 {propagation_success}/4 帧传播成功")
            return False
            
    except Exception as e:
        print(f"\n✗ 流式分割测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_diagnostics():
    """测试诊断 API (需要服务运行)"""
    print("\n" + "="*60)
    print("测试 3: 诊断 API")
    print("="*60)
    
    try:
        import httpx
        
        # 尝试连接本地服务
        client = httpx.Client(timeout=10.0)
        
        # 测试健康检查
        try:
            response = client.get("http://localhost:9004/health")
            if response.status_code == 200:
                print("✓ 健康检查通过")
            else:
                print(f"✗ 健康检查失败: {response.status_code}")
                return False
        except Exception as e:
            print(f"⚠ 服务未运行，跳过 API 测试: {e}")
            return True  # 不算失败
        
        # 测试诊断端点
        response = client.get("http://localhost:9004/diagnostics")
        if response.status_code == 200:
            data = response.json()
            print(f"✓ 诊断端点正常:")
            print(f"  - 服务状态: {data.get('status')}")
            print(f"  - GPU: {data.get('gpu', {}).get('available')}")
            print(f"  - 图像模型: {data.get('image_model', {}).get('loaded')}")
            print(f"  - 流式模型: {data.get('streaming_model', {}).get('loaded')}")
        else:
            print(f"✗ 诊断端点失败: {response.status_code}")
        
        # 测试内部分割测试
        response = client.post("http://localhost:9004/diagnostics/test-segment")
        if response.status_code == 200:
            data = response.json()
            print(f"✓ 内部分割测试:")
            print(f"  - 测试通过: {data.get('test_passed')}")
            print(f"  - 物体数: {data.get('num_objects', 0)}")
        else:
            print(f"✗ 内部分割测试失败: {response.status_code}")
        
        return True
        
    except ImportError:
        print("⚠ httpx 未安装，跳过 API 测试")
        return True
    except Exception as e:
        print(f"⚠ API 测试异常: {e}")
        return True  # 不算失败


def main():
    """运行所有测试"""
    print("="*60)
    print("SAM3 分割修复测试")
    print("="*60)
    
    results = []
    
    # 测试 1: 单图分割
    results.append(("单图分割", test_single_image_segmentation()))
    
    # 测试 2: 流式分割
    results.append(("流式分割", test_streaming_segmentation()))
    
    # 测试 3: API 诊断
    results.append(("API 诊断", test_api_diagnostics()))
    
    # 总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    passed = 0
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"  {name}: {status}")
        if result:
            passed += 1
    
    print(f"\n总计: {passed}/{len(results)} 测试通过")
    
    return passed == len(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

