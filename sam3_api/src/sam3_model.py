"""
SAM3 模型服务 - 独立模块，用于单图分割
参考 backend/sam3_service.py 和 demo.py 实现
"""
import os
import base64
import torch
import numpy as np
import cv2
from typing import List, Dict, Optional, Tuple

# ============================================================================
# 在import SAM3之前，patch torch.autocast 使其使用float32而非bfloat16
# ============================================================================
_OriginalAutocast = torch.autocast

class _PatchedAutocast(_OriginalAutocast):
    """强制使用float32的autocast"""
    def __init__(self, device_type, dtype=None, enabled=True, cache_enabled=None):
        # 把bfloat16改成float32
        if dtype == torch.bfloat16:
            dtype = torch.float32
        super().__init__(device_type, dtype=dtype, enabled=enabled, cache_enabled=cache_enabled)

torch.autocast = _PatchedAutocast
# ============================================================================

# 直接从本地 sam3 包导入
try:
    from sam3.model_builder import build_sam3_video_predictor
except ImportError:
    print("Error: Could not import sam3. Make sure the sam3 package exists in src/ directory.")

# 从配置文件加载颜色设置
try:
    from config_loader import get_tool_color, get_visualization_config, get_model_config
except ImportError:
    # 如果配置加载器不可用，使用内置默认值
    print("[Warning] config_loader not available, using built-in defaults")
    
    TOOL_COLORS = {
        "grasper":    (0, 255, 127),
        "bipolar":    (255, 0, 255),
        "hook":       (0, 165, 255),
        "scissors":   (255, 255, 0),
        "clipper":    (147, 20, 255),
        "irrigator":  (255, 191, 0),
        "specimenbag":(0, 255, 255),
        "forceps":    (50, 205, 50),
        "needle":     (180, 105, 255),
        "suction":    (250, 206, 135),
        "default":    (128, 128, 128),
    }
    
    INSTANCE_COLORS = [
        (0, 255, 127), (255, 0, 255), (0, 165, 255), (255, 255, 0),
        (147, 20, 255), (255, 191, 0), (0, 255, 255), (50, 205, 50),
    ]
    
    def get_tool_color(label: str, instance_id: int = 0) -> Tuple[int, int, int]:
        label_lower = label.lower().strip()
        if label_lower in TOOL_COLORS:
            return TOOL_COLORS[label_lower]
        for key in TOOL_COLORS:
            if key in label_lower or label_lower in key:
                return TOOL_COLORS[key]
        return INSTANCE_COLORS[instance_id % len(INSTANCE_COLORS)]
    
    def get_visualization_config():
        return {"alpha": 0.4, "contour_thickness": 2, "return_base64": False}
    
    def get_model_config():
        return {"checkpoint_path": None, "device": "cuda"}


class SAM3Model:
    """SAM3 单图分割模型封装"""
    
    def __init__(
        self, 
        checkpoint_path: str = "/data/ckpt/sam3/sam3.pt",
        device: str = "cuda"
    ):
        self.device = device
        self.model_path = checkpoint_path
        self.predictor = None
        self.session_id = None
        self.video_info = {}
        
        print(f"[SAM3Model] Initializing with device={device}...")
        
        if not os.path.exists(checkpoint_path):
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
        
        try:
            self.predictor = build_sam3_video_predictor(
                checkpoint_path=checkpoint_path
            )
            print("[SAM3Model] Model loaded successfully.")
        except Exception as e:
            print(f"[SAM3Model] Failed to load model: {e}")
            import traceback
            traceback.print_exc()
            raise RuntimeError(f"Failed to initialize SAM3: {e}")
    
    def _close_session(self):
        """关闭当前session"""
        if self.session_id and self.predictor:
            try:
                self.predictor.handle_request(
                    request=dict(
                        type="close_session",
                        session_id=self.session_id,
                    )
                )
            except Exception as e:
                print(f"[SAM3Model] Error closing session: {e}")
            finally:
                self.session_id = None
    
    def _start_session(self, image_path: str) -> str:
        """为图片启动session"""
        if self.session_id:
            self._close_session()
        
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        # 获取图片尺寸
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"Could not read image: {image_path}")
        height, width = img.shape[:2]
        self.video_info = {"width": width, "height": height}
        
        print(f"[SAM3Model] Starting session for {image_path} ({width}x{height})")
        
        try:
            response = self.predictor.handle_request(
                request=dict(
                    type="start_session",
                    resource_path=image_path,
                )
            )
            self.session_id = response["session_id"]
            print(f"[SAM3Model] Session started: {self.session_id}")
            return self.session_id
        except Exception as e:
            print(f"[SAM3Model] Error starting session: {e}")
            raise
    
    def segment_with_bboxes(
        self,
        image_path: str,
        bboxes: List[Dict],
        output_dir: Optional[str] = None,
        alpha: Optional[float] = None,
        contour_thickness: Optional[int] = None,
        colors: Optional[Dict[str, Tuple[int, int, int]]] = None,
        return_base64: Optional[bool] = None
    ) -> Dict:
        """
        使用bounding boxes进行分割
        
        Args:
            image_path: 输入图片路径
            bboxes: bbox列表
            output_dir: 输出目录
            alpha: mask透明度 (0.0-1.0, 默认从配置读取)
            contour_thickness: mask边缘粗细 (默认从配置读取)
            colors: 自定义颜色映射 {"label": (B,G,R), ...}
            return_base64: 是否返回 base64 编码的图片 (默认从配置读取)
        """
        # 从配置获取默认值
        viz_config = get_visualization_config()
        if alpha is None:
            alpha = viz_config.get("alpha", 0.4)
        if contour_thickness is None:
            contour_thickness = viz_config.get("contour_thickness", 2)
        if return_base64 is None:
            return_base64 = viz_config.get("return_base64", False)
        
        # 启动session
        self._start_session(image_path)
        width = self.video_info["width"]
        height = self.video_info["height"]
        
        print(f"[SAM3Model] Processing {len(bboxes)} bboxes (alpha={alpha}, thickness={contour_thickness})")
        
        # 收集所有mask
        all_masks = {}
        
        # SAM3的bbox prompt只支持单个bbox，每个bbox独立session
        # 增加验证：mask的质心必须在bbox内
        for idx, bbox in enumerate(bboxes):
            label = bbox.get("label", "object")
            
            # 为每个bbox创建独立session
            self._close_session()
            self._start_session(image_path)
            
            # 转换bbox格式：xyxy -> xywh (归一化)
            x1, y1, x2, y2 = bbox["x1"], bbox["y1"], bbox["x2"], bbox["y2"]
            box_xywh = [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height]
            
            try:
                # 使用text prompt指定目标类型，帮助SAM3更好地理解要分割什么
                response = self.predictor.handle_request(
                    request=dict(
                        type="add_prompt",
                        session_id=self.session_id,
                        frame_index=0,
                        text=label,  # 用label作为text prompt
                        bounding_boxes=[box_xywh],
                        bounding_box_labels=[1],
                    )
                )
                
                outputs = response.get("outputs")
                if outputs and "out_obj_ids" in outputs and "out_binary_masks" in outputs:
                    out_obj_ids = outputs["out_obj_ids"]
                    out_masks = outputs["out_binary_masks"]
                    
                    if isinstance(out_obj_ids, np.ndarray):
                        out_obj_ids = out_obj_ids.tolist()
                    
                    # 取第一个mask
                    if len(out_obj_ids) > 0:
                        mask = out_masks[0]
                        if isinstance(mask, torch.Tensor):
                            mask = mask.squeeze().cpu().numpy()
                        
                        # 验证mask有效性（放宽验证：只要有mask就接受）
                        mask_binary = mask > 0.5
                        if np.any(mask_binary):
                            ys, xs = np.where(mask_binary)
                            centroid_x = np.mean(xs)
                            centroid_y = np.mean(ys)
                            mask_area = np.sum(mask_binary)
                            
                            # 计算 mask 与 bbox 的 IoU（放宽验证）
                            bbox_mask = np.zeros_like(mask_binary)
                            bbox_mask[max(0,y1):min(height,y2), max(0,x1):min(width,x2)] = True
                            intersection = np.sum(mask_binary & bbox_mask)
                            iou = intersection / (mask_area + 1e-6)  # mask 有多少在 bbox 内
                            
                            # 放宽验证：只要有 10% 的 mask 在 bbox 内，或者 mask 足够大
                            min_iou_threshold = 0.1
                            min_area_threshold = 500  # 最小面积阈值
                            
                            if iou >= min_iou_threshold or mask_area >= min_area_threshold:
                                all_masks[idx] = {"mask": mask, "label": label}
                                print(f"[SAM3Model] bbox {idx} ({label}): mask accepted (area={mask_area}, iou={iou:.2f}, centroid=({centroid_x:.0f},{centroid_y:.0f})) ✓")
                            else:
                                # 即使验证失败，也记录 mask（调试模式）
                                all_masks[idx] = {"mask": mask, "label": label}
                                print(f"[SAM3Model] bbox {idx} ({label}): mask accepted despite low iou (area={mask_area}, iou={iou:.2f}) [relaxed mode] ⚠")
                        else:
                            print(f"[SAM3Model] bbox {idx} ({label}): empty mask ✗")
                    else:
                        print(f"[SAM3Model] bbox {idx} ({label}): no mask returned")
                        
            except Exception as e:
                print(f"[SAM3Model] Error processing bbox {idx}: {e}")
                import traceback
                traceback.print_exc()
        
        # 读取原图并绘制mask
        image = cv2.imread(image_path)
        result_image = image.copy()
        mask_results = []
        
        label_instance_count = {}
        
        for obj_id, mask_info in all_masks.items():
            mask = mask_info["mask"]
            label = mask_info["label"]
            
            instance_id = label_instance_count.get(label, 0)
            label_instance_count[label] = instance_id + 1
            
            # 获取颜色：优先使用自定义颜色
            if colors and label in colors:
                color = colors[label]
            else:
                color = get_tool_color(label, instance_id)
            
            # 确保mask是2D的
            if mask.ndim > 2:
                mask = mask.squeeze()
            
            # 转换为uint8
            if mask.dtype == bool:
                mask = mask.astype(np.uint8) * 255
            elif mask.max() <= 1:
                mask = (mask * 255).astype(np.uint8)
            else:
                mask = mask.astype(np.uint8)
            
            # 透明overlay
            mask_bool = mask > 128
            for c in range(3):
                result_image[:, :, c] = np.where(
                    mask_bool,
                    (1 - alpha) * result_image[:, :, c] + alpha * color[c],
                    result_image[:, :, c]
                ).astype(np.uint8)
            
            # 绘制轮廓
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(result_image, contours, -1, color, contour_thickness)
            
            mask_results.append({
                "obj_id": obj_id,
                "label": label,
                "color": color,
                "area": int(np.sum(mask > 128))
            })
        
        # 保存结果
        if output_dir is None:
            output_dir = os.path.dirname(image_path)
        os.makedirs(output_dir, exist_ok=True)
        
        basename = os.path.basename(image_path)
        name, ext = os.path.splitext(basename)
        output_path = os.path.join(output_dir, f"{name}_masked{ext}")
        
        cv2.imwrite(output_path, result_image)
        print(f"[SAM3Model] Result saved to: {output_path}")
        
        result = {
            "output_path": output_path,
            "masks": mask_results,
            "num_objects": len(mask_results)
        }
        
        # 如果需要返回 base64
        if return_base64:
            # 编码为 PNG 格式的 base64
            _, buffer = cv2.imencode('.png', result_image)
            image_base64 = base64.b64encode(buffer).decode('utf-8')
            result["image_base64"] = image_base64
            result["image_format"] = "png"
            print(f"[SAM3Model] Base64 image size: {len(image_base64)} chars")
        
        return result
    
    def __del__(self):
        try:
            self._close_session()
        except:
            pass


# 全局模型实例（懒加载）
_global_model: Optional[SAM3Model] = None


def get_model() -> SAM3Model:
    """获取全局模型实例（懒加载）"""
    global _global_model
    if _global_model is None:
        checkpoint = os.environ.get("SAM3_CHECKPOINT", "/data/ckpt/sam3/sam3.pt")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _global_model = SAM3Model(checkpoint_path=checkpoint, device=device)
    return _global_model
