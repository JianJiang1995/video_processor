# 专用手术夹检测器 v1

日期：2026-07-03

## 目标

建立一个本地专用检测模型，用于检测已经夹在组织上的手术夹候选框。它和现有工具 YOLO 不同：

- 现有 `YOLO Expert` 检测器械，例如钛夹钳、剪刀、电凝钩。
- `Clip Detector v1` 检测已部署的夹子本体候选，例如 Hem-o-lok 或金属钛夹样小目标。

当前 v1 是 one-class 检测器，类别固定为：

```text
surgical_clip
```

暂不直接区分 Hem-o-lok 和钛夹。原因是现有标注还不足以可靠训练二分类；通用 VLM/LocateAnything 对二者类别也会跳变。先稳定检出候选框，再用本地 VLM、规则或人工标注升级分类更稳。

## 文件位置

训练数据：

```text
datasets/clip_detector_v1
```

模型权重：

```text
models/clip_detector/yolo_clip_v1/weights/best.pt
```

训练日志：

```text
models/clip_detector/yolo_clip_v1
```

首次训练时 Ultralytics 还保留了一份原始 run：

```text
runs/detect/models/clip_detector/yolo_clip_v1
```

服务：

```text
backend/services/clip_detector_service.py
```

数据集生成脚本：

```text
scripts/build_clip_detector_dataset.py
```

训练脚本：

```text
scripts/train_clip_detector.py
```

## 数据集构建

使用 6 个夹子专项视频抽帧：

```text
/home/user/proj/video_processor/test_data/夹子视频/*/*.mp4
```

构建命令：

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/build_clip_detector_dataset.py \
  --output datasets/clip_detector_v1 \
  --sample-seconds 4 \
  --max-frames-per-video 35 \
  --clean \
  --gpu 1
```

数据集统计：

```text
训练图像：128
验证图像：34
总图像：162
总候选框：312
空标签帧：31
```

伪标签来源：

```text
Locate all small deployed surgical clips clamped on tissue.
Locate all small thin metal ligating clips clamped on tissue.
Locate all small thick polymer locking clips clamped on tissue.
```

这些 prompt 只用于 bootstrap 伪标签，不作为最终业务规则。

## 训练

训练命令：

```bash
/home/user/proj/video_processor/.venv/bin/python scripts/train_clip_detector.py \
  --data datasets/clip_detector_v1/data.yaml \
  --epochs 20 \
  --imgsz 960 \
  --batch 8 \
  --device 1 \
  --project models/clip_detector \
  --name yolo_clip_v1
```

首版训练使用 `models/clip_detector/pretrained/yolo11n.pt`，输入尺寸 `960`，单张 RTX 4090 显存占用约 `2.6GB`。

最终验证结果来自伪标签验证集：

```text
Precision: 0.769
Recall:    0.520
mAP50:     0.621
mAP50-95:  0.400
Speed:     ~0.7ms/image inference
```

注意：这是伪标签验证集指标，不等价于医生人工标注测试集精度。

## 后端接入

配置：

```json
"clip_detector": {
  "enabled": true,
  "model_path": "/home/user/proj/video_processor/video_stream_app/models/clip_detector/yolo_clip_v1/weights/best.pt",
  "device": "cuda:1",
  "confidence_threshold": 0.25,
  "max_area_ratio": 0.12
}
```

专家融合输出会增加：

```text
[Clip Detector] 检出已部署手术夹候选 3/3 帧，总数 10，最高置信度 0.72
```

验证命令：

```bash
/home/user/proj/video_processor/.venv/bin/python - <<'PY'
import cv2
from backend.services.expert_fusion import run_experts_on_window

frames = [
    cv2.imread("datasets/clip_detector_v1/images/val/video2-1_video2-1-1_0072p00s.jpg"),
    cv2.imread("datasets/clip_detector_v1/images/val/video7-1_video7-1-1_0108p00s.jpg"),
    cv2.imread("datasets/clip_detector_v1/images/val/miccai1-2_miccai1-2-1_0104p00s.jpg"),
]
out = run_experts_on_window(frames, use_yolo=False, use_phase=False, use_triplet=False)
print(out["text"])
PY
```

## 当前限制

1. v1 是候选框检测器，不是最终分类器。
2. Hem-o-lok 和钛夹暂未做稳定二分类。
3. 训练标签来自 LocateAnything 伪标签，存在噪声。
4. 对小高光、器械边缘、白色聚合物夹的区分仍需二阶段分类或人工标注微调。
5. 当前验证集与训练标签同源，不能代表真实临床泛化能力。

## 下一步

推荐升级为人工修正小样本训练：

```text
1. 从现有专项视频和真实测试视频抽 300-500 张关键帧。
2. 用 v1 自动预标注。
3. 人工修正类别：
   - hemolok_clip
   - titanium_clip
   - clip_applier
   - gauze
   - specular_highlight
4. 训练 v2 多类检测器。
5. 将 v2 输出与 phase/CVS/clip-before-cut 规则融合。
```

v1 已经能接入实时窗口分析，用作本地、低延迟的手术夹候选证据。
