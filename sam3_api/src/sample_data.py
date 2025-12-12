#!/usr/bin/env python3
"""
从JSONL文件采样数据并复制到test_data目录
"""
import os
import json
import random
import shutil
from pathlib import Path

# 配置
JSONL_PATH = "/data/jj/proj/Laparo/data_json_epoch4_gridposition/cholecinstanceseg/ready/train_vqa_tool_localization.jsonl"
OUTPUT_DIR = "./test_data"
SAMPLE_SIZE = 100
RANDOM_SEED = 42

def main():
    # 设置随机种子
    random.seed(RANDOM_SEED)
    
    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 读取所有数据
    print(f"读取数据文件: {JSONL_PATH}")
    all_data = []
    with open(JSONL_PATH, 'r') as f:
        for line in f:
            if line.strip():
                try:
                    data = json.loads(line)
                    all_data.append(data)
                except json.JSONDecodeError as e:
                    print(f"JSON解析错误: {e}")
                    continue
    
    print(f"总共读取 {len(all_data)} 条数据")
    
    # 采样
    if len(all_data) <= SAMPLE_SIZE:
        sampled_data = all_data
        print(f"数据量小于采样数，使用全部数据")
    else:
        sampled_data = random.sample(all_data, SAMPLE_SIZE)
        print(f"随机采样 {SAMPLE_SIZE} 条数据")
    
    # 处理每条数据
    samples = []
    for idx, data in enumerate(sampled_data):
        try:
            image_path = data["images"][0]
            refs = data["objects"]["ref"]
            bboxes_raw = data["objects"]["bbox"]
            
            # 检查图片是否存在
            if not os.path.exists(image_path):
                print(f"跳过不存在的图片: {image_path}")
                continue
            
            # 复制图片到test_data
            image_name = os.path.basename(image_path)
            # 添加索引前缀避免重名
            new_image_name = f"{idx:04d}_{image_name}"
            new_image_path = os.path.join(OUTPUT_DIR, new_image_name)
            shutil.copy2(image_path, new_image_path)
            
            # 转换bbox格式
            bboxes = []
            for ref, bbox in zip(refs, bboxes_raw):
                x1, y1, x2, y2 = bbox
                bboxes.append({
                    "x1": int(x1),
                    "y1": int(y1),
                    "x2": int(x2),
                    "y2": int(y2),
                    "label": ref
                })
            
            # 保存样本信息
            sample = {
                "image_input_path": os.path.abspath(new_image_path),
                "original_path": image_path,
                "bboxes": bboxes
            }
            samples.append(sample)
            
            if (idx + 1) % 20 == 0:
                print(f"已处理 {idx + 1}/{len(sampled_data)} 条数据")
                
        except Exception as e:
            print(f"处理数据 {idx} 时出错: {e}")
            continue
    
    # 保存样本索引文件
    samples_file = os.path.join(OUTPUT_DIR, "samples.json")
    with open(samples_file, 'w') as f:
        json.dump(samples, f, indent=2)
    
    print(f"\n完成！")
    print(f"- 采样数据保存到: {OUTPUT_DIR}")
    print(f"- 样本索引文件: {samples_file}")
    print(f"- 成功处理 {len(samples)} 个样本")
    
    # 统计工具类型分布
    tool_counts = {}
    for sample in samples:
        for bbox in sample["bboxes"]:
            label = bbox["label"]
            tool_counts[label] = tool_counts.get(label, 0) + 1
    
    print(f"\n工具类型分布:")
    for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        print(f"  {tool}: {count}")

if __name__ == "__main__":
    main()

