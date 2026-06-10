"""
SurgR1 API Client - Surgical Image Analysis Service
Calls the external SurgR1 API for image analysis with three questions:
1. Tool localization (bounding boxes)
2. Surgical action description
3. Surgical phase identification

支持并发批量处理:
- analyze_frames_concurrent: 并发分析多帧
- 使用 asyncio.Semaphore 控制并发数
- 自动重试失败的请求
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple
from PIL import Image
import httpx
import base64
from io import BytesIO
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

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


@dataclass
class ConcurrentConfig:
    """并发处理配置"""
    max_concurrent: int = 3  # 最大并发请求数
    retry_count: int = 2     # 失败重试次数
    retry_delay: float = 0.5 # 重试延迟（秒）


class SurgR1Client:
    """
    SurgR1 API Client
    
    Calls the external SurgR1 service for surgical image analysis.
    Each image is analyzed with 3 questions:
    1. Tool localization (bounding boxes)
    2. Surgical action description  
    3. Surgical phase identification
    
    并发特性:
    - analyze_frames_concurrent: 使用 asyncio.gather 并发分析
    - 自动 Semaphore 控制并发数防止过载
    - 支持失败重试
    - 支持按会话取消正在进行的请求
    
    配置从 config.json 的 services.surgr1 读取:
    - max_concurrent: 最大并发数
    - retry_count: 重试次数
    """
    
    def __init__(
        self,
        api_url: str = None,
        timeout: float = 120.0,
        max_concurrent: int = None  # None 表示从配置读取
    ):
        config = load_config()
        surgr1_config = config.get("services", {}).get("surgr1", {})
        
        self.api_url = (api_url or surgr1_config.get("api_url", "http://localhost:9003")).rstrip('/')
        self.timeout = timeout
        self._client = None
        
        # 从配置读取并发参数
        config_max_concurrent = surgr1_config.get("max_concurrent", 3)
        config_retry_count = surgr1_config.get("retry_count", 2)
        
        # 使用传入参数或配置值
        actual_max_concurrent = max_concurrent if max_concurrent is not None else config_max_concurrent
        
        # 并发配置
        self.concurrent_config = ConcurrentConfig(
            max_concurrent=actual_max_concurrent,
            retry_count=config_retry_count
        )
        self._semaphore = asyncio.Semaphore(actual_max_concurrent)
        
        # 会话取消标志: session_id -> bool (True = 已取消)
        self._cancelled_sessions: Dict[str, bool] = {}
        
        logger.info(f"[SurgR1Client] Initialized with API: {self.api_url}, max_concurrent: {actual_max_concurrent}, retry_count: {config_retry_count}")
    
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
    
    async def cancel_session(self, session_id: str):
        """
        取消指定会话的所有待处理请求
        
        这会设置一个取消标志，使得后续的分析请求会立即返回取消状态。
        注意：已经在 vLLM 中处理的请求无法取消，但可以防止新请求被发送。
        
        Args:
            session_id: 要取消的会话 ID
        """
        self._cancelled_sessions[session_id] = True
        logger.info(f"[SurgR1Client] Session {session_id} marked as cancelled")
    
    def is_session_cancelled(self, session_id: str) -> bool:
        """检查会话是否已被取消"""
        return self._cancelled_sessions.get(session_id, False)
    
    def clear_cancellation(self, session_id: str):
        """清除会话的取消标志（用于开始新的处理）"""
        self._cancelled_sessions.pop(session_id, None)
    
    async def check_health(self) -> bool:
        """Check if SurgR1 service is available"""
        try:
            response = await self.client.get(f"{self.api_url}/health", timeout=2.0)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[SurgR1Client] Health check failed: {e}")

        # SurgR1/vLLM can delay /health while a large batch is running.  If the
        # API port is still accepting TCP connections, keep the UI from marking
        # the model as unavailable during active inference.
        try:
            parsed = urlparse(self.api_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=1.0,
            )
            writer.close()
            await writer.wait_closed()
            logger.info("[SurgR1Client] Health endpoint delayed, but API port is reachable")
            return True
        except Exception as tcp_error:
            logger.warning(f"[SurgR1Client] TCP health fallback failed: {tcp_error}")
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
    
    async def analyze_frames_batch(
        self,
        frames: List[Dict[str, Any]],
        analysis_type: str = "all",
        session_id: str = None,
        save_to_mysql: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Analyze multiple frames in a single batch request.
        
        This is much faster than analyzing frames one by one because:
        1. Single HTTP request overhead
        2. GPU can batch process multiple images more efficiently
        
        Args:
            frames: List of dicts with keys: image (PIL/bytes/path), frame_idx, timestamp
            analysis_type: "all", "phase", "action", or "tools"
            session_id: Optional session ID for MySQL storage
            save_to_mysql: Whether to save results to MySQL
            
        Returns:
            List of analysis results for each frame
        """
        if not frames:
            return []
        
        # Check if session is cancelled before processing
        if session_id and self.is_session_cancelled(session_id):
            logger.info(f"[SurgR1Client] Skipping batch - session {session_id} cancelled")
            return [{
                "frame_idx": f.get("frame_idx"),
                "timestamp": f.get("timestamp"),
                "phase": "[Cancelled]",
                "action": "",
                "tools": "",
                "cancelled": True
            } for f in frames]
        
        # Convert all images to temp file paths
        image_paths = []
        cleanup_paths = []
        frame_info = []  # Store frame_idx and timestamp for each image
        
        for frame_data in frames:
            image = frame_data.get("image")
            if isinstance(image, (Image.Image, bytes)):
                image_path = self._save_temp_image(image)
                cleanup_paths.append(image_path)
            else:
                image_path = image
            image_paths.append(image_path)
            frame_info.append({
                "frame_idx": frame_data.get("frame_idx"),
                "timestamp": frame_data.get("timestamp")
            })
        
        try:
            # Pipeline v4: SurgR1 只问 Phase；action 由 Phase/Triplet Expert 回答，
            # tools 由 YOLO Expert 回答。"all" 与 "phase" 等价。
            config = load_config()
            questions_config = config.get("analysis", {}).get("questions", {})
            question_map = {
                "phase": ["surgical_phase"],
                "action": ["surgical_action"],  # 保留以备单独调用
                "tools": ["tool_localization"],
                "all": ["surgical_phase"],
            }
            selected_keys = question_map.get(analysis_type, ["surgical_phase"])
            custom_questions = [questions_config[k] for k in selected_keys if k in questions_config]

            # Single batch API call
            batch_result = await self.analyze_batch(
                image_paths=image_paths,
                questions=custom_questions
            )
            
            results = []
            
            if batch_result.get("success") and batch_result.get("results"):
                api_results = batch_result["results"]
                
                for i, api_result in enumerate(api_results):
                    responses = api_result.get("responses", {})
                    
                    analysis_result = {
                        "frame_idx": frame_info[i]["frame_idx"],
                        "timestamp": frame_info[i]["timestamp"],
                        "phase": responses.get("surgical_phase", ""),
                        "action": responses.get("surgical_action", ""),
                        "tools": responses.get("tool_localization", "")
                    }
                    results.append(analysis_result)
                    
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
                                image_path=image_paths[i],
                                frame_idx=frame_info[i]["frame_idx"],
                                timestamp=frame_info[i]["timestamp"],
                                analysis_type="frame"
                            )
                        except Exception as e:
                            logger.warning(f"[SurgR1Client] Failed to save to MySQL: {e}")
                
                logger.info(f"[SurgR1Client] Batch analyzed {len(results)} frames successfully")
            else:
                # Return empty results on failure
                for i in range(len(frames)):
                    results.append({
                        "frame_idx": frame_info[i]["frame_idx"],
                        "timestamp": frame_info[i]["timestamp"],
                        "phase": f"[Error: {batch_result.get('error', 'Batch failed')}]",
                        "action": "",
                        "tools": ""
                    })
            
            return results
            
        finally:
            # Cleanup temp files
            for path in cleanup_paths:
                try:
                    Path(path).unlink(missing_ok=True)
                except:
                    pass

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
        # Check if session is cancelled before processing
        if session_id and self.is_session_cancelled(session_id):
            logger.debug(f"[SurgR1Client] Skipping frame analysis - session {session_id} cancelled")
            return {
                "phase": "[Cancelled]",
                "action": "",
                "tools": "",
                "cancelled": True
            }
        
        # Convert image to path if needed
        if isinstance(image, (Image.Image, bytes)):
            image_path = self._save_temp_image(image)
            cleanup_path = image_path
        else:
            image_path = image
            cleanup_path = None
        
        try:
            # Pipeline v4: SurgR1 只问 Phase；"all" 映射到 phase。
            config = load_config()
            questions_config = config.get("analysis", {}).get("questions", {})
            question_map = {
                "phase": ["surgical_phase"],
                "action": ["surgical_action"],
                "tools": ["tool_localization"],
                "all": ["surgical_phase"],
            }
            selected_keys = question_map.get(analysis_type, ["surgical_phase"])
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
    
    async def analyze_frames_concurrent(
        self,
        frames: List[Dict[str, Any]],
        analysis_type: str = "all",
        session_id: str = None,
        save_to_mysql: bool = True,
        max_concurrent: int = None
    ) -> List[Dict[str, Any]]:
        """
        并发分析多帧 - 高性能版本
        
        使用 asyncio.gather + Semaphore 并发处理多个帧，
        比串行处理快 N 倍（N = 并发数）
        
        Args:
            frames: 帧列表，每个包含 image, frame_idx, timestamp
            analysis_type: 分析类型 "all", "phase", "action", "tools"
            session_id: 会话 ID
            save_to_mysql: 是否保存到数据库
            max_concurrent: 最大并发数，None 使用默认配置
            
        Returns:
            分析结果列表（按原始顺序）
        """
        if not frames:
            return []
        
        # Check if session is cancelled before starting
        if session_id and self.is_session_cancelled(session_id):
            logger.info(f"[SurgR1Client] Skipping batch analysis - session {session_id} cancelled")
            return [{
                "frame_idx": f.get("frame_idx"),
                "timestamp": f.get("timestamp"),
                "success": False,
                "cancelled": True,
                "phase": "[Cancelled]",
                "action": "",
                "tools": ""
            } for f in frames]
        
        # 使用指定的并发数或默认配置
        semaphore = self._semaphore
        if max_concurrent and max_concurrent != self.concurrent_config.max_concurrent:
            semaphore = asyncio.Semaphore(max_concurrent)
        
        start_time = time.time()
        
        async def analyze_with_semaphore(frame_data: Dict[str, Any], index: int) -> Tuple[int, Dict[str, Any]]:
            """带信号量控制的单帧分析"""
            async with semaphore:
                try:
                    result = await self.analyze_frame(
                        image=frame_data.get("image"),
                        analysis_type=analysis_type,
                        session_id=session_id,
                        frame_idx=frame_data.get("frame_idx"),
                        timestamp=frame_data.get("timestamp"),
                        save_to_mysql=save_to_mysql
                    )
                    return index, {
                        "frame_idx": frame_data.get("frame_idx"),
                        "timestamp": frame_data.get("timestamp"),
                        "success": True,
                        **result
                    }
                except Exception as e:
                    logger.warning(f"[SurgR1Client] Concurrent analysis failed for frame {frame_data.get('frame_idx')}: {e}")
                    return index, {
                        "frame_idx": frame_data.get("frame_idx"),
                        "timestamp": frame_data.get("timestamp"),
                        "success": False,
                        "error": str(e),
                        "phase": f"[Error: {str(e)}]",
                        "action": "",
                        "tools": ""
                    }
        
        # 创建所有任务并并发执行
        tasks = [
            analyze_with_semaphore(frame, i) 
            for i, frame in enumerate(frames)
        ]
        
        # 并发执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 按原始顺序排序结果
        sorted_results = [None] * len(frames)
        success_count = 0
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"[SurgR1Client] Unexpected error in concurrent analysis: {result}")
                continue
            
            index, data = result
            sorted_results[index] = data
            if data.get("success"):
                success_count += 1
        
        # 填充失败的结果
        for i, r in enumerate(sorted_results):
            if r is None:
                sorted_results[i] = {
                    "frame_idx": frames[i].get("frame_idx"),
                    "timestamp": frames[i].get("timestamp"),
                    "success": False,
                    "error": "Unknown error",
                    "phase": "",
                    "action": "",
                    "tools": ""
                }
        
        elapsed = time.time() - start_time
        logger.info(
            f"[SurgR1Client] Concurrent analysis completed: "
            f"{success_count}/{len(frames)} frames in {elapsed:.2f}s "
            f"({len(frames)/elapsed:.1f} fps)"
        )
        
        return sorted_results
    
    async def analyze_frames_concurrent_with_retry(
        self,
        frames: List[Dict[str, Any]],
        analysis_type: str = "all",
        session_id: str = None,
        save_to_mysql: bool = True,
        max_retries: int = 2,
        retry_delay: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        带重试的并发帧分析
        
        失败的帧会自动重试，最多重试 max_retries 次
        
        Args:
            frames: 帧列表
            analysis_type: 分析类型
            session_id: 会话 ID
            save_to_mysql: 是否保存到数据库
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
            
        Returns:
            分析结果列表
        """
        # 第一轮并发分析
        results = await self.analyze_frames_concurrent(
            frames=frames,
            analysis_type=analysis_type,
            session_id=session_id,
            save_to_mysql=save_to_mysql
        )
        
        # 收集失败的帧
        for retry in range(max_retries):
            failed_indices = [
                i for i, r in enumerate(results) 
                if not r.get("success")
            ]
            
            if not failed_indices:
                break
            
            logger.info(f"[SurgR1Client] Retry {retry + 1}/{max_retries}: {len(failed_indices)} failed frames")
            
            await asyncio.sleep(retry_delay)
            
            # 重试失败的帧
            retry_frames = [frames[i] for i in failed_indices]
            retry_results = await self.analyze_frames_concurrent(
                frames=retry_frames,
                analysis_type=analysis_type,
                session_id=session_id,
                save_to_mysql=save_to_mysql
            )
            
            # 更新结果
            for i, idx in enumerate(failed_indices):
                results[idx] = retry_results[i]
        
        return results


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
