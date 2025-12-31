# SAM3 流式视频分割集成设计文档

## 1. 背景与问题

### 1.1 当前架构
- **SurgR1**：每 1 秒采样一帧进行器械检测，输出 bounding boxes
- **SAM3**：基于 bbox 生成精确的分割 mask
- **问题**：中间帧（~24-29帧/秒）没有分割结果

### 1.2 SAM3 的能力
根据 [sam3-realtime](https://github.com/matteo-tafuro/sam3-realtime)：
- SAM3 支持视频物体分割（VOS）
- 可以从关键帧的 prompt（bbox/point）开始，自动**传播（propagate）** mask 到后续帧
- 适用于实时视频流处理

### 1.3 挑战
1. **Mask 漂移**：长时间传播可能导致 mask 偏离目标
2. **新器械**：传播无法检测新出现的器械
3. **器械消失/重现**：需要正确处理物体的进出
4. **快速运动**：器械快速移动时传播可能失败
5. **遮挡**：器械互相遮挡时的处理

---

## 2. 解决方案：智能流式分割

### 2.1 核心思想

```
┌─────────────────────────────────────────────────────────────────┐
│                        帧处理流程                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   关键帧（每1秒）          中间帧（其余帧）                       │
│   ┌──────────────┐        ┌──────────────────────────────┐     │
│   │  SurgR1 分析  │        │  SAM3 Mask 传播              │     │
│   │  ↓           │        │  ↓                          │     │
│   │  获取 bboxes │        │  使用上一帧的 mask           │     │
│   │  ↓           │        │  自动传播到当前帧            │     │
│   │  一致性检查   │        │                             │     │
│   │  ↓           │        └──────────────────────────────┘     │
│   │  更新 SAM3   │                                             │
│   └──────────────┘                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 状态机设计

```
                    ┌─────────┐
                    │  IDLE   │ ← 初始状态/无器械
                    └────┬────┘
                         │ SurgR1 检测到器械
                         ▼
                    ┌─────────┐
         ┌─────────│TRACKING │←────────────────┐
         │         └────┬────┘                 │
         │              │                      │
    检测到新器械    一致性检查失败         一致性检查通过
    或器械数量变化      │                      │
         │              ▼                      │
         │         ┌─────────┐                 │
         └────────→│ REINIT  │─────────────────┘
                   └─────────┘  重新初始化完成
```

**状态定义**：
| 状态 | 描述 | SAM3 行为 |
|------|------|----------|
| `IDLE` | 没有检测到器械 | 不传播，输出原始帧 |
| `TRACKING` | 正常跟踪中 | 传播已有 mask 到新帧 |
| `REINIT` | 需要重新初始化 | 用新 bbox 重建 mask |

---

## 3. 一致性检查机制

### 3.1 触发重新初始化的条件

#### 条件 1：器械数量变化
```python
def check_instrument_count_change(current_bboxes, tracked_objects):
    current_count = len(current_bboxes)
    tracked_count = len(tracked_objects)
    
    if current_count != tracked_count:
        return True, "REINIT"  # 器械数量变化，需要重新初始化
    return False, None
```

#### 条件 2：新器械类型出现
```python
def check_new_instrument_types(current_labels, tracked_labels):
    new_labels = set(current_labels) - set(tracked_labels)
    
    if new_labels:
        return True, f"新器械类型: {new_labels}"
    return False, None
```

#### 条件 3：Mask 质心与 BBox 偏离
```python
def check_mask_bbox_consistency(mask, bbox, threshold=0.3):
    """
    检查 SAM3 mask 的质心是否在 SurgR1 bbox 附近
    
    threshold: 偏离阈值（相对于 bbox 对角线长度的比例）
    """
    # 计算 mask 质心
    mask_centroid = compute_centroid(mask)
    
    # 计算 bbox 中心
    bbox_center = ((bbox.x1 + bbox.x2) / 2, (bbox.y1 + bbox.y2) / 2)
    
    # 计算 bbox 对角线长度
    diagonal = sqrt((bbox.x2 - bbox.x1)**2 + (bbox.y2 - bbox.y1)**2)
    
    # 计算偏离距离
    distance = sqrt((mask_centroid[0] - bbox_center[0])**2 + 
                   (mask_centroid[1] - bbox_center[1])**2)
    
    # 相对偏离
    relative_offset = distance / diagonal
    
    if relative_offset > threshold:
        return True, f"Mask 偏离: {relative_offset:.2f} > {threshold}"
    return False, None
```

#### 条件 4：Mask 面积异常变化
```python
def check_mask_area_stability(current_area, prev_area, threshold=0.5):
    """
    检查 mask 面积是否突然变化过大
    """
    if prev_area == 0:
        return False, None
    
    change_ratio = abs(current_area - prev_area) / prev_area
    
    if change_ratio > threshold:
        return True, f"面积突变: {change_ratio:.2f}"
    return False, None
```

#### 条件 5：定时强制刷新
```python
def check_forced_refresh(last_reinit_time, current_time, max_interval=10.0):
    """
    每 N 秒强制用 SurgR1 bbox 刷新 SAM3
    防止长时间传播导致的累积误差
    """
    if current_time - last_reinit_time > max_interval:
        return True, f"定时刷新: {max_interval}s"
    return False, None
```

### 3.2 一致性检查流程

```python
class ConsistencyChecker:
    def __init__(self):
        self.last_reinit_time = 0
        self.tracked_objects = {}  # obj_id -> {label, last_area, last_centroid}
        self.reinit_cooldown = 0.5  # 重新初始化后的冷却时间
    
    def check(self, current_time, surgr1_bboxes, sam3_masks):
        """
        返回: (need_reinit: bool, reason: str, reinit_type: str)
        reinit_type: 'full' = 完全重建, 'partial' = 只更新部分物体
        """
        # 检查冷却时间
        if current_time - self.last_reinit_time < self.reinit_cooldown:
            return False, None, None
        
        # 1. 器械数量变化
        if len(surgr1_bboxes) != len(self.tracked_objects):
            return True, "器械数量变化", "full"
        
        # 2. 新器械类型
        current_labels = {b['label'] for b in surgr1_bboxes}
        tracked_labels = {o['label'] for o in self.tracked_objects.values()}
        if current_labels != tracked_labels:
            return True, "器械类型变化", "full"
        
        # 3. 定时刷新
        if current_time - self.last_reinit_time > 10.0:
            return True, "定时刷新", "full"
        
        # 4. 逐个检查一致性
        for obj_id, mask in sam3_masks.items():
            if obj_id not in self.tracked_objects:
                continue
            
            # 找到对应的 bbox
            bbox = self._find_matching_bbox(obj_id, surgr1_bboxes)
            if bbox is None:
                return True, f"物体 {obj_id} 丢失匹配", "partial"
            
            # 质心偏离检查
            need_reinit, reason = check_mask_bbox_consistency(mask, bbox)
            if need_reinit:
                return True, reason, "partial"
            
            # 面积突变检查
            current_area = np.sum(mask > 0.5)
            prev_area = self.tracked_objects[obj_id].get('last_area', 0)
            need_reinit, reason = check_mask_area_stability(current_area, prev_area)
            if need_reinit:
                return True, reason, "partial"
        
        return False, None, None
```

---

## 4. 器械匹配算法

### 4.1 问题
SurgR1 每次分析返回的 bbox 没有跟踪 ID，需要与 SAM3 的跟踪物体进行匹配。

### 4.2 匹配策略

```python
from scipy.optimize import linear_sum_assignment

def match_bboxes_to_tracked_objects(bboxes, tracked_objects):
    """
    使用匈牙利算法将 SurgR1 bbox 与 SAM3 跟踪物体匹配
    
    考虑因素：
    1. 位置接近度（IoU 或中心距离）
    2. 标签匹配
    """
    if not bboxes or not tracked_objects:
        return {}, list(range(len(bboxes))), list(tracked_objects.keys())
    
    # 构建代价矩阵
    cost_matrix = np.zeros((len(bboxes), len(tracked_objects)))
    
    obj_ids = list(tracked_objects.keys())
    
    for i, bbox in enumerate(bboxes):
        for j, obj_id in enumerate(obj_ids):
            tracked = tracked_objects[obj_id]
            
            # 计算 IoU
            iou = compute_iou(bbox, tracked['last_bbox'])
            
            # 标签匹配奖励
            label_match = 1.0 if bbox['label'] == tracked['label'] else 0.5
            
            # 代价 = 1 - (IoU * label_match)
            cost_matrix[i, j] = 1 - (iou * label_match)
    
    # 匈牙利算法求解
    row_ind, col_ind = linear_sum_assignment(cost_matrix)
    
    # 构建匹配结果
    matches = {}
    unmatched_bboxes = list(range(len(bboxes)))
    unmatched_objects = list(obj_ids)
    
    for i, j in zip(row_ind, col_ind):
        if cost_matrix[i, j] < 0.7:  # 阈值
            matches[i] = obj_ids[j]
            unmatched_bboxes.remove(i)
            unmatched_objects.remove(obj_ids[j])
    
    return matches, unmatched_bboxes, unmatched_objects
```

---

## 5. 完整处理流程

### 5.1 关键帧处理（SurgR1 分析帧）

```python
async def process_keyframe(frame, timestamp, session):
    """
    处理关键帧：SurgR1 分析 + 一致性检查 + SAM3 更新
    """
    # 1. SurgR1 分析
    surgr1_result = await surgr1_client.analyze_frame(frame, "all")
    bboxes = parse_bboxes(surgr1_result['tools'])
    
    # 2. 如果没有器械，切换到 IDLE 状态
    if not bboxes:
        session.state = "IDLE"
        session.tracked_objects.clear()
        return frame  # 返回原始帧
    
    # 3. 一致性检查
    need_reinit, reason, reinit_type = session.checker.check(
        timestamp, bboxes, session.last_masks
    )
    
    # 4. 根据检查结果处理
    if session.state == "IDLE" or need_reinit:
        # 需要重新初始化 SAM3
        logger.info(f"SAM3 重新初始化: {reason}")
        
        # 关闭旧会话
        if session.sam3_session_id:
            await sam3_client.close_stream_session(session.sam3_session_id)
        
        # 创建新会话
        result = await sam3_client.create_stream_session(session.id)
        session.sam3_session_id = result['session_id']
        
        # 用 bboxes 初始化
        sam3_result = await sam3_client.process_stream_frame(
            session.sam3_session_id, frame, 0, timestamp, bboxes
        )
        
        # 更新跟踪状态
        session.state = "TRACKING"
        session.checker.last_reinit_time = timestamp
        session.update_tracked_objects(bboxes, sam3_result)
        
    else:
        # 正常传播 + 用 SurgR1 bbox 校正
        sam3_result = await sam3_client.process_stream_frame(
            session.sam3_session_id, frame, session.frame_count, timestamp, bboxes
        )
        session.update_tracked_objects(bboxes, sam3_result)
    
    return sam3_result.get('visualization', frame)
```

### 5.2 中间帧处理（SAM3 传播）

```python
async def process_intermediate_frame(frame, timestamp, session):
    """
    处理中间帧：只用 SAM3 传播 mask
    """
    if session.state != "TRACKING":
        return frame  # 不在跟踪状态，返回原始帧
    
    # SAM3 传播 mask（不传 bboxes）
    sam3_result = await sam3_client.process_stream_frame(
        session.sam3_session_id, frame, session.frame_count, timestamp, 
        bboxes=None  # 不提供 bbox，SAM3 自动传播
    )
    
    # 更新 mask 缓存
    session.last_masks = sam3_result.get('masks', {})
    
    return sam3_result.get('visualization', frame)
```

### 5.3 主处理循环

```python
async def streaming_processing_loop(session):
    """
    主视频处理循环
    """
    surgr1_interval = 1.0  # SurgR1 分析间隔
    sam3_interval = 0.1    # SAM3 处理间隔（10 FPS）
    
    last_surgr1_time = -surgr1_interval
    last_sam3_time = 0
    
    while session.is_active:
        frame, timestamp = await get_next_frame(session)
        
        is_keyframe = (timestamp - last_surgr1_time >= surgr1_interval)
        
        if is_keyframe:
            # 关键帧：SurgR1 + SAM3 更新
            last_surgr1_time = timestamp
            result_frame = await process_keyframe(frame, timestamp, session)
        elif timestamp - last_sam3_time >= sam3_interval:
            # 中间帧：SAM3 传播
            last_sam3_time = timestamp
            result_frame = await process_intermediate_frame(frame, timestamp, session)
        else:
            # 跳过这一帧（使用上一帧的结果）
            result_frame = session.last_result_frame
        
        # 缓存结果供前端获取
        session.last_result_frame = result_frame
        session.frame_count += 1
```

---

## 6. 配置参数

```yaml
# config.yaml

sam3_streaming:
  # 一致性检查参数
  consistency:
    # Mask 质心与 bbox 中心的最大偏离（相对于 bbox 对角线）
    centroid_offset_threshold: 0.3
    
    # Mask 面积变化的最大比例
    area_change_threshold: 0.5
    
    # 强制刷新间隔（秒）
    forced_refresh_interval: 10.0
    
    # 重新初始化后的冷却时间（秒）
    reinit_cooldown: 0.5
  
  # 帧率控制
  timing:
    # SurgR1 分析间隔（秒）
    surgr1_interval: 1.0
    
    # SAM3 传播间隔（秒）- 决定分割的流畅度
    sam3_propagate_interval: 0.1
    
    # 最大传播帧数（超过后强制刷新）
    max_propagate_frames: 30
  
  # 匹配参数
  matching:
    # IoU 匹配阈值
    iou_threshold: 0.3
    
    # 标签不匹配时的代价乘数
    label_mismatch_penalty: 0.5
```

---

## 7. 错误处理与降级

### 7.1 SAM3 服务不可用
```python
if not sam3_available:
    # 降级：只显示 SurgR1 的 bbox 标注
    return draw_bboxes_on_frame(frame, bboxes)
```

### 7.2 传播失败
```python
if sam3_result.get('num_objects', 0) == 0 and session.tracked_objects:
    # 传播丢失了所有物体，强制重新初始化
    session.state = "REINIT"
```

### 7.3 内存泄漏防护
```python
# SAM3 有潜在的内存泄漏问题（见 sam3-realtime README）
# 定期重建会话来释放内存
if session.frame_count % 1000 == 0:
    await recreate_sam3_session(session)
```

---

## 8. 性能考虑

### 8.1 计算资源
| 操作 | GPU 时间 | 频率 |
|------|---------|------|
| SurgR1 分析 | ~100ms | 1 Hz |
| SAM3 传播 | ~30ms | 10 Hz |
| SAM3 重新初始化 | ~150ms | ~0.1 Hz |

### 8.2 优化策略
1. **批量处理**：累积几帧后批量发送给 SAM3
2. **帧跳过**：在 GPU 繁忙时跳过中间帧
3. **分辨率降采样**：对高分辨率视频降采样后处理

---

## 9. 实现路线图

### Phase 1：基础集成（已完成）
- [x] SAM3 流式会话管理
- [x] SurgR1 + SAM3 后台处理
- [x] 前端 SAM3 帧显示

### Phase 2：智能重新初始化
- [ ] 器械数量变化检测
- [ ] 新器械类型检测
- [ ] 实现一致性检查器

### Phase 3：高级功能
- [ ] Mask-BBox 一致性检查
- [ ] 面积稳定性检查
- [ ] 匈牙利匹配算法
- [ ] 定时强制刷新

### Phase 4：优化与稳定性
- [ ] 内存泄漏防护
- [ ] 性能监控
- [ ] 配置热更新
- [ ] 错误恢复机制

---

## 10. 测试用例

### 10.1 功能测试
| 场景 | 预期行为 |
|------|---------|
| 器械首次进入视野 | 触发 REINIT，开始跟踪 |
| 器械离开视野 | 检测到数量变化，切换到 IDLE |
| 新器械进入 | 触发 REINIT，添加新跟踪 |
| 器械快速移动 | mask 跟随移动，可能触发一致性重建 |
| 器械互相遮挡 | 继续跟踪，遮挡后恢复 |
| 连续 10 秒无变化 | 触发定时刷新 |

### 10.2 压力测试
- 长时间运行（>1小时）测试内存稳定性
- 高帧率视频（60fps）测试
- 多器械场景（5+器械同时存在）

---

## 附录 A：API 变更

### 新增端点
```
POST /api/analysis/sam3/stream-reinit/{session_id}
  强制重新初始化 SAM3 会话

GET /api/analysis/sam3/consistency-status/{session_id}
  获取一致性检查状态

PUT /api/analysis/sam3/config
  更新 SAM3 流式配置
```

### 修改端点
```
GET /api/analysis/sam3/stream-frame/{session_id}
  新增返回字段:
  - state: "IDLE" | "TRACKING" | "REINIT"
  - last_reinit_time: float
  - consistency_score: float (0-1)
```

