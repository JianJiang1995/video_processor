#!/usr/bin/env python3
"""
测试 bbox 解析器

验证 parse_bboxes_from_surgr1 能正确解析各种格式的 SurgR1 输出
"""
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'video_stream_app', 'backend'))

from services.sam3_consistency import parse_bboxes_from_surgr1


def test_format1_surgr1():
    """测试 SurgR1 格式: label(x1,y1),(x2,y2)"""
    text = "grasper(328,0),(466,112) hook(516,0),(853,287)"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    assert result[0]["label"] == "grasper"
    assert result[0]["x1"] == 328
    assert result[1]["label"] == "hook"
    print("✓ Format 1 (SurgR1): PASSED")


def test_format1_with_answer_tag():
    """测试带 <answer> 标签的 SurgR1 格式"""
    text = "<think>Looking for tools...</think><answer>grasper(100,50),(200,150) scissors(300,100),(400,200)</answer>"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    assert result[0]["label"] == "grasper"
    assert result[1]["label"] == "scissors"
    print("✓ Format 1 with <answer> tag: PASSED")


def test_format2_colon():
    """测试冒号格式: label: (x1, y1), (x2, y2)"""
    text = "grasper: (100, 50), (200, 150); hook: (300, 100), (400, 200)"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    assert result[0]["label"] == "grasper"
    assert result[1]["label"] == "hook"
    print("✓ Format 2 (colon): PASSED")


def test_format3_bbox_keyword():
    """测试带 bbox 关键字的格式"""
    text = "tool1 bbox (100,50), (200,150); tool2: bbox (300,100), (400,200)"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    print("✓ Format 3 (bbox keyword): PASSED")


def test_format4_no_label():
    """测试无标签格式: (x1,y1), (x2,y2)"""
    text = "Found tools at (100,50), (200,150) and (300,100), (400,200)"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    assert result[0]["label"] == "tool_0"
    assert result[1]["label"] == "tool_1"
    print("✓ Format 4 (no label): PASSED")


def test_format5_comma_separated():
    """测试逗号分隔格式: x1,y1,x2,y2"""
    text = "Bounding boxes: 100,50,200,150 and 300,100,400,200"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    print("✓ Format 5 (comma separated): PASSED")


def test_empty_input():
    """测试空输入"""
    assert parse_bboxes_from_surgr1("") == []
    assert parse_bboxes_from_surgr1(None) == []
    print("✓ Empty input: PASSED")


def test_no_bboxes():
    """测试没有 bbox 的输入"""
    text = "No tools detected in this frame"
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 0, f"Expected 0 bboxes, got {len(result)}"
    print("✓ No bboxes: PASSED")


def test_real_surgr1_output():
    """测试真实的 SurgR1 输出"""
    # 模拟真实的 SurgR1 输出
    text = """<think>
I can see two surgical instruments in the image:
1. A grasper on the left side
2. A hook/cautery on the right side
</think>
<answer>grasper(100,50),(250,180) hook(400,30),(600,200)</answer>"""
    
    result = parse_bboxes_from_surgr1(text)
    
    assert len(result) == 2, f"Expected 2 bboxes, got {len(result)}"
    assert result[0]["label"] == "grasper"
    assert result[1]["label"] == "hook"
    print("✓ Real SurgR1 output: PASSED")


def main():
    print("=" * 60)
    print("测试 bbox 解析器")
    print("=" * 60)
    
    tests = [
        test_format1_surgr1,
        test_format1_with_answer_tag,
        test_format2_colon,
        test_format3_bbox_keyword,
        test_format4_no_label,
        test_format5_comma_separated,
        test_empty_input,
        test_no_bboxes,
        test_real_surgr1_output,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__}: FAILED - {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: ERROR - {e}")
            failed += 1
    
    print("=" * 60)
    print(f"总计: {passed} 通过, {failed} 失败")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)




