#!/usr/bin/env python3
"""
Test script for SurgR1 API

Usage:
    python test_api.py [image_path1] [image_path2] ...
    
If no paths provided, uses test images from cholec dataset.
"""

import sys
import json
import requests
from pathlib import Path

API_URL = "http://localhost:9001"


def test_health():
    """Test health endpoint"""
    print("=" * 60)
    print("Testing /health endpoint...")
    try:
        resp = requests.get(f"{API_URL}/health")
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")
        return resp.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_models():
    """Test models listing"""
    print("=" * 60)
    print("Testing /v1/models endpoint...")
    try:
        resp = requests.get(f"{API_URL}/v1/models")
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")
        return resp.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_analyze_batch(image_paths: list):
    """Test batch analysis"""
    print("=" * 60)
    print(f"Testing /analyze endpoint with {len(image_paths)} images...")
    
    try:
        resp = requests.post(
            f"{API_URL}/analyze",
            json={"image_paths": image_paths}
        )
        print(f"Status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"Total images: {data['total_images']}")
            print(f"Total questions: {data['total_questions']}")
            print()
            
            for result in data['results']:
                print(f"Image: {result['image_path']}")
                print("-" * 40)
                for q_key, answer in result['responses'].items():
                    # Extract just the answer part (truncate if too long)
                    if '<answer>' in answer:
                        start = answer.find('<answer>') + len('<answer>')
                        end = answer.find('</answer>') if '</answer>' in answer else len(answer)
                        short_answer = answer[start:end][:200]
                    else:
                        short_answer = answer[:200]
                    print(f"  {q_key}: {short_answer}...")
                print()
            return True
        else:
            print(f"Error: {resp.text}")
            return False
            
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_analyze_single(image_path: str):
    """Test single image analysis"""
    print("=" * 60)
    print(f"Testing /analyze_single endpoint...")
    
    try:
        resp = requests.post(
            f"{API_URL}/analyze_single",
            params={"image_path": image_path}
        )
        print(f"Status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            print(f"Image: {data['image_path']}")
            for q_key, answer in data['responses'].items():
                print(f"  {q_key}: {answer[:100]}...")
            return True
        else:
            print(f"Error: {resp.text}")
            return False
            
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    # Get image paths from command line or use defaults
    if len(sys.argv) > 1:
        image_paths = sys.argv[1:]
    else:
        # Default test images
        test_dir = Path("/data/tos_copy/CholecT50/videos/VID01")
        if test_dir.exists():
            image_paths = [str(p) for p in sorted(test_dir.glob("*.png"))[:3]]
        else:
            print("No test images found. Please provide image paths as arguments.")
            print("Usage: python test_api.py /path/to/image1.jpg /path/to/image2.jpg")
            return
    
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║              SurgR1 API Test Suite                           ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()
    
    # Run tests
    results = []
    
    results.append(("Health Check", test_health()))
    results.append(("Models List", test_models()))
    
    if image_paths:
        # Filter valid paths
        valid_paths = [p for p in image_paths if Path(p).exists()]
        if valid_paths:
            results.append(("Batch Analysis", test_analyze_batch(valid_paths)))
            results.append(("Single Analysis", test_analyze_single(valid_paths[0])))
        else:
            print(f"Warning: No valid image paths found")
            for p in image_paths:
                print(f"  - {p}: {'exists' if Path(p).exists() else 'NOT FOUND'}")
    
    # Summary
    print("=" * 60)
    print("Test Summary:")
    print("-" * 60)
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
    
    all_passed = all(r[1] for r in results)
    print("-" * 60)
    print(f"Overall: {'✅ All tests passed!' if all_passed else '❌ Some tests failed'}")


if __name__ == "__main__":
    main()




