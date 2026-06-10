"""Gemini Embedding Service for semantic similarity search.
Uses gemini-embedding-2-preview model to embed window summaries
and enable semantic search across analyzed windows.
"""
import os
import json
import asyncio
import logging
import numpy as np
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Manages text embeddings for window summaries using Gemini."""

    def __init__(self, model: str = "gemini-embedding-2-preview"):
        from google import genai
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        self._client = genai.Client(api_key=api_key)
        self._model = model
        # In-memory store: {session_id: {window_id: {"embedding": [...], "text": "...", "metadata": {...}}}}
        self._embeddings: Dict[str, Dict[int, Dict]] = {}
        self._session_dir_cache: Dict[str, str] = {}
        logger.info(f"[Embedding] Initialized with model: {model}")

    async def embed_text(self, text: str) -> List[float]:
        """Get embedding vector for text using Gemini embedding model."""
        try:
            result = await asyncio.to_thread(
                self._client.models.embed_content,
                model=self._model,
                contents=text
            )
            return result.embeddings[0].values
        except Exception as e:
            logger.error(f"[Embedding] Failed to embed text: {e}")
            raise

    async def add_window_embedding(
        self, session_id: str, window_id: int,
        summary_text: str, metadata: dict = None
    ):
        """Embed and store a window summary."""
        try:
            embedding = await self.embed_text(summary_text)
            if session_id not in self._embeddings:
                self._embeddings[session_id] = {}
            self._embeddings[session_id][window_id] = {
                "embedding": embedding,
                "text": summary_text,
                "metadata": metadata or {}
            }
            # Persist to disk asynchronously
            await asyncio.to_thread(self._save_to_disk, session_id)
            logger.info(f"[Embedding] Added embedding for session={session_id} window={window_id} (dim={len(embedding)})")
        except Exception as e:
            logger.warning(f"[Embedding] Failed to embed window {window_id}: {e}")

    async def search_similar(
        self, session_id: str, query_text: str, top_k: int = 5
    ) -> List[Dict]:
        """Semantic search: embed query, find similar windows by cosine similarity."""
        if session_id not in self._embeddings or not self._embeddings[session_id]:
            # Try loading from disk
            await asyncio.to_thread(self._load_from_disk, session_id)

        if session_id not in self._embeddings or not self._embeddings[session_id]:
            return []

        query_embedding = await self.embed_text(query_text)
        return self._rank_by_similarity(session_id, query_embedding, top_k)

    async def find_similar_windows(
        self, session_id: str, window_id: int, top_k: int = 5
    ) -> List[Dict]:
        """Find windows similar to a given window."""
        if session_id not in self._embeddings:
            await asyncio.to_thread(self._load_from_disk, session_id)

        source = self._embeddings.get(session_id, {}).get(window_id)
        if not source:
            return []
        return self._rank_by_similarity(
            session_id, source["embedding"], top_k, exclude_window=window_id
        )

    def text_search(self, session_id: str, query: str) -> List[Dict]:
        """Exact text search (case-insensitive substring match)."""
        results = []
        query_lower = query.lower()
        for wid, data in self._embeddings.get(session_id, {}).items():
            text = data.get("text", "")
            if query_lower in text.lower():
                results.append({
                    "window_id": wid,
                    "text": text,
                    "match_type": "exact",
                    **data.get("metadata", {})
                })
        return sorted(results, key=lambda x: x["window_id"])

    def _rank_by_similarity(
        self, session_id: str, query_vec: List[float],
        top_k: int, exclude_window: int = None
    ) -> List[Dict]:
        """Cosine similarity ranking."""
        query_arr = np.array(query_vec)
        query_norm = np.linalg.norm(query_arr)
        if query_norm == 0:
            return []

        results = []
        for wid, data in self._embeddings.get(session_id, {}).items():
            if wid == exclude_window:
                continue
            emb_arr = np.array(data["embedding"])
            emb_norm = np.linalg.norm(emb_arr)
            if emb_norm == 0:
                continue
            sim = float(np.dot(query_arr, emb_arr) / (query_norm * emb_norm))
            results.append({
                "window_id": wid,
                "similarity": round(sim, 4),
                "text": data["text"],
                **data.get("metadata", {})
            })
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    def _get_session_dir(self, session_id: str) -> Optional[str]:
        """Find session directory on disk."""
        if session_id in self._session_dir_cache:
            return self._session_dir_cache[session_id]

        from .frame_storage_service import get_sessions_storage_base
        sessions_base = get_sessions_storage_base()
        if not sessions_base.exists():
            return None

        # Session dirs are named: {timestamp}_{session_id}_{video_name}
        for d in sessions_base.iterdir():
            if d.is_dir() and session_id in d.name:
                self._session_dir_cache[session_id] = str(d)
                return str(d)
        return None

    def _save_to_disk(self, session_id: str):
        """Persist embeddings to session folder as JSON."""
        session_dir = self._get_session_dir(session_id)
        if not session_dir:
            logger.warning(f"[Embedding] Session dir not found for {session_id}, skipping disk save")
            return

        filepath = Path(session_dir) / "embeddings.json"
        data = {}
        for wid, wdata in self._embeddings.get(session_id, {}).items():
            data[str(wid)] = {
                "text": wdata["text"],
                "embedding": wdata["embedding"],
                "metadata": wdata.get("metadata", {})
            }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug(f"[Embedding] Saved {len(data)} embeddings to {filepath}")

    def _load_from_disk(self, session_id: str):
        """Load embeddings from session folder."""
        session_dir = self._get_session_dir(session_id)
        if not session_dir:
            return

        filepath = Path(session_dir) / "embeddings.json"
        if not filepath.exists():
            return

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            if session_id not in self._embeddings:
                self._embeddings[session_id] = {}

            for wid_str, wdata in data.items():
                wid = int(wid_str)
                self._embeddings[session_id][wid] = {
                    "embedding": wdata["embedding"],
                    "text": wdata["text"],
                    "metadata": wdata.get("metadata", {})
                }
            logger.info(f"[Embedding] Loaded {len(data)} embeddings from disk for session {session_id}")
        except Exception as e:
            logger.error(f"[Embedding] Failed to load from disk: {e}")

    def get_session_stats(self, session_id: str) -> Dict:
        """Get embedding stats for a session."""
        embeddings = self._embeddings.get(session_id, {})
        return {
            "session_id": session_id,
            "num_embeddings": len(embeddings),
            "window_ids": sorted(embeddings.keys())
        }


_embedding_service: Optional[EmbeddingService] = None

def get_embedding_service() -> Optional[EmbeddingService]:
    """Get or create singleton embedding service."""
    global _embedding_service
    if _embedding_service is not None:
        return _embedding_service

    try:
        config_path = Path(__file__).parent.parent.parent / "config.json"
        with open(config_path) as f:
            config = json.load(f)

        emb_config = config.get("services", {}).get("embedding", {})
        if not emb_config.get("enabled", False):
            logger.info("[Embedding] Service disabled in config")
            return None

        model = emb_config.get("model", "gemini-embedding-2-preview")
        _embedding_service = EmbeddingService(model=model)
        return _embedding_service
    except Exception as e:
        logger.error(f"[Embedding] Failed to initialize: {e}")
        return None
