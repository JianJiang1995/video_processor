"""
API Logging Middleware
Records API requests, responses and timing information to timestamped log files.
"""
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message
import traceback

# Create logs directory
LOGS_DIR = Path(__file__).parent.parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_api_logger() -> logging.Logger:
    """
    Setup API logger with timestamped file handler.
    Log file format: api_YYYYMMDD_HHMMSS.log
    """
    # Create timestamped log filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"api_{timestamp}.log"
    
    # Create logger
    api_logger = logging.getLogger("api_logger")
    api_logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers
    if api_logger.handlers:
        return api_logger
    
    # File handler with detailed format
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_format = logging.Formatter(
        '%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    
    # Console handler for important logs
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_format)
    
    api_logger.addHandler(file_handler)
    api_logger.addHandler(console_handler)
    
    api_logger.info(f"=" * 80)
    api_logger.info(f"API Logger Started - Log file: {log_file}")
    api_logger.info(f"=" * 80)
    
    return api_logger


# Global logger instance
api_logger = setup_api_logger()


class APILoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log API requests and responses with timing information.
    
    Logs:
    - Request method, path, query params
    - Response status code
    - Processing time (ms)
    - Request/Response body (for JSON APIs)
    - Errors and exceptions
    """
    
    # Paths to skip logging (static assets, etc.)
    SKIP_PATHS = {'/assets', '/favicon.ico', '/_next', '/static', '/sessions'}
    
    # Paths to log body content
    LOG_BODY_PATHS = {'/api/analysis', '/api/video', '/api/model', '/api/voice'}
    
    # Max body size to log (to avoid huge logs)
    MAX_BODY_LOG_SIZE = 2000
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip logging for static assets
        if any(request.url.path.startswith(skip) for skip in self.SKIP_PATHS):
            return await call_next(request)
        
        # Record start time
        start_time = time.time()
        request_id = f"{int(start_time * 1000) % 100000:05d}"
        
        # Get request info
        method = request.method
        path = request.url.path
        query = str(request.query_params) if request.query_params else ""
        client_ip = request.client.host if request.client else "unknown"
        
        # Log request
        log_parts = [
            f"[{request_id}]",
            f"REQ",
            f"{method}",
            f"{path}"
        ]
        if query:
            log_parts.append(f"?{query}")
        log_parts.append(f"| from {client_ip}")
        
        api_logger.info(" ".join(log_parts))
        
        # Try to log request body for POST/PUT
        if method in ['POST', 'PUT', 'PATCH']:
            await self._log_request_body(request, request_id)
        
        # Process request
        response = None
        error_msg = None
        
        try:
            response = await call_next(request)
        except Exception as e:
            error_msg = str(e)
            api_logger.error(f"[{request_id}] ERROR | {traceback.format_exc()}")
            raise
        finally:
            # Calculate processing time
            process_time = (time.time() - start_time) * 1000  # ms
            
            # Log response
            status_code = response.status_code if response else 500
            status_emoji = self._get_status_emoji(status_code)
            
            log_parts = [
                f"[{request_id}]",
                f"RES",
                f"{status_emoji}",
                f"{status_code}",
                f"| {process_time:.2f}ms",
                f"| {method} {path}"
            ]
            
            if error_msg:
                log_parts.append(f"| ERROR: {error_msg[:100]}")
            
            # Color code by timing
            if process_time > 5000:
                api_logger.warning(" ".join(log_parts) + " [SLOW]")
            elif process_time > 1000:
                api_logger.info(" ".join(log_parts) + " [>1s]")
            else:
                api_logger.info(" ".join(log_parts))
        
        return response
    
    async def _log_request_body(self, request: Request, request_id: str):
        """Log request body for specific API paths"""
        try:
            # Only log body for API paths
            if not any(request.url.path.startswith(p) for p in self.LOG_BODY_PATHS):
                return
            
            # Get content type
            content_type = request.headers.get('content-type', '')
            
            if 'application/json' in content_type:
                body = await request.body()
                if body:
                    body_str = body.decode('utf-8', errors='ignore')
                    if len(body_str) > self.MAX_BODY_LOG_SIZE:
                        body_str = body_str[:self.MAX_BODY_LOG_SIZE] + "...[truncated]"
                    
                    try:
                        body_json = json.loads(body_str)
                        # Mask sensitive fields
                        if 'api_key' in body_json:
                            body_json['api_key'] = '***'
                        if 'password' in body_json:
                            body_json['password'] = '***'
                        body_str = json.dumps(body_json, ensure_ascii=False)
                    except:
                        pass
                    
                    api_logger.info(f"[{request_id}] BODY | {body_str}")
            
            elif 'multipart/form-data' in content_type:
                api_logger.info(f"[{request_id}] BODY | [multipart/form-data]")
                
        except Exception as e:
            api_logger.debug(f"[{request_id}] Could not log request body: {e}")
    
    def _get_status_emoji(self, status_code: int) -> str:
        """Get emoji for status code"""
        if status_code < 300:
            return "✅"
        elif status_code < 400:
            return "↩️"
        elif status_code < 500:
            return "⚠️"
        else:
            return "❌"


def log_api_call(
    service: str,
    endpoint: str,
    request_data: dict = None,
    response_data: dict = None,
    duration_ms: float = None,
    error: str = None
):
    """
    Log external API calls (SurgR1, GLM, SAM3, TTS, etc.)
    
    Args:
        service: Service name (e.g., "SurgR1", "GLM", "TTS")
        endpoint: API endpoint
        request_data: Request payload (will be truncated)
        response_data: Response data (will be truncated)
        duration_ms: Call duration in milliseconds
        error: Error message if any
    """
    log_parts = [f"[{service}]"]
    
    if endpoint:
        log_parts.append(f"{endpoint}")
    
    if duration_ms is not None:
        log_parts.append(f"| {duration_ms:.2f}ms")
    
    if error:
        log_parts.append(f"| ERROR: {error[:200]}")
        api_logger.error(" ".join(log_parts))
    else:
        if response_data:
            # Truncate response for logging
            resp_str = str(response_data)
            if len(resp_str) > 500:
                resp_str = resp_str[:500] + "..."
            log_parts.append(f"| {resp_str}")
        
        api_logger.info(" ".join(log_parts))


def log_surgr1_call(
    image_path: str,
    response: dict,
    duration_ms: float,
    error: str = None
):
    """Log SurgR1 API call"""
    request_info = f"image={Path(image_path).name}" if image_path else ""
    
    if error:
        api_logger.error(f"[SurgR1] {request_info} | {duration_ms:.2f}ms | ERROR: {error}")
    else:
        phase = response.get('phase', '')[:50] if response else ''
        action = response.get('action', '')[:50] if response else ''
        api_logger.info(f"[SurgR1] {request_info} | {duration_ms:.2f}ms | phase={phase} | action={action}")


def log_glm_call(
    prompt_preview: str,
    response: dict,
    duration_ms: float,
    error: str = None
):
    """Log GLM API call"""
    prompt_short = prompt_preview[:100] + "..." if len(prompt_preview) > 100 else prompt_preview
    
    if error:
        api_logger.error(f"[GLM] | {duration_ms:.2f}ms | ERROR: {error}")
    else:
        resp_preview = ""
        if response and response.get('summary'):
            resp_preview = response['summary'][:100] + "..."
        elif response and response.get('text'):
            resp_preview = response['text'][:100] + "..."
        
        api_logger.info(f"[GLM] | {duration_ms:.2f}ms | response={resp_preview}")


def log_tts_call(
    text_preview: str,
    duration_ms: float,
    audio_length_sec: float = None,
    error: str = None
):
    """Log TTS API call"""
    text_short = text_preview[:50] + "..." if len(text_preview) > 50 else text_preview
    
    if error:
        api_logger.error(f"[TTS] text={text_short} | {duration_ms:.2f}ms | ERROR: {error}")
    else:
        audio_info = f"audio={audio_length_sec:.1f}s" if audio_length_sec else ""
        api_logger.info(f"[TTS] text={text_short} | {duration_ms:.2f}ms | {audio_info}")



