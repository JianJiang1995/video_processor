"""
Test SurgR1 API: Single image with multiple questions
Tests batch processing of the same image with 3 different questions as independent samples.
"""
import asyncio
import httpx
import json
import time
from pathlib import Path

# SurgR1 API endpoint
SURGR1_API = "http://localhost:9003"

# Test image - use the sample video frame
TEST_IMAGE = "/data2/jj/proj/video_processor/test_data/sample_frame.jpg"

# Standard surgical questions
QUESTIONS = {
    "tool_localization": "Given the laparoscopic surgical image <image>, locate all the tools in the format of bbox (x1,y1), (x2,y2).",
    "surgical_action": "Given the laparoscopic surgical image <image>, describe the complete surgical action in terms of tool, action, and tissue.",
    "surgical_phase": "Given the laparoscopic surgical image <image>, which surgical phase does this frame belong to? Choose from: Preparation, CalotTriangleDissection, ClippingCutting, GallbladderDissection, GallbladderPackaging, CleaningCoagulation, GallbladderRetraction."
}


async def check_health():
    """Check if SurgR1 service is available"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(f"{SURGR1_API}/health")
            data = response.json()
            print(f"✓ SurgR1 Status: {data['status']}")
            print(f"  Model loaded: {data['model_loaded']}")
            return data['model_loaded']
        except Exception as e:
            print(f"✗ Health check failed: {e}")
            return False


async def test_single_image_default_questions(image_path: str):
    """
    Test: Send single image with default 3 questions
    This should work as 3 independent samples.
    """
    print("\n" + "="*60)
    print("TEST 1: Single image with default 3 questions")
    print("="*60)
    
    payload = {
        "image_paths": [image_path]
        # No custom questions - uses default 3
    }
    
    print(f"Image: {image_path}")
    print(f"Questions: 3 (default)")
    
    async with httpx.AsyncClient(timeout=300) as client:
        start_time = time.time()
        try:
            response = await client.post(
                f"{SURGR1_API}/analyze",
                json=payload
            )
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                print(f"\n✓ Success! ({elapsed:.2f}s)")
                print(f"  Total images: {data['total_images']}")
                print(f"  Total questions: {data['total_questions']}")
                
                for result in data['results']:
                    print(f"\n  Image: {Path(result['image_path']).name}")
                    for q_key, answer in result['responses'].items():
                        # Show first 100 chars of answer
                        preview = answer[:100].replace('\n', ' ')
                        print(f"    {q_key}: {preview}...")
                return True
            else:
                print(f"\n✗ Failed: HTTP {response.status_code}")
                print(f"  Response: {response.text}")
                return False
                
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"\n✗ Error after {elapsed:.2f}s: {e}")
            return False


async def test_single_image_single_question(image_path: str, question_key: str):
    """
    Test: Send single image with single custom question
    This is the baseline - should always work.
    """
    print("\n" + "="*60)
    print(f"TEST 2: Single image with single question ({question_key})")
    print("="*60)
    
    question = QUESTIONS[question_key]
    
    payload = {
        "image_paths": [image_path],
        "questions": [question]
    }
    
    print(f"Image: {image_path}")
    print(f"Question: {question_key}")
    
    async with httpx.AsyncClient(timeout=120) as client:
        start_time = time.time()
        try:
            response = await client.post(
                f"{SURGR1_API}/analyze",
                json=payload
            )
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                print(f"\n✓ Success! ({elapsed:.2f}s)")
                
                for result in data['results']:
                    for q_key, answer in result['responses'].items():
                        preview = answer[:200].replace('\n', ' ')
                        print(f"  Answer: {preview}...")
                return True
            else:
                print(f"\n✗ Failed: HTTP {response.status_code}")
                print(f"  Response: {response.text}")
                return False
                
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"\n✗ Error after {elapsed:.2f}s: {e}")
            return False


async def test_sequential_questions(image_path: str):
    """
    Test: Send same image with each question separately (sequential)
    This tests if the issue is batching vs sequential.
    """
    print("\n" + "="*60)
    print("TEST 3: Same image, 3 questions sent sequentially")
    print("="*60)
    
    total_start = time.time()
    results = {}
    
    async with httpx.AsyncClient(timeout=120) as client:
        for q_key, question in QUESTIONS.items():
            payload = {
                "image_paths": [image_path],
                "questions": [question]
            }
            
            start_time = time.time()
            try:
                response = await client.post(
                    f"{SURGR1_API}/analyze",
                    json=payload
                )
                elapsed = time.time() - start_time
                
                if response.status_code == 200:
                    data = response.json()
                    answer = list(data['results'][0]['responses'].values())[0]
                    results[q_key] = answer[:100]
                    print(f"  ✓ {q_key}: {elapsed:.2f}s")
                else:
                    print(f"  ✗ {q_key}: HTTP {response.status_code}")
                    results[q_key] = None
                    
            except Exception as e:
                print(f"  ✗ {q_key}: {e}")
                results[q_key] = None
    
    total_elapsed = time.time() - total_start
    print(f"\nTotal time: {total_elapsed:.2f}s")
    print(f"All succeeded: {all(v is not None for v in results.values())}")
    return results


def find_test_image():
    """Find a test image to use"""
    # Try known locations
    candidates = [
        "/data2/jj/proj/video_processor/test_data/sample_frame.jpg",
        "/data2/jj/proj/video_processor/test_data/test_frame.jpg",
    ]
    
    for path in candidates:
        if Path(path).exists():
            return path
    
    # Try to extract a frame from the test video
    video_path = "/data2/jj/proj/video_processor/test_data/2024-12-24_225315_VID002.mp4"
    if Path(video_path).exists():
        output_path = "/data2/jj/proj/video_processor/test_data/sample_frame.jpg"
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 30)  # Get frame 30
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                cv2.imwrite(output_path, frame)
                print(f"✓ Extracted test frame from video: {output_path}")
                return output_path
        except Exception as e:
            print(f"✗ Failed to extract frame: {e}")
    
    return None


async def main():
    print("="*60)
    print("SurgR1 API Test: Single Image Multi-Question Batch Processing")
    print("="*60)
    
    # Check health
    is_ready = await check_health()
    if not is_ready:
        print("\n⚠ SurgR1 model not loaded. Please wait for model to load.")
        return
    
    # Find test image
    test_image = find_test_image()
    if not test_image:
        print("\n✗ No test image found. Please provide a test image.")
        return
    
    print(f"\nUsing test image: {test_image}")
    
    # Run tests
    print("\n" + "#"*60)
    print("# Running Tests")
    print("#"*60)
    
    # Test 1: Single image with single question (baseline)
    await test_single_image_single_question(test_image, "surgical_phase")
    
    # Test 2: Sequential questions (to isolate batching issue)
    await test_sequential_questions(test_image)
    
    # Test 3: Single image with default 3 questions (the problematic case)
    await test_single_image_default_questions(test_image)
    
    print("\n" + "="*60)
    print("Tests completed!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())


