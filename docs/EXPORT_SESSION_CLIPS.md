# Export Session Clips 使用文档

视频分析会话片段导出工具，将指定 session 的分析结果导出为带右侧分析文字面板的独立视频片段。

## 功能概述

- 从数据库中读取视频分析结果
- 将每个分析窗口导出为独立的视频片段
- 自动在视频右侧添加分析文字面板（600px 宽）
- 支持并行处理，加速批量导出
- 支持本地视频文件和流媒体帧图片两种数据源

## 依赖要求

### 系统依赖

- **FFmpeg**: 用于视频编码和处理
- **中文字体**: 如 wqy-microhei、wqy-zenhei 等（用于显示中文分析文字）

### Python 依赖

- `Pillow`: 用于文字渲染和图像处理
- `SQLAlchemy`: 数据库 ORM
- `PyMySQL`: MySQL 数据库驱动

```bash
pip install Pillow SQLAlchemy PyMySQL
```

## 使用方法

### 脚本位置

```
/data2/jj/proj/video_processor/video_stream_app/scripts/export_session_clips.py
```

### 命令格式

```bash
python export_session_clips.py [session_id] [选项]
```

### 基础用法

#### 1. 列出所有可用 session

```bash
# 列出最近 50 个 session（默认）
python export_session_clips.py --list

# 列出最近 20 个 session
python export_session_clips.py --list --limit 20

# 简写形式
python export_session_clips.py -l --limit 10
```

输出示例：
```
================================================================================
Available Sessions (showing 10)
================================================================================

  Session ID: c4bb4893
    Name: surgery_video_001.mp4
    Type: local
    Path: /data/videos/surgery_video_001.mp4
    Duration: 300.5s
    Created: 2026-01-20T10:30:00

  Session ID: a1b2c3d4
    Name: stream_session
    Type: stream
    Path: N/A
    Duration: 180.0s
    Created: 2026-01-19T15:20:00

================================================================================
```

#### 2. 导出指定 session 的所有窗口

```bash
python export_session_clips.py e2f698f8
```

#### 3. 导出指定窗口

```bash
# 只导出窗口 0, 1, 2
python export_session_clips.py c4bb4893 --windows 0,1,2
python 
# 简写形式
python export_session_clips.py c4bb4893 -w 0,1,2,5,10
```

#### 4. 指定输出目录

```bash
# 导出到自定义目录
python export_session_clips.py c4bb4893 --output /tmp/exported_clips

# 简写形式
python export_session_clips.py c4bb4893 -o /home/user/clips
```

#### 5. 指定并行 worker 数量

```bash
# 使用 4 个并行进程
python export_session_clips.py c4bb4893 --workers 4

# 简写形式
python export_session_clips.py c4bb4893 -j 8
```

### 组合使用

```bash
# 导出指定窗口到自定义目录，使用 4 个并行进程
python export_session_clips.py c4bb4893 \
    --windows 0,1,2,3 \
    --output /data/exports/surgery \
    --workers 4
```

## 命令行参数说明

| 参数 | 简写 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| `session_id` | - | 位置参数 | - | 要导出的 Session ID |
| `--windows` | `-w` | 字符串 | 全部 | 逗号分隔的窗口 ID 列表 |
| `--output` | `-o` | 路径 | `output/<session_id>` | 输出目录 |
| `--workers` | `-j` | 整数 | CPU 核数 (最大4) | 并行 worker 数量 |
| `--list` | `-l` | 标志 | - | 列出所有可用 session |
| `--limit` | - | 整数 | 50 | 列出 session 的最大数量 |

## 输出格式

### 文件命名规则

导出的视频文件命名格式为：

```
{session_id}_window{window_id}_t{start_time}-{end_time}s.mp4
```

例如：
- `c4bb4893_window0_t0-15s.mp4`
- `c4bb4893_window1_t15-30s.mp4`
- `c4bb4893_window12_t180-195s.mp4`

### 默认输出位置

如果未指定 `--output`，默认输出到：

```
/data2/jj/proj/video_processor/output/<session_id>/
```

### 视频规格

- **编码**: H.264 (libx264)
- **预设**: fast
- **质量**: CRF 23
- **文字面板宽度**: 600 像素
- **字体大小**: 24 像素
- **行间距**: 8 像素

## 数据源说明

脚本会根据 session 的 `video_type` 自动选择数据源：

### 1. 本地视频文件 (video_type = "local")

- 从原始视频文件中提取指定时间段
- 使用 FFmpeg 的 `drawtext` 滤镜添加文字面板
- 要求：原始视频文件路径有效且可访问

### 2. 流媒体帧图片 (video_type = "stream")

- 从存储目录的 `frames/` 子目录读取帧图片
- 使用 PIL 合成文字面板
- 再使用 FFmpeg 将帧序列编码为视频
- 帧文件命名格式：`frame_XXX_tsYYY_ZZZ.jpg`

## 配置文件

脚本从 `video_stream_app/config.json` 读取数据库配置：

```json
{
  "database": {
    "mysql": {
      "host": "localhost",
      "port": 3306,
      "user": "root",
      "password": "your_password",
      "database": "video_analyzer"
    }
  }
}
```

## 运行示例

### 完整导出流程

```bash
# 1. 进入脚本目录
cd /data2/jj/proj/video_processor/video_stream_app/scripts

# 2. 查看可用的 session
python export_session_clips.py --list --limit 5

# 3. 导出指定 session（使用 4 个进程）
python export_session_clips.py c4bb4893 -j 4

# 4. 查看输出
ls -la /data2/jj/proj/video_processor/output/c4bb4893/
```

### 输出示例

```
2026-01-21 10:30:00 - INFO - Session: c4bb4893
2026-01-21 10:30:00 - INFO -   Video: surgery_video_001.mp4
2026-01-21 10:30:00 - INFO -   Type: local
2026-01-21 10:30:00 - INFO -   Path: /data/videos/surgery_video_001.mp4
2026-01-21 10:30:00 - INFO -   Storage: /data/storage/c4bb4893
2026-01-21 10:30:00 - INFO -   Total windows: 20
2026-01-21 10:30:00 - INFO -   Output: /data2/jj/proj/video_processor/output/c4bb4893
2026-01-21 10:30:00 - INFO -   Workers: 4 (parallel)

开始并行导出 20 个窗口 (使用 4 个进程)...
  ✓ Window 3: c4bb4893_window3_t45-60s.mp4 (2.5 MB)
  ✓ Window 1: c4bb4893_window1_t15-30s.mp4 (2.3 MB)
  ✓ Window 0: c4bb4893_window0_t0-15s.mp4 (2.1 MB)
  ✓ Window 2: c4bb4893_window2_t30-45s.mp4 (2.4 MB)
  ...

============================================================
EXPORT COMPLETE
  Total: 20
  Success: 20
  Failed: 0
  Output: /data2/jj/proj/video_processor/output/c4bb4893
============================================================
```

## 常见问题

### 1. 找不到中文字体

错误信息：文字显示为方框或乱码

解决方案：
```bash
# Ubuntu/Debian
sudo apt-get install fonts-wqy-microhei fonts-wqy-zenhei

# CentOS/RHEL
sudo yum install wqy-microhei-fonts wqy-zenhei-fonts
```

### 2. FFmpeg 未安装

错误信息：`ffmpeg: command not found`

解决方案：
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# CentOS/RHEL
sudo yum install ffmpeg
```

### 3. 数据库连接失败

错误信息：`Can't connect to MySQL server`

解决方案：
1. 检查 MySQL 服务是否运行
2. 检查 `config.json` 中的数据库配置
3. 确认用户名和密码正确

### 4. 找不到原始视频文件

错误信息：`Video path does not exist`

解决方案：
1. 确认 session 记录中的视频路径正确
2. 检查视频文件是否被移动或删除
3. 如果是流媒体类型，检查 storage_path 下是否有 frames 目录

### 5. 内存不足

导出大量窗口时可能出现内存问题

解决方案：
```bash
# 减少并行 worker 数量
python export_session_clips.py c4bb4893 --workers 2
```

## 性能建议

1. **并行度设置**: 默认最多使用 8 个 worker，可根据 CPU 核数和内存调整
2. **磁盘 I/O**: SSD 可显著提升帧图片的读写速度
3. **批量导出**: 对于大量窗口，建议分批导出避免资源耗尽
4. **网络存储**: 如果数据在网络存储上，建议减少 worker 数量避免 I/O 瓶颈
5. **纯 FFmpeg 处理**: 使用 ffmpeg concat + drawtext 滤镜一步完成，无需生成临时文件，每窗口约 3-5 秒

## 相关文档

- [视频流部署指南](VIDEO_STREAM_DEPLOYMENT.md)
- [SAM3 流式分析设计](SAM3_STREAMING_DESIGN.md)
- [Electron 应用指南](ELECTRON_APP_GUIDE.md)
