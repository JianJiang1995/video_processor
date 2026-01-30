"""
Test script for GeminiClient
Run: python -m backend.services.test_gemini_client
"""
import asyncio
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.services.gemini_client import (
    GeminiClient,
    get_gemini_client,
    ensure_gemini_available,
    GENAI_AVAILABLE
)


async def test_basic_chat():
    """Test basic text chat"""
    print("\n=== Test 1: Basic Chat ===")
    client = get_gemini_client()
    
    result = await client.chat(
        message="用一句话介绍腹腔镜胆囊切除术",
        system_prompt="你是一个医学专家，请简洁回答"
    )
    
    print(f"Success: {result.get('success')}")
    print(f"Model: {result.get('model')}")
    print(f"Response: {result.get('text', '')[:200]}...")
    print(f"Duration: {result.get('duration_ms', 0):.0f}ms")
    
    return result.get('success', False)


async def test_health_check():
    """Test health check"""
    print("\n=== Test 2: Health Check ===")
    client = get_gemini_client()
    
    is_healthy = await client.check_health()
    print(f"Health: {'OK' if is_healthy else 'FAILED'}")
    
    return is_healthy


async def test_integrate_analysis():
    """Test integrate_analysis_results with mock data"""
    print("\n=== Test 3: Integrate Analysis Results ===")
    client = get_gemini_client()
    
    # Mock frame analyses (similar to what SurgR1 would produce)
    mock_frame_analyses = [
        {
            "frame_idx": 0,
            "timestamp": 0.0,
            "phase": "GallbladderPackaging",
            "action": "grasper grasp specimen_bag",
            "tools": "grasper detected at (100,200)"
        },
        {
            "frame_idx": 1,
            "timestamp": 1.0,
            "phase": "GallbladderPackaging",
            "action": "grasper grasp specimen_bag",
            "tools": "grasper detected at (110,210)"
        },
        {
            "frame_idx": 2,
            "timestamp": 2.0,
            "phase": "GallbladderPackaging",
            "action": "specimen_bag contains gallbladder",
            "tools": "grasper detected at (120,220)"
        }
    ]
    
    # Mock history context
    mock_history = """## 之前窗口分析历史
### 窗口 0（0.0s - 15.0s）
- 阶段：胆囊分离阶段
- 摘要：抓钳正牵拉胆囊，进行分离操作
"""
    
    result = await client.integrate_analysis_results(
        frame_analyses=mock_frame_analyses,
        images=None,  # No images for this test
        history_context=mock_history
    )
    
    print(f"Success: {result.get('success')}")
    print(f"Model: {result.get('model')}")
    print(f"Frame count: {result.get('frame_count')}")
    print(f"Summary: {result.get('summary', '')[:300]}...")
    
    return result.get('success', False)


async def main():
    print("=" * 60)
    print("GeminiClient Integration Test")
    print("=" * 60)
    
    # Check SDK availability
    print(f"\ngoogle-genai SDK available: {GENAI_AVAILABLE}")
    
    if not GENAI_AVAILABLE:
        print("\nERROR: google-genai SDK not installed.")
        print("Run: pip install google-genai")
        return
    
    # Check API key
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("\nWARNING: GEMINI_API_KEY environment variable not set.")
        print("Set it with: export GEMINI_API_KEY='your-api-key'")
        print("Get your API key from: https://aistudio.google.com/app/apikey")
        return
    
    print(f"API Key: {'*' * 10}...{api_key[-4:]}")
    
    # Run tests
    tests = [
        ("Health Check", test_health_check),
        ("Basic Chat", test_basic_chat),
        ("Integrate Analysis", test_integrate_analysis),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            print(f"ERROR in {name}: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")
    
    total_passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {total_passed}/{len(results)} tests passed")


if __name__ == "__main__":
    asyncio.run(main())


