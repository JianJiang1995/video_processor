"""
SAM3 API Client - Segmentation Service
Calls the external SAM3 API for image segmentation given bounding boxes.
Uses the bbox outputs from SurgR1 to generate masks.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from PIL import Image
import httpx
import base64
from io import BytesIO
import tempfile
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def parse_bbox_from_surgr1(bbox_text: str) -> List[Dict[str, Any]]:
    """
    Parse bounding box text from SurgR1 output to structured format.
    
    Expected format: "tool_name: bbox (x1,y1), (x2,y2)" or similar
    
    Returns:
        List of bbox dicts with x1, y1, x2, y2, label
    """
    bboxes = []
    
    # Try to parse various formats
    # Format 1: "grasper: (100, 50), (300, 200)"
    pattern1 = r"(\w+):\s*\((\d+),\s*(\d+)\),\s*\((\d+),\s*(\d+)\)"
    matches = re.findall(pattern1, bbox_text)
    
    for match in matches:
        label, x1, y1, x2, y2 = match
        bboxes.append({
            "x1": int(x1),
            "y1": int(y1),
            "x2": int(x2),
            "y2": int(y2),
            "label": label
        })
    
    # Format 2: "bbox (x1,y1), (x2,y2)" with label prefix
    if not bboxes:
        pattern2 = r"bbox\s*\((\d+),\s*(\d+)\),\s*\((\d+),\s*(\d+)\)"
        matches = re.findall(pattern2, bbox_text, re.IGNORECASE)
        
        for i, match in enumerate(matches):
            x1, y1, x2, y2 = match
            bboxes.append({
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "label": f"object_{i}"
            })
    
    return bboxes


class SAM3Client:
    """
    SAM3 API Client
    
    Calls the external SAM3 service for image segmentation.
    Takes bounding boxes (from SurgR1) and generates masks.
    """
    
    def __init__(
        self,
        api_url: str = None,
        timeout: float = 60.0
    ):
        config = load_config()
        sam3_config = config.get("services", {}).get("sam3", {})
        
        self.api_url = (api_url or sam3_config.get("api_url", "http://localhost:9004")).rstrip('/')
        self.timeout = timeout
        self._client = None
        
        logger.info(f"[SAM3Client] Initialized with API: {self.api_url}")
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def check_health(self) -> bool:
        """Check if SAM3 service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[SAM3Client] Health check failed: {e}")
            return False
    
    def _save_temp_image(self, image: Union[Image.Image, bytes]) -> str:
        """Save image to temporary file and return path"""
        if isinstance(image, bytes):
            image = Image.open(BytesIO(image))
        
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        
        # Create temp file
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        image.save(temp_file.name, format='PNG')
        return temp_file.name
    
    async def segment_with_bboxes(
        self,
        image_path: str,
        bboxes: List[Dict[str, Any]],
        output_dir: Optional[str] = None,
        alpha: Optional[float] = None,
        contour_thickness: Optional[int] = None,
        return_base64: bool = True
    ) -> Dict[str, Any]:
        """
        Segment image using bounding boxes.
        
        Args:
            image_path: Path to the input image
            bboxes: List of bounding boxes with x1, y1, x2, y2, label
            output_dir: Optional output directory
            alpha: Mask transparency (0.0-1.0)
            contour_thickness: Contour line thickness
            return_base64: Whether to return base64 encoded result image
            
        Returns:
            Dict with segmentation results
        """
        try:
            payload = {
                "image_input_path": image_path,
                "bboxes": bboxes,
                "return_base64": return_base64
            }
            
            if output_dir:
                payload["output_dir"] = output_dir
            if alpha is not None:
                payload["alpha"] = alpha
            if contour_thickness is not None:
                payload["contour_thickness"] = contour_thickness
            
            response = await self.client.post(
                f"{self.api_url}/sam3",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            
            return {
                "success": data.get("success", False),
                "output_path": data.get("output_path", ""),
                "num_objects": data.get("num_objects", 0),
                "masks": data.get("masks", []),
                "message": data.get("message", ""),
                "image_base64": data.get("image_base64"),
                "image_format": data.get("image_format", "png")
            }
                
        except Exception as e:
            logger.error(f"[SAM3Client] Segmentation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "output_path": "",
                "num_objects": 0,
                "masks": []
            }
    
    async def segment_from_surgr1(
        self,
        image: Union[str, Image.Image, bytes],
        surgr1_bbox_output: str,
        alpha: float = 0.3,
        return_base64: bool = True
    ) -> Dict[str, Any]:
        """
        Segment image using SurgR1's bounding box output.
        
        This is a convenience method that parses SurgR1's text output
        and calls the SAM3 API.
        
        Args:
            image: Image (path, PIL Image, or bytes)
            surgr1_bbox_output: Raw text output from SurgR1's tool_localization
            alpha: Mask transparency
            return_base64: Whether to return base64 encoded result
            
        Returns:
            Dict with segmentation results
        """
        # Parse bboxes from SurgR1 output
        bboxes = parse_bbox_from_surgr1(surgr1_bbox_output)
        
        if not bboxes:
            logger.warning("[SAM3Client] No bboxes parsed from SurgR1 output")
            return {
                "success": False,
                "error": "No bounding boxes found in SurgR1 output",
                "parsed_text": surgr1_bbox_output[:200]
            }
        
        # Convert image to path if needed
        if isinstance(image, (Image.Image, bytes)):
            image_path = self._save_temp_image(image)
            cleanup_path = image_path
        else:
            image_path = image
            cleanup_path = None
        
        try:
            result = await self.segment_with_bboxes(
                image_path=image_path,
                bboxes=bboxes,
                alpha=alpha,
                return_base64=return_base64
            )
            
            result["parsed_bboxes"] = bboxes
            return result
            
        finally:
            # Cleanup temp file
            if cleanup_path:
                try:
                    Path(cleanup_path).unlink(missing_ok=True)
                except:
                    pass
    
    async def get_config(self) -> Dict[str, Any]:
        """Get SAM3 service configuration"""
        try:
            response = await self.client.get(f"{self.api_url}/config")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"[SAM3Client] Failed to get config: {e}")
            return {}
    
    # =========================================================================
    # Streaming API - for real-time video segmentation with mask propagation
    # =========================================================================
    
    async def create_stream_session(self, stream_id: str) -> Dict[str, Any]:
        """
        Create a streaming session for real-time video segmentation.
        
        This maintains tracking state across frames, enabling mask propagation.
        
        Args:
            stream_id: Unique identifier for the video stream
            
        Returns:
            Dict with session_id for subsequent calls
        """
        try:
            response = await self.client.post(
                f"{self.api_url}/stream/create",
                json={"stream_id": stream_id}
            )
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"[SAM3Client] Stream session created: {data.get('session_id')}")
            return data
            
        except Exception as e:
            logger.error(f"[SAM3Client] Failed to create stream session: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def process_stream_frame(
        self,
        session_id: str,
        frame: Union[Image.Image, bytes],
        frame_idx: int,
        timestamp: float = 0.0,
        bboxes: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Process a frame in streaming mode.
        
        If bboxes are provided (from SurgR1), new masks are generated.
        Otherwise, masks are propagated from previous frames.
        
        Args:
            session_id: Streaming session ID
            frame: Frame image (PIL Image or bytes)
            frame_idx: Frame index
            timestamp: Timestamp in seconds
            bboxes: Optional list of bboxes from SurgR1
            
        Returns:
            Dict with segmented frame as base64 image
        """
        try:
            # Convert frame to base64
            if isinstance(frame, bytes):
                frame = Image.open(BytesIO(frame))
            
            if frame.mode == 'RGBA':
                frame = frame.convert('RGB')
            
            buffer = BytesIO()
            frame.save(buffer, format='JPEG', quality=85)
            frame_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            # Build request
            payload = {
                "session_id": session_id,
                "frame_base64": frame_base64,
                "frame_idx": frame_idx,
                "timestamp": timestamp
            }
            
            if bboxes:
                payload["bboxes"] = bboxes
            
            response = await self.client.post(
                f"{self.api_url}/stream/process",
                json=payload
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"[SAM3Client] Stream frame processing failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def close_stream_session(self, session_id: str) -> Dict[str, Any]:
        """Close a streaming session"""
        try:
            response = await self.client.delete(f"{self.api_url}/stream/{session_id}")
            response.raise_for_status()
            
            logger.info(f"[SAM3Client] Stream session closed: {session_id}")
            return response.json()
            
        except Exception as e:
            logger.error(f"[SAM3Client] Failed to close stream session: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_stream_status(self, session_id: str) -> Dict[str, Any]:
        """Get streaming session status"""
        try:
            response = await self.client.get(f"{self.api_url}/stream/status/{session_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"[SAM3Client] Failed to get stream status: {e}")
            return {"exists": False}


# Global client instance
_sam3_client: Optional[SAM3Client] = None


def get_sam3_client() -> SAM3Client:
    """Get the global SAM3 client instance"""
    global _sam3_client
    if _sam3_client is None:
        _sam3_client = SAM3Client()
    return _sam3_client


async def ensure_sam3_available() -> SAM3Client:
    """Get client and verify service is available"""
    client = get_sam3_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[SAM3Client] SAM3 service may not be available")
    
    return client

