#!/usr/bin/env python3
"""监控数据库中surg r1和glm的处理速度"""

import sqlite3
import time
import sys
from datetime import datetime

db_path = '/data2/jj/proj/video_processor/video_stream_app/video_analysis.db'

print('=== 数据库监控开始 ===')
print('frame_analyses = SurgR1帧分析, window_summaries = GLM窗口总结')
print('按 Ctrl+C 停止')
print()
sys.stdout.flush()

last_frame_count = 0
last_window_count = 0
last_time = time.time()

while True:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 获取帧分析数量 (SurgR1)
        cursor.execute('SELECT COUNT(*) FROM frame_analyses')
        frame_count = cursor.fetchone()[0]
        
        # 获取窗口总结数量 (GLM)
        cursor.execute('SELECT COUNT(*) FROM window_summaries')
        window_count = cursor.fetchone()[0]
        
        # 获取最新的帧分析记录
        cursor.execute('SELECT id, frame_idx, created_at FROM frame_analyses ORDER BY id DESC LIMIT 1')
        latest_frame = cursor.fetchone()
        
        # 获取最新的窗口总结记录
        cursor.execute('SELECT id, window_id, created_at FROM window_summaries ORDER BY id DESC LIMIT 1')
        latest_window = cursor.fetchone()
        
        # 计算速度
        current_time = time.time()
        elapsed = current_time - last_time
        
        frame_new = frame_count - last_frame_count
        window_new = window_count - last_window_count
        
        frame_rate = frame_new / elapsed if elapsed > 0 else 0
        window_rate = window_new / elapsed if elapsed > 0 else 0
        
        print(f'[{datetime.now().strftime("%H:%M:%S")}] SurgR1帧: {frame_count} (+{frame_new}, {frame_rate:.2f}/s) | GLM窗口: {window_count} (+{window_new}, {window_rate:.2f}/s)')
        if latest_frame:
            print(f'  最新帧分析: ID={latest_frame[0]} 帧索引={latest_frame[1]} 时间={latest_frame[2]}')
        if latest_window:
            print(f'  最新窗口总结: ID={latest_window[0]} WindowID={latest_window[1]} 时间={latest_window[2]}')
        print()
        sys.stdout.flush()
        
        last_frame_count = frame_count
        last_window_count = window_count
        last_time = current_time
        
        conn.close()
        time.sleep(3)
    except KeyboardInterrupt:
        print('\n监控结束')
        break
    except Exception as e:
        print(f'错误: {e}')
        sys.stdout.flush()
        time.sleep(3)
