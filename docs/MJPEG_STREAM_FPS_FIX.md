# MJPEG 视频流帧率优化修复

## 问题描述

**现象**: 通过 stream_simulator 提供的 MJPEG 视频流播放时，帧率明显低于本地视频播放，出现卡顿效果。

**影响**: 前端应用连接 `http://localhost:9001/stream` 时，视频不流畅。

---

## 根本原因分析

### 系统架构

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   VideoSource       │ ──▶ │   Broadcaster Loop  │ ──▶ │  Client Loop    │
│   (读取视频帧)       │     │   (编码 JPEG)        │     │  (发送到浏览器)  │
└─────────────────────┘     └─────────────────────┘     └─────────────────┘
        ↓                           ↓                          ↓
   realtime=True              _shared_frame              response.write()
   (按视频帧率读取)            (共享帧缓冲)               (MJPEG multipart)
```

### 问题根源

**修复前的实现**:
- Broadcaster Loop: `async for frame in source.frames_async()` → 编码 JPEG → 更新 `_shared_frame`
- Client Loop: `await asyncio.sleep(frame_interval)` → 检查 `_shared_frame` 是否变化 → 发送

**问题**: 两个独立的 `asyncio.sleep` 循环**相位不同步**！

```
时间轴:
Broadcaster: |--编码--|sleep|--编码--|sleep|--编码--|sleep|
Client:         |sleep|检查|sleep|检查|sleep|检查|
                       ↑         ↑         ↑
                    旧帧!      旧帧!      新帧
```

当 Client 的 sleep 结束去检查时，Broadcaster 可能还没更新帧，导致：
- `jpeg_data == last_frame` (帧没变化)
- 跳过发送，等下一个 sleep 周期
- **累积效应**: 帧率从 22fps 下降到 9-14fps

### 调试数据证据

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| Client FPS | 9.38 → 14.63 (不断下降) | 22.64 → 22.73 (稳定) |
| Skipped 帧/30帧周期 | 1-7 帧 | 0 帧 |
| 目标 FPS | 22.73 | 22.73 |

---

## 解决方案

### 核心修改

使用 `asyncio.Event` 替代盲目轮询，实现**事件驱动**的帧分发：

```python
# http_server.py

class HTTPStreamServer:
    def __init__(self, ...):
        # 新增: 帧就绪事件
        self._new_frame_event: Optional[asyncio.Event] = None
    
    async def _start_broadcaster(self):
        # 创建事件
        self._new_frame_event = asyncio.Event()
        ...
    
    async def _broadcaster_loop(self):
        async for frame in self._shared_source.frames_async(realtime=True):
            jpeg_data = encode_frame_jpeg(frame, self.jpeg_quality)
            self._shared_frame = jpeg_data
            # 新增: 通知所有等待的客户端
            if self._new_frame_event:
                self._new_frame_event.set()
                self._new_frame_event.clear()
    
    async def mjpeg_stream(self, request):
        while self._broadcast_started or not self.video_ended:
            # 修改: 等待事件通知，而非盲目 sleep
            if self._new_frame_event:
                try:
                    await asyncio.wait_for(
                        self._new_frame_event.wait(), 
                        timeout=frame_interval * 2
                    )
                except asyncio.TimeoutError:
                    pass  # 超时也要检查是否视频结束
            else:
                await asyncio.sleep(frame_interval)
            
            # 获取并发送帧...
```

### 工作原理

```
时间轴 (修复后):
Broadcaster: |--编码--|set()--|--编码--|set()--|--编码--|set()--|
Client:      |wait()...↑      |wait()...↑      |wait()...↑
                    立即唤醒!       立即唤醒!       立即唤醒!
```

- Broadcaster 编码完成后立即 `event.set()` 通知
- 所有等待的 Client 被唤醒，立即发送新帧
- `event.clear()` 为下一帧做准备
- 超时机制防止 Broadcaster 停止时 Client 挂起

---

## 涉及文件

| 文件 | 修改内容 |
|------|----------|
| `stream_simulator/http_server.py` | 添加 `_new_frame_event`，修改 broadcaster 和 client 循环 |
| `stream_simulator/video_source.py` | 无实际修改（调试日志已移除） |

---

## 相关信息（供其他 bug 修复参考）

### 帧处理流程

1. **VideoSource.frames_async(realtime=True)**
   - 位置: `stream_simulator/video_source.py`
   - 功能: 按视频原始帧率读取帧
   - 时间控制: `asyncio.sleep(wait_time)` 基于 `frame_interval = 1/fps`

2. **encode_frame_jpeg(frame, quality)**
   - 位置: `stream_simulator/video_source.py`
   - 功能: OpenCV JPEG 编码
   - 耗时: 约 15-28ms (取决于分辨率和内容复杂度)

3. **MJPEG Multipart 格式**
   ```
   --frame\r\n
   Content-Type: image/jpeg\r\n
   Content-Length: <size>\r\n
   \r\n
   <jpeg_data>\r\n
   ```

### 关键变量

- `_shared_frame`: 当前最新的 JPEG 编码帧 (bytes)
- `_new_frame_event`: 帧就绪通知事件 (asyncio.Event)
- `frame_interval`: 帧间隔秒数 (1/fps，约 44ms for 22.7fps)
- `video_ended`: 视频是否播放完成的标志

### 前端接收方式

前端 `VideoPlayer.vue` 使用 `<img src="streamUrl">` 显示 MJPEG 流：
```vue
<img
  v-if="mode === 'stream' && isHttpStream"
  :src="streamUrl"
  class="stream-image"
/>
```

其中 `streamUrl` 对于 HTTP 流直接使用原始 URL，不经过后端代理。

---

## Commit 信息

```
commit 46267a1
fix(stream_simulator): 使用 asyncio.Event 替代轮询机制提升 MJPEG 流帧率

问题: 视频流播放比本地播放帧率低、有卡顿
原因: broadcaster 和 client 两个独立的 asyncio.sleep 循环相位不同步，
      客户端轮询时经常发现帧没变化，导致帧率从 22fps 下降到 9-14fps

修复:
- 添加 _new_frame_event (asyncio.Event) 用于帧就绪通知
- broadcaster 在编码新帧后调用 event.set() 通知所有等待的客户端
- client 使用 asyncio.wait_for(event.wait()) 等待新帧，而非盲目轮询
- 保留超时机制防止 broadcaster 停止时客户端挂起

效果: 客户端帧率从 9-14fps 提升到 22.7fps (接近目标值)，skipped 帧从 1-7 降为 0
```

---

## 日期

2026-01-19
