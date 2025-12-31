#!/usr/bin/env python3
"""
GLM-4.6V-Flash API 客户端
提供简单的接口调用GLM服务
"""
import asyncio
import httpx
import base64
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import sys

sys.path.insert(0, str(Path(__file__).parent))
from config import load_config, GLMConfig


class GLMClient:
    """GLM-4.6V-Flash API 客户端"""
    
    def __init__(
        self,
        api_url: str = None,
        model_name: str = None,
        config: GLMConfig = None,
        timeout: float = 180.0
    ):
        if config is None:
            config = load_config()
        
        self.config = config
        self.api_url = (api_url or config.api_url).rstrip('/')
        self.model_name = model_name or config.model.served_model_name
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()
    
    async def is_available(self) -> bool:
        """检查服务是否可用"""
        try:
            resp = await self.client.get(f"{self.api_url}/models")
            return resp.status_code == 200
        except:
            return False
    
    async def chat(
        self,
        message: str,
        temperature: float = None,
        max_tokens: int = None,
        top_p: float = None,
        system_prompt: str = None
    ) -> str:
        """
        文本聊天
        
        Args:
            message: 用户消息
            temperature: 温度参数
            max_tokens: 最大token数
            top_p: top_p参数
            system_prompt: 系统提示词
        
        Returns:
            模型回复文本
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature or self.config.inference.temperature,
            "max_tokens": max_tokens or self.config.inference.max_tokens,
            "top_p": top_p or self.config.inference.top_p,
        }
        
        resp = await self.client.post(
            f"{self.api_url}/chat/completions",
            json=payload
        )
        resp.raise_for_status()
        
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    
    async def analyze_image(
        self,
        image: Union[str, bytes, Path],
        question: str = "描述这张图片的内容。",
        temperature: float = None,
        max_tokens: int = None,
        system_prompt: str = None
    ) -> str:
        """
        分析图片
        
        Args:
            image: 图片路径、URL或bytes
            question: 关于图片的问题
            temperature: 温度参数
            max_tokens: 最大token数
            system_prompt: 系统提示词
        
        Returns:
            模型回复文本
        """
        # 处理图片
        if isinstance(image, (str, Path)):
            image_path = Path(image)
            if image_path.exists():
                # 本地文件
                with open(image_path, "rb") as f:
                    image_data = base64.b64encode(f.read()).decode()
                suffix = image_path.suffix.lower()
                if suffix in ['.jpg', '.jpeg']:
                    mime_type = 'image/jpeg'
                elif suffix == '.png':
                    mime_type = 'image/png'
                else:
                    mime_type = 'image/jpeg'
                image_url = f"data:{mime_type};base64,{image_data}"
            else:
                # URL
                image_url = str(image)
        elif isinstance(image, bytes):
            image_data = base64.b64encode(image).decode()
            image_url = f"data:image/jpeg;base64,{image_data}"
        else:
            raise ValueError(f"Unsupported image type: {type(image)}")
        
        # 构建消息
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": question}
            ]
        })
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature or self.config.inference.temperature,
            "max_tokens": max_tokens or self.config.inference.max_tokens,
        }
        
        resp = await self.client.post(
            f"{self.api_url}/chat/completions",
            json=payload
        )
        resp.raise_for_status()
        
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    
    async def analyze_images(
        self,
        images: List[Union[str, bytes, Path]],
        question: str,
        temperature: float = None,
        max_tokens: int = None,
        system_prompt: str = None
    ) -> str:
        """
        分析多张图片
        
        Args:
            images: 图片列表
            question: 关于图片的问题
            temperature: 温度参数
            max_tokens: 最大token数
            system_prompt: 系统提示词
        
        Returns:
            模型回复文本
        """
        content = []
        
        for image in images:
            if isinstance(image, (str, Path)):
                image_path = Path(image)
                if image_path.exists():
                    with open(image_path, "rb") as f:
                        image_data = base64.b64encode(f.read()).decode()
                    suffix = image_path.suffix.lower()
                    mime_type = 'image/jpeg' if suffix in ['.jpg', '.jpeg'] else 'image/png'
                    image_url = f"data:{mime_type};base64,{image_data}"
                else:
                    image_url = str(image)
            elif isinstance(image, bytes):
                image_data = base64.b64encode(image).decode()
                image_url = f"data:image/jpeg;base64,{image_data}"
            else:
                continue
            
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        
        content.append({"type": "text", "text": question})
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature or self.config.inference.temperature,
            "max_tokens": max_tokens or self.config.inference.max_tokens,
        }
        
        resp = await self.client.post(
            f"{self.api_url}/chat/completions",
            json=payload
        )
        resp.raise_for_status()
        
        data = resp.json()
        return data["choices"][0]["message"]["content"]


# 便捷函数
async def chat(message: str, **kwargs) -> str:
    """快速聊天"""
    async with GLMClient() as client:
        return await client.chat(message, **kwargs)


async def analyze_image(image: Union[str, bytes, Path], question: str = "描述这张图片", **kwargs) -> str:
    """快速分析图片"""
    async with GLMClient() as client:
        return await client.analyze_image(image, question, **kwargs)


if __name__ == "__main__":
    # 测试
    async def test():
        async with GLMClient() as client:
            if await client.is_available():
                print("服务可用")
                result = await client.chat("你好")
                print(f"回复: {result}")
            else:
                print("服务不可用")
    
    asyncio.run(test())

