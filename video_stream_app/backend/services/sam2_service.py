"""
SAM2 (Segment Anything Model 2) Service
Provides surgical instrument segmentation and mask visualization
"""
import base64
from io import BytesIO
from typing import List, Optional, Dict, Any, Tuple
from PIL import Image
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SAM2Service:
    """
    SAM2-based segmentation service for surgical instruments
    
    Provides:
    - Automatic instrument detection and segmentation
    - Mask visualization overlays
    - Multi-object tracking across frames
    """
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        device: str = "cuda:0",
        model_type: str = "sam2_hiera_large"
    ):
        """
        Initialize SAM2 Service
        
        Args:
            model_path: Path to SAM2 model checkpoint
            device: Device to run inference on
            model_type: SAM2 model variant
        """
        self.model_path = model_path
        self.device = device
        self.model_type = model_type
        self._model = None
        self._predictor = None
        self._is_loaded = False
        
    @property
    def is_available(self) -> bool:
        """Check if SAM2 is available"""
        try:
            # Check if sam2 package is installed
            import sam2
            return True
        except ImportError:
            return False
    
    def load_model(self):
        """Load SAM2 model"""
        if self._is_loaded:
            logger.info("SAM2 model already loaded")
            return
        
        if not self.is_available:
            logger.warning("SAM2 not available. Install with: pip install sam2")
            return
        
        if not self.model_path:
            logger.warning("SAM2 model path not specified")
            return
        
        try:
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            
            logger.info(f"Loading SAM2 model: {self.model_type}")
            
            self._model = build_sam2(
                config_file=f"sam2_{self.model_type}.yaml",
                ckpt_path=self.model_path,
                device=self.device
            )
            
            self._predictor = SAM2ImagePredictor(self._model)
            self._is_loaded = True
            
            logger.info("SAM2 model loaded successfully")
            
        except Exception as e:
            logger.error(f"Failed to load SAM2 model: {e}")
            self._is_loaded = False
    
    def unload_model(self):
        """Unload SAM2 model to free memory"""
        if self._model is not None:
            del self._model
            del self._predictor
            self._model = None
            self._predictor = None
            self._is_loaded = False
            
            import torch
            torch.cuda.empty_cache()
            logger.info("SAM2 model unloaded")
    
    def segment_image(
        self,
        image: Image.Image,
        point_coords: Optional[np.ndarray] = None,
        point_labels: Optional[np.ndarray] = None,
        box: Optional[np.ndarray] = None,
        multimask_output: bool = True
    ) -> Dict[str, Any]:
        """
        Segment objects in an image
        
        Args:
            image: Input PIL image
            point_coords: Point prompts (N, 2) array
            point_labels: Point labels (N,) array, 1=foreground, 0=background
            box: Box prompt (4,) array [x1, y1, x2, y2]
            multimask_output: Whether to return multiple masks
            
        Returns:
            Dict with masks, scores, and visualization
        """
        if not self._is_loaded:
            return self._mock_segmentation(image)
        
        try:
            # Convert to numpy
            image_np = np.array(image)
            
            # Set image for predictor
            self._predictor.set_image(image_np)
            
            # Run prediction
            masks, scores, logits = self._predictor.predict(
                point_coords=point_coords,
                point_labels=point_labels,
                box=box,
                multimask_output=multimask_output
            )
            
            # Get best mask
            best_idx = np.argmax(scores)
            best_mask = masks[best_idx]
            
            # Create visualization
            overlay = self._create_mask_overlay(image_np, best_mask)
            
            return {
                "success": True,
                "masks": masks.tolist(),
                "scores": scores.tolist(),
                "best_mask_idx": int(best_idx),
                "overlay_base64": self._image_to_base64(overlay)
            }
            
        except Exception as e:
            logger.error(f"SAM2 segmentation error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def auto_segment_instruments(
        self,
        image: Image.Image
    ) -> Dict[str, Any]:
        """
        Automatically detect and segment surgical instruments
        
        Uses automatic point generation to find instrument-like objects
        
        Args:
            image: Input image
            
        Returns:
            Dict with detected instruments and masks
        """
        if not self._is_loaded:
            return self._mock_instrument_detection(image)
        
        try:
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
            
            # Create automatic mask generator
            mask_generator = SAM2AutomaticMaskGenerator(
                model=self._model,
                points_per_side=32,
                pred_iou_thresh=0.86,
                stability_score_thresh=0.92,
                crop_n_layers=1,
                min_mask_region_area=100
            )
            
            image_np = np.array(image)
            masks = mask_generator.generate(image_np)
            
            # Filter for instrument-like masks (elongated, in center region)
            instruments = self._filter_instrument_masks(masks, image_np.shape)
            
            # Create combined overlay
            overlay = self._create_multi_mask_overlay(image_np, instruments)
            
            return {
                "success": True,
                "num_instruments": len(instruments),
                "instruments": instruments,
                "overlay_base64": self._image_to_base64(overlay)
            }
            
        except Exception as e:
            logger.error(f"Auto segmentation error: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _filter_instrument_masks(
        self,
        masks: List[Dict],
        image_shape: Tuple[int, int, int]
    ) -> List[Dict]:
        """Filter masks to find surgical instruments"""
        instruments = []
        h, w = image_shape[:2]
        
        for mask_data in masks:
            mask = mask_data['segmentation']
            bbox = mask_data['bbox']  # x, y, w, h
            area = mask_data['area']
            
            # Filter criteria for instruments:
            # 1. Reasonable size (not too small, not too large)
            if area < 500 or area > (h * w * 0.5):
                continue
            
            # 2. Elongated shape (aspect ratio)
            aspect_ratio = bbox[2] / max(bbox[3], 1)
            if aspect_ratio < 1.5 and (1/aspect_ratio) < 1.5:
                # Too square, likely not an instrument
                continue
            
            instruments.append({
                "bbox": bbox,
                "area": int(area),
                "score": float(mask_data.get('predicted_iou', 0.9)),
                "mask": mask
            })
        
        return instruments[:5]  # Return top 5 instrument candidates
    
    def _create_mask_overlay(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        color: Tuple[int, int, int] = (0, 255, 0),
        alpha: float = 0.4
    ) -> Image.Image:
        """Create overlay visualization of mask on image"""
        overlay = image.copy()
        
        # Create colored mask
        colored_mask = np.zeros_like(overlay)
        colored_mask[mask] = color
        
        # Blend
        overlay = (overlay * (1 - alpha) + colored_mask * alpha).astype(np.uint8)
        
        # Add mask boundary
        import cv2
        contours, _ = cv2.findContours(
            mask.astype(np.uint8),
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )
        cv2.drawContours(overlay, contours, -1, color, 2)
        
        return Image.fromarray(overlay)
    
    def _create_multi_mask_overlay(
        self,
        image: np.ndarray,
        instruments: List[Dict],
        alpha: float = 0.4
    ) -> Image.Image:
        """Create overlay with multiple instrument masks"""
        colors = [
            (255, 0, 0),    # Red
            (0, 255, 0),    # Green
            (0, 0, 255),    # Blue
            (255, 255, 0),  # Yellow
            (255, 0, 255),  # Magenta
        ]
        
        overlay = image.copy().astype(float)
        
        for i, inst in enumerate(instruments):
            mask = inst['mask']
            color = colors[i % len(colors)]
            
            colored_mask = np.zeros_like(overlay)
            colored_mask[mask] = color
            
            overlay = overlay * (1 - alpha * mask[:, :, np.newaxis]) + colored_mask * alpha
        
        return Image.fromarray(overlay.astype(np.uint8))
    
    def _mock_segmentation(self, image: Image.Image) -> Dict[str, Any]:
        """Mock segmentation when SAM2 is not available"""
        logger.info("Using mock segmentation (SAM2 not loaded)")
        
        # Return the original image with a note
        return {
            "success": True,
            "mock": True,
            "message": "SAM2 not available. This is a placeholder.",
            "overlay_base64": self._image_to_base64(image)
        }
    
    def _mock_instrument_detection(self, image: Image.Image) -> Dict[str, Any]:
        """Mock instrument detection"""
        return {
            "success": True,
            "mock": True,
            "num_instruments": 0,
            "instruments": [],
            "message": "SAM2 not available. No instruments detected.",
            "overlay_base64": self._image_to_base64(image)
        }
    
    def _image_to_base64(self, image: Image.Image) -> str:
        """Convert image to base64"""
        if isinstance(image, np.ndarray):
            image = Image.fromarray(image)
        
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode()


# Factory function
def create_sam2_service(
    model_path: str = None,
    device: str = "cuda:0"
) -> SAM2Service:
    """Create SAM2 service instance"""
    service = SAM2Service(model_path=model_path, device=device)
    if model_path:
        service.load_model()
    return service




