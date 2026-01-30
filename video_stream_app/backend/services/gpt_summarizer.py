"""
GPT Summarization Service
Generates summaries for 5-second video windows using OpenAI API
"""
import asyncio
import base64
from io import BytesIO
from typing import List, Optional, Dict, Any
from PIL import Image
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GPTSummarizer:
    """
    GPT-based video summarization service
    
    Uses OpenAI's vision models to analyze video frames
    and generate narrative summaries.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "gpt-4o",
        use_chinese: bool = False
    ):
        """
        Initialize GPT Summarizer
        
        Args:
            api_key: OpenAI API key
            base_url: Optional custom API base URL
            model: Model to use (gpt-4o, gpt-4o-mini, etc.)
            use_chinese: Whether to generate Chinese summaries
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.use_chinese = use_chinese
        self._client = None
        
    def _get_client(self):
        """Get or create OpenAI client"""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                
                kwargs = {}
                if self.api_key:
                    kwargs['api_key'] = self.api_key
                if self.base_url:
                    kwargs['base_url'] = self.base_url
                
                self._client = AsyncOpenAI(**kwargs)
            except ImportError:
                raise RuntimeError("OpenAI package not installed. Run: pip install openai")
        
        return self._client
    
    def _image_to_base64(self, image: Image.Image, format: str = "JPEG", quality: int = 85) -> str:
        """Convert PIL image to base64 data URL"""
        buffer = BytesIO()
        # Convert RGBA to RGB if necessary
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        image.save(buffer, format=format, quality=quality)
        base64_data = base64.b64encode(buffer.getvalue()).decode()
        return f"data:image/jpeg;base64,{base64_data}"
    
    async def summarize_window(
        self,
        images: List[Image.Image],
        context: str = "",
        system_prompt: str = None,
        max_tokens: int = 500,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """
        Generate summary for a video window
        
        Args:
            images: List of frame images
            context: Additional context about the frames
            system_prompt: Custom system prompt
            max_tokens: Maximum response tokens
            temperature: Sampling temperature
            
        Returns:
            Dict with summary text and metadata
        """
        client = self._get_client()
        
        # Default system prompt
        if system_prompt is None:
            system_prompt = """You are an expert surgical video analyst. Analyze the provided video frames and generate a concise narrative summary.

Your summary should:
1. Describe the current surgical phase/action
2. Identify visible tools and their usage
3. Note any significant observations
4. Be 2-4 sentences in a single paragraph

Output only the summary, no additional formatting."""

        # Build message content with images
        content = []
        
        # Add images (limit to avoid token limits)
        max_images = min(len(images), 5)  # Max 5 images per request
        step = max(1, len(images) // max_images)
        
        for i in range(0, len(images), step):
            if len(content) >= max_images * 2:  # Each image adds 2 items
                break
            
            image_url = self._image_to_base64(images[i])
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url, "detail": "low"}
            })
        
        # Add context text
        prompt_text = "Analyze these video frames and provide a concise summary."
        if context:
            prompt_text += f"\n\nAdditional context:\n{context}"
        
        if self.use_chinese:
            prompt_text += "\n\n请用中文回答。Please respond in Chinese."
        
        content.append({"type": "text", "text": prompt_text})
        
        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            
            summary_text = response.choices[0].message.content.strip()
            
            return {
                "success": True,
                "summary": summary_text,
                "model": self.model,
                "tokens_used": response.usage.total_tokens if response.usage else 0
            }
            
        except Exception as e:
            logger.error(f"GPT summarization error: {e}")
            return {
                "success": False,
                "summary": f"[Error generating summary: {str(e)}]",
                "model": self.model,
                "error": str(e)
            }
    
    async def analyze_frame(
        self,
        image: Image.Image,
        questions: List[str] = None
    ) -> Dict[str, str]:
        """
        Analyze a single frame with multiple questions
        
        Args:
            image: Frame image
            questions: List of analysis questions
            
        Returns:
            Dict mapping questions to answers
        """
        if questions is None:
            questions = [
                "What surgical phase is shown?",
                "What surgical action is being performed?",
                "What tools are visible and where are they located?"
            ]
        
        client = self._get_client()
        image_url = self._image_to_base64(image)
        
        results = {}
        
        for question in questions:
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                                {"type": "text", "text": question}
                            ]
                        }
                    ],
                    max_tokens=200,
                    temperature=0.3
                )
                
                results[question] = response.choices[0].message.content.strip()
                
            except Exception as e:
                results[question] = f"[Error: {str(e)}]"
        
        return results
    
    async def batch_summarize(
        self,
        windows: List[Dict[str, Any]],
        max_concurrent: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Summarize multiple windows concurrently
        
        Args:
            windows: List of dicts with 'images' and optional 'context'
            max_concurrent: Max concurrent API calls
            
        Returns:
            List of summary results
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def summarize_with_semaphore(window: Dict) -> Dict[str, Any]:
            async with semaphore:
                return await self.summarize_window(
                    images=window['images'],
                    context=window.get('context', '')
                )
        
        tasks = [summarize_with_semaphore(w) for w in windows]
        results = await asyncio.gather(*tasks)
        
        return results


# Simple factory function
def create_summarizer(
    api_key: str = None,
    base_url: str = None,
    model: str = "gpt-4o",
    use_chinese: bool = False
) -> GPTSummarizer:
    """Create a GPT summarizer instance"""
    return GPTSummarizer(
        api_key=api_key,
        base_url=base_url,
        model=model,
        use_chinese=use_chinese
    )




