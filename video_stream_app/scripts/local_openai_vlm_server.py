#!/usr/bin/env python3
"""Small OpenAI-compatible local VLM server backed by Transformers.

This is intentionally minimal: it implements only the endpoints used by the
app's GLMClient: /v1/models and /v1/chat/completions.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import re
import time
import uuid
from typing import Any, Dict, List, Tuple

import httpx
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image
from transformers import (
    AutoConfig,
    AutoModel,
    AutoModelForCausalLM,
    AutoModelForImageTextToText,
    AutoProcessor,
    AutoTokenizer,
    GenerationConfig,
)


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    temperature: float = 0.0
    max_tokens: int = 512


def _decode_data_image(url: str) -> Image.Image:
    if "," not in url:
        raise ValueError("invalid data image url")
    _, encoded = url.split(",", 1)
    data = base64.b64decode(encoded)
    return Image.open(io.BytesIO(data)).convert("RGB")


async def _load_image(url: str) -> Image.Image:
    if url.startswith("data:image"):
        return _decode_data_image(url)
    if url.startswith("http://") or url.startswith("https://"):
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content)).convert("RGB")
    raise ValueError("unsupported image url")


async def _normalize_messages(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    normalized: List[Dict[str, Any]] = []
    image_count = 0

    for msg in messages:
        role = str(msg.get("role") or "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            normalized.append({"role": role, "content": content})
            continue

        parts: List[Dict[str, Any]] = []
        for part in content or []:
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text") or "")
                if text:
                    parts.append({"type": "text", "text": text})
            elif part_type == "image_url":
                image_url = part.get("image_url") or {}
                url = image_url.get("url") if isinstance(image_url, dict) else str(image_url)
                if url:
                    image = await _load_image(url)
                    image_count += 1
                    parts.append({"type": "image", "image": image})

        if parts:
            normalized.append({"role": role, "content": parts})

    return normalized, image_count


def _clean_generation(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "")
    cleaned = re.sub(r"<\|[^|]+?\|>", "", cleaned)
    return cleaned.strip()


def _to_minicpm_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            converted.append({"role": role, "content": [content]})
            continue

        parts: List[Any] = []
        for part in content or []:
            if part.get("type") == "text":
                text = str(part.get("text") or "")
                if text:
                    parts.append(text)
            elif part.get("type") == "image":
                image = part.get("image")
                if image is not None:
                    parts.append(image)
        if parts:
            converted.append({"role": role, "content": parts})
    return converted


def _build_internvl_transform(input_size: int = 448) -> Any:
    import torchvision.transforms as T
    from torchvision.transforms.functional import InterpolationMode

    mean = (0.485, 0.456, 0.406)
    std = (0.229, 0.224, 0.225)
    return T.Compose([
        T.Lambda(lambda img: img.convert("RGB") if img.mode != "RGB" else img),
        T.Resize((input_size, input_size), interpolation=InterpolationMode.BICUBIC),
        T.ToTensor(),
        T.Normalize(mean=mean, std=std),
    ])


def _find_closest_aspect_ratio(
    aspect_ratio: float,
    target_ratios: List[Tuple[int, int]],
    width: int,
    height: int,
    image_size: int,
) -> Tuple[int, int]:
    best_ratio_diff = float("inf")
    best_ratio = (1, 1)
    area = width * height
    for ratio in target_ratios:
        target_aspect_ratio = ratio[0] / ratio[1]
        ratio_diff = abs(aspect_ratio - target_aspect_ratio)
        if ratio_diff < best_ratio_diff:
            best_ratio_diff = ratio_diff
            best_ratio = ratio
        elif ratio_diff == best_ratio_diff:
            if area > 0.5 * image_size * image_size * ratio[0] * ratio[1]:
                best_ratio = ratio
    return best_ratio


def _internvl_dynamic_preprocess(
    image: Image.Image,
    min_num: int = 1,
    max_num: int = 4,
    image_size: int = 448,
    use_thumbnail: bool = True,
) -> List[Image.Image]:
    orig_width, orig_height = image.size
    aspect_ratio = orig_width / orig_height
    target_ratios = sorted(
        {
            (i, j)
            for n in range(min_num, max_num + 1)
            for i in range(1, n + 1)
            for j in range(1, n + 1)
            if min_num <= i * j <= max_num
        },
        key=lambda x: x[0] * x[1],
    )
    ratio = _find_closest_aspect_ratio(aspect_ratio, target_ratios, orig_width, orig_height, image_size)
    target_width = image_size * ratio[0]
    target_height = image_size * ratio[1]
    blocks = ratio[0] * ratio[1]
    resized = image.resize((target_width, target_height))
    processed: List[Image.Image] = []
    for idx in range(blocks):
        box = (
            (idx % (target_width // image_size)) * image_size,
            (idx // (target_width // image_size)) * image_size,
            ((idx % (target_width // image_size)) + 1) * image_size,
            ((idx // (target_width // image_size)) + 1) * image_size,
        )
        processed.append(resized.crop(box))
    if use_thumbnail and len(processed) != 1:
        processed.append(image.resize((image_size, image_size)))
    return processed


def _internvl_images_to_pixel_values(images: List[Image.Image], max_num: int = 4) -> Tuple[torch.Tensor, List[int]]:
    transform = _build_internvl_transform(448)
    pixel_values: List[torch.Tensor] = []
    num_patches_list: List[int] = []
    for image in images:
        tiles = _internvl_dynamic_preprocess(image, max_num=max_num)
        tensors = [transform(tile) for tile in tiles]
        num_patches_list.append(len(tensors))
        pixel_values.extend(tensors)
    return torch.stack(pixel_values), num_patches_list


def _to_internvl_question(messages: List[Dict[str, Any]]) -> Tuple[str, List[Image.Image]]:
    text_parts: List[str] = []
    images: List[Image.Image] = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            text_parts.append(content)
            continue
        for part in content or []:
            if part.get("type") == "text":
                text = str(part.get("text") or "")
                if text:
                    text_parts.append(text)
            elif part.get("type") == "image":
                image = part.get("image")
                if image is not None:
                    images.append(image)
    text = "\n".join(text_parts).strip()
    if not images:
        return text, []
    if len(images) == 1:
        return f"<image>\n{text}", images
    image_refs = "\n".join(f"Image-{idx + 1}: <image>" for idx in range(len(images)))
    return f"{image_refs}\n{text}", images


def _to_jina_conversation(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Image.Image]]:
    conversation: List[Dict[str, Any]] = []
    images: List[Image.Image] = []
    for msg in messages:
        role = str(msg.get("role") or "user")
        content = msg.get("content", "")
        if isinstance(content, str):
            conversation.append({"role": role, "content": [{"type": "text", "text": content}]})
            continue

        parts: List[Dict[str, Any]] = []
        for part in content or []:
            if part.get("type") == "text":
                text = str(part.get("text") or "")
                if text:
                    parts.append({"type": "text", "text": text})
            elif part.get("type") == "image":
                image = part.get("image")
                if image is not None:
                    images.append(image)
                    parts.append({"type": "image", "image": image})
        if parts:
            conversation.append({"role": role, "content": parts})
    return conversation, images


def create_app(model_path: str, served_model_name: str, max_concurrent: int) -> FastAPI:
    app = FastAPI(title="Local OpenAI-compatible VLM")
    state: Dict[str, Any] = {}
    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    @app.on_event("startup")
    async def startup() -> None:
        config = AutoConfig.from_pretrained(model_path, trust_remote_code=True)
        model_type = str(getattr(config, "model_type", "") or "").lower()
        if model_type == "minicpmo":
            model = AutoModel.from_pretrained(
                model_path,
                trust_remote_code=True,
                attn_implementation="sdpa",
                torch_dtype=torch.bfloat16,
                init_vision=True,
                init_audio=False,
                init_tts=False,
            )
            model.eval().cuda()
            if hasattr(model, "prepare_processor"):
                model.prepare_processor()
            state["backend"] = "minicpm"
            state["processor"] = None
            state["model"] = model
            return

        if model_type == "internvl_chat":
            tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, use_fast=False)
            model = AutoModel.from_pretrained(
                model_path,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
                low_cpu_mem_usage=True,
                use_flash_attn=False,
            )
            model.eval().cuda()
            state["backend"] = "internvl"
            state["processor"] = None
            state["tokenizer"] = tokenizer
            state["model"] = model
            return

        if model_type == "jvlm":
            processor = AutoProcessor.from_pretrained(model_path, use_fast=False, trust_remote_code=True)
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                trust_remote_code=True,
                torch_dtype=torch.bfloat16,
                device_map="auto",
            )
            model.eval()
            state["backend"] = "jina"
            state["processor"] = processor
            state["model"] = model
            return

        processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
        model = AutoModelForImageTextToText.from_pretrained(
            model_path,
            trust_remote_code=True,
            torch_dtype="auto",
            device_map="auto",
        )
        model.eval()
        state["backend"] = "image_text_to_text"
        state["processor"] = processor
        state["model"] = model

    @app.get("/v1/models")
    async def models() -> Dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": served_model_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "local",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: ChatCompletionRequest) -> Dict[str, Any]:
        if "model" not in state or "processor" not in state:
            raise HTTPException(status_code=503, detail="model is still loading")

        processor = state["processor"]
        model = state["model"]
        try:
            normalized, image_count = await _normalize_messages(request.messages)
            if not normalized:
                raise ValueError("empty messages")

            async with semaphore:
                temperature = float(request.temperature or 0.0)
                max_new_tokens = int(request.max_tokens or 512)
                if state.get("backend") == "minicpm":
                    minicpm_msgs = _to_minicpm_messages(normalized)
                    text = await asyncio.to_thread(
                        model.chat,
                        msgs=minicpm_msgs,
                        max_new_tokens=max_new_tokens,
                        do_sample=temperature > 0.0,
                        use_image_id=False,
                        max_slice_nums=1,
                        use_tts_template=False,
                        enable_thinking=False,
                        use_cache=False,
                    )
                    text = _clean_generation(str(text))
                    prompt_tokens = 0
                    completion_tokens = 0
                elif state.get("backend") == "internvl":
                    question, images = _to_internvl_question(normalized)
                    if images:
                        pixel_values, num_patches_list = _internvl_images_to_pixel_values(images, max_num=4)
                        pixel_values = pixel_values.to(torch.bfloat16).to(model.device)
                    else:
                        pixel_values = None
                        num_patches_list = None
                    generation_config = {
                        "max_new_tokens": max_new_tokens,
                        "do_sample": temperature > 0.0,
                    }
                    if temperature > 0.0:
                        generation_config["temperature"] = temperature
                    text = await asyncio.to_thread(
                        model.chat,
                        state["tokenizer"],
                        pixel_values,
                        question,
                        generation_config,
                        num_patches_list=num_patches_list,
                    )
                    text = _clean_generation(str(text))
                    prompt_tokens = 0
                    completion_tokens = 0
                elif state.get("backend") == "jina":
                    conversation, images = _to_jina_conversation(normalized)
                    text_prompt = processor.apply_chat_template(conversation, add_generation_prompt=True)
                    if images:
                        inputs = processor(
                            text=[text_prompt],
                            images=images,
                            padding="longest",
                            return_tensors="pt",
                        )
                    else:
                        inputs = processor(text=[text_prompt], padding="longest", return_tensors="pt")
                    inputs = {
                        key: value.to(model.device) if hasattr(value, "to") else value
                        for key, value in inputs.items()
                    }
                    generation_config = GenerationConfig(
                        max_new_tokens=max_new_tokens,
                        do_sample=temperature > 0.0,
                    )
                    if temperature > 0.0:
                        generation_config.temperature = temperature
                    with torch.inference_mode():
                        output = await asyncio.to_thread(
                            model.generate,
                            **inputs,
                            generation_config=generation_config,
                            return_dict_in_generate=True,
                            use_model_defaults=True,
                        )
                    generated = output.sequences[0][inputs["input_ids"].shape[-1]:]
                    text = processor.tokenizer.decode(generated, skip_special_tokens=True)
                    text = _clean_generation(text)
                    prompt_tokens = int(inputs["input_ids"].shape[-1])
                    completion_tokens = int(generated.shape[-1])
                else:
                    inputs = processor.apply_chat_template(
                        normalized,
                        tokenize=True,
                        add_generation_prompt=True,
                        enable_thinking=False,
                        return_dict=True,
                        return_tensors="pt",
                    )
                    inputs = {
                        key: value.to(model.device) if hasattr(value, "to") else value
                        for key, value in inputs.items()
                    }
                    generation_kwargs = {
                        "max_new_tokens": max_new_tokens,
                        "do_sample": temperature > 0.0,
                    }
                    if temperature > 0.0:
                        generation_kwargs["temperature"] = temperature
                    with torch.inference_mode():
                        output_ids = await asyncio.to_thread(model.generate, **inputs, **generation_kwargs)
                    trimmed = [
                        output[len(input_ids):]
                        for input_ids, output in zip(inputs["input_ids"], output_ids)
                    ]
                    text = processor.batch_decode(trimmed, skip_special_tokens=True)[0]
                    text = _clean_generation(text)
                    prompt_tokens = int(inputs["input_ids"].shape[-1])
                    completion_tokens = max(0, int(trimmed[0].shape[-1]) if trimmed else 0)

            return {
                "id": f"chatcmpl-local-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": served_model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                    "image_count": image_count,
                },
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--served-model-name", default="GLM-4.6V-Flash")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--max-concurrent", type=int, default=1)
    args = parser.parse_args()

    app = create_app(args.model_path, args.served_model_name, args.max_concurrent)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
