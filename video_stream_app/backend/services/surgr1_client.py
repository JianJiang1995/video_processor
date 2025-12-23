"""
SurgR1 API Client - Surgical Image Analysis Service
Calls the external SurgR1 API for image analysis with three questions:
1. Tool localization (bounding boxes)
2. Surgical action description
3. Surgical phase identification
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Union
from PIL import Image
import httpx
import base64
from io import BytesIO
import tempfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Try to import API logger
try:
    from ..middleware import log_surgr1_call
except ImportError:
    def log_surgr1_call(*args, **kwargs):
        pass

# Load config
CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


def load_config() -> dict:
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


class SurgR1Client:
    """
    SurgR1 API Client
    
    Calls the external SurgR1 service for surgical image analysis.
    Each image is analyzed with 3 questions:
    1. Tool localization (bounding boxes)
    2. Surgical action description  
    3. Surgical phase identification
    """
    
    def __init__(
        self,
        api_url: str = None,
        timeout: float = 120.0
    ):
        config = load_config()
        surgr1_config = config.get("services", {}).get("surgr1", {})
        
        self.api_url = (api_url or surgr1_config.get("api_url", "http://localhost:9003")).rstrip('/')
        self.timeout = timeout
        self._client = None
        
        logger.info(f"[SurgR1Client] Initialized with API: {self.api_url}")
    
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
        """Check if SurgR1 service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[SurgR1Client] Health check failed: {e}")
            return False
    
    def _save_temp_image(self, image: Union[Image.Image, bytes]) -> str:
        """Save image to temporary file and return path"""
        if isinstance(image, bytes):
            image = Image.open(BytesIO(image))
        
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        
        # Create temp file
        temp_file = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        image.save(temp_file.name, format='JPEG', quality=85)
        return temp_file.name
    
    async def analyze_image(
        self,
        image_path: str,
        questions: Optional[List[str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Analyze a single image with surgical questions.
        
        Args:
            image_path: Path to the image file
            questions: Optional custom questions (defaults to 3 standard questions)
            temperature: Optional sampling temperature
            max_tokens: Optional max tokens
            
        Returns:
            Dict with analysis results for each question
        """
        start_time = time.time()
        
        try:
            payload = {
                "image_paths": [image_path]
            }
            
            if questions:
                payload["questions"] = questions
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            
            response = await self.client.post(
                f"{self.api_url}/analyze",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            duration_ms = (time.time() - start_time) * 1000
            
            if data.get("results") and len(data["results"]) > 0:
                result = data["results"][0]
                responses = result.get("responses", {})
                
                # Log the API call
                log_surgr1_call(
                    image_path=image_path,
                    response={
                        "phase": responses.get("surgical_phase", "")[:50],
                        "action": responses.get("surgical_action", "")[:50]
                    },
                    duration_ms=duration_ms
                )
                
                return {
                    "success": True,
                    "image_path": result.get("image_path", image_path),
                    "responses": responses,
                    "total_questions": data.get("total_questions", 0),
                    "duration_ms": duration_ms
                }
            else:
                log_surgr1_call(
                    image_path=image_path,
                    response={},
                    duration_ms=duration_ms,
                    error="No results returned"
                )
                return {
                    "success": False,
                    "error": "No results returned",
                    "image_path": image_path
                }
                
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            log_surgr1_call(
                image_path=image_path,
                response={},
                duration_ms=duration_ms,
                error=str(e)
            )
            logger.error(f"[SurgR1Client] Analysis failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "image_path": image_path
            }
    
    async def analyze_batch(
        self,
        image_paths: List[str],
        questions: Optional[List[str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Analyze a batch of images.
        
        Args:
            image_paths: List of image file paths
            questions: Optional custom questions
            temperature: Optional sampling temperature
            max_tokens: Optional max tokens
            
        Returns:
            Dict with batch analysis results
        """
        try:
            payload = {
                "image_paths": image_paths
            }
            
            if questions:
                payload["questions"] = questions
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            
            response = await self.client.post(
                f"{self.api_url}/analyze",
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            
            return {
                "success": True,
                "results": data.get("results", []),
                "total_images": data.get("total_images", 0),
                "total_questions": data.get("total_questions", 0)
            }
                
        except Exception as e:
            logger.error(f"[SurgR1Client] Batch analysis failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "results": []
            }
    
    async def analyze_frame(
        self,
        image: Union[str, Image.Image, bytes],
        analysis_type: str = "all",
        session_id: str = None,
        frame_idx: int = None,
        timestamp: float = None,
        save_to_mysql: bool = True
    ) -> Dict[str, str]:
        """
        Analyze a surgical frame with predefined questions.
        
        Args:
            image: Frame image (path, PIL Image, or bytes)
            analysis_type: "all", "phase", "action", or "tools"
            session_id: Optional session ID for MySQL storage
            frame_idx: Optional frame index
            timestamp: Optional video timestamp
            save_to_mysql: Whether to save results to MySQL
            
        Returns:
            Dict with analysis results
        """
        # Convert image to path if needed
        if isinstance(image, (Image.Image, bytes)):
            image_path = self._save_temp_image(image)
            cleanup_path = image_path
        else:
            image_path = image
            cleanup_path = None
        
        try:
            # Map analysis type to questions
            question_map = {
                "phase": ["surgical_phase"],
                "action": ["surgical_action"],
                "tools": ["tool_localization"],
                "all": None  # Use default 3 questions
            }
            
            custom_questions = None
            if analysis_type != "all":
                config = load_config()
                questions_config = config.get("analysis", {}).get("questions", {})
                selected_keys = question_map.get(analysis_type, [])
                custom_questions = [questions_config[k] for k in selected_keys if k in questions_config]
            
            result = await self.analyze_image(image_path, questions=custom_questions)
            
            if result.get("success"):
                responses = result.get("responses", {})
                
                # Map response keys to standard names
                analysis_result = {
                    "phase": responses.get("surgical_phase", ""),
                    "action": responses.get("surgical_action", ""),
                    "tools": responses.get("tool_localization", "")
                }
                
                # Save to MySQL if enabled
                if save_to_mysql and session_id:
                    try:
                        from .mysql_service import get_mysql_service
                        mysql = get_mysql_service()
                        mysql.save_analysis(
                            session_id=session_id,
                            tool_localization=analysis_result["tools"],
                            surgical_action=analysis_result["action"],
                            surgical_phase=analysis_result["phase"],
                            image_path=image_path,
                            frame_idx=frame_idx,
                            timestamp=timestamp,
                            analysis_type="frame"
                        )
                        logger.debug(f"[SurgR1Client] Saved analysis to MySQL: session={session_id}")
                    except Exception as e:
                        logger.warning(f"[SurgR1Client] Failed to save to MySQL: {e}")
                
                return analysis_result
            else:
                return {
                    "phase": f"[Error: {result.get('error', 'Unknown')}]",
                    "action": "",
                    "tools": ""
                }
                
        finally:
            # Cleanup temp file
            if cleanup_path:
                try:
                    Path(cleanup_path).unlink(missing_ok=True)
                except:
                    pass


# Global client instance
_surgr1_client: Optional[SurgR1Client] = None


def get_surgr1_client() -> SurgR1Client:
    """Get the global SurgR1 client instance"""
    global _surgr1_client
    if _surgr1_client is None:
        _surgr1_client = SurgR1Client()
    return _surgr1_client


async def ensure_surgr1_available() -> SurgR1Client:
    """Get client and verify service is available"""
    client = get_surgr1_client()
    
    is_healthy = await client.check_health()
    if not is_healthy:
        logger.warning("[SurgR1Client] SurgR1 service may not be available")
    
    return client

