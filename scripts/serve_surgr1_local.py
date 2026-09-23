#!/usr/bin/env python3
"""Serve the installed SurgR1 checkpoint without the legacy kill-all launcher."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT / "config.json").read_text())["services"]["surgr1"]
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ.get("SURGR1_GPU", str(config.get("gpu", 0)))
    os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
    os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
    library = ROOT.parent / "SurgR1_api/main.py"
    spec = importlib.util.spec_from_file_location("installed_surgr1", library)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG["model"].update(
        gpu_memory_utilization=float(config.get("gpu_memory_utilization", .80)),
        max_model_len=4096, max_num_seqs=4, max_pixels=401408, min_pixels=200704,
    )
    module.CONFIG["inference"].update(temperature=0.0, max_tokens=512)
    model = module.model
    if config.get("model_path"):
        model.model_path = config["model_path"]

    import uvicorn
    from fastapi import FastAPI, HTTPException

    def warmup():
        from PIL import Image
        # Compile first-inference kernels before accepting real windows.
        # Synthetic outputs are discarded, never stored as clinical evidence.
        with tempfile.TemporaryDirectory(prefix="surgr1-warmup-") as directory:
            path = str(Path(directory) / "blank.jpg")
            Image.new("RGB", (840, 630), (64, 64, 64)).save(path)
            model.analyze_batch([path, path], [module.QUESTIONS["surgical_action"]], 0.0, 8)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(model.load)
        try:
            await asyncio.to_thread(warmup)
            yield
        finally:
            # The local vLLM worker owns GPU memory beyond the HTTP process.
            await asyncio.to_thread(model.llm.llm_engine.engine_core.shutdown)

    app = FastAPI(title="Local SurgR1", lifespan=lifespan)
    gate = asyncio.Semaphore(1)

    @app.get("/health")
    async def health():
        return {"status": "healthy" if model.is_loaded() else "loading",
                "model_loaded": model.is_loaded(), "model_path": model.model_path}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "SurgR1", "owned_by": "local"}]}

    @app.post("/analyze")
    async def analyze(request: module.ImageAnalysisRequest):
        if not model.is_loaded():
            raise HTTPException(503, "model_loading")
        if not 1 <= len(request.image_paths) <= 4:
            raise HTTPException(422, "Expected one to four images")
        if request.questions and len(request.questions) > 3:
            raise HTTPException(422, "Too many questions")
        if any(not Path(path).is_file() for path in request.image_paths):
            raise HTTPException(422, "Image missing")
        async with gate:
            results = await asyncio.to_thread(
                model.analyze_batch, request.image_paths, request.questions,
                request.temperature, min(request.max_tokens or 512, 768),
            )
        return {"results": [r.model_dump() for r in results], "total_images": len(results),
                "total_questions": len(results) * len(request.questions or module.QUESTIONS)}

    uvicorn.run(app, host="127.0.0.1", port=int(config.get("port", 9003)))


if __name__ == "__main__":
    main()
