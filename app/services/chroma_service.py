from __future__ import annotations

from typing import Any, Optional

import chromadb
from chromadb.api import ClientAPI
from chromadb.api.models.Collection import Collection
from pathlib import Path

from app.core.config import settings
import re
from app.services.embedding import (
    SentenceTransformerEmbeddingFunction,
    OpenAIEmbeddingFunction,
    OllamaEmbeddingFunction,
    LogBERTEmbeddingFunction,
    LogBERTClientEmbeddingFunction,
)


class ChromaClientProvider:
    """Factory for Chroma client and collections."""

    def __init__(self, embedding_model_name: Optional[str] = None) -> None:
        # Choose embedding provider
        provider = settings.EMBEDDING_PROVIDER.lower()
        embedding_fn: Any = None
        if provider == "openai":
            embedding_fn = OpenAIEmbeddingFunction(
                model=settings.OPENAI_EMBEDDING_MODEL,
                api_key=settings.OPENAI_API_KEY,
            )
        elif provider == "sentence-transformers":
            embedding_fn = SentenceTransformerEmbeddingFunction(
                embedding_model_name or settings.EMBEDDING_MODEL_NAME
            )
        elif provider == "ollama":
            embedding_fn = OllamaEmbeddingFunction(
                base_url=settings.OLLAMA_BASE_URL,
                model=settings.OLLAMA_EMBEDDING_MODEL,
            )
        elif provider == "logbert":
            # Check if external LogBERT service is configured
            if settings.LOGBERT_BASE_URL:
                # Use external LogBERT microservice (OpenAI-compatible API)
                logbert_base = settings.LOGBERT_BASE_URL.rstrip("/")
                embedding_fn = LogBERTClientEmbeddingFunction(
                    base_url=logbert_base,
                    model_name=settings.LOGBERT_MODEL_NAME,
                )
            else:
                # Use local LogBERT (loads model in-process)
                embedding_fn = LogBERTEmbeddingFunction(
                    model_name=settings.LOGBERT_MODEL_NAME,
                    device=settings.LOGBERT_DEVICE,
                )
        elif provider == "tei":
            # Text Embeddings Inference - HuggingFace's high-performance embedding server
            # Reuses OpenAIEmbeddingFunction since TEI exposes OpenAI-compatible /v1/embeddings
            tei_base = settings.TEI_BASE_URL.rstrip("/")
            embedding_fn = OpenAIEmbeddingFunction(
                model=settings.TEI_MODEL_NAME,
                api_key=settings.TEI_API_KEY,
                base_url=f"{tei_base}/v1",
            )
        else:
            raise ValueError(
                f"Unknown EMBEDDING_PROVIDER '{settings.EMBEDDING_PROVIDER}'. "
                "Supported: openai, sentence-transformers, ollama, logbert, tei"
            )
        self.embedding_fn = embedding_fn
        self._client = self._create_client()

    def _create_client(self) -> ClientAPI:
        if settings.CHROMA_MODE.lower() == "http":
            return chromadb.HttpClient(
                host=settings.CHROMA_SERVER_HOST,
                port=settings.CHROMA_SERVER_PORT,
            )
        # default to local persistent client
        # Resolve to an absolute path to avoid losing data when cwd changes across restarts
        try:
            persist_path = str(Path(settings.CHROMA_PERSIST_DIRECTORY).resolve())
            # Ensure directory exists
            Path(persist_path).mkdir(parents=True, exist_ok=True)
        except Exception:
            # Fall back to the raw setting if resolution fails for any reason
            persist_path = settings.CHROMA_PERSIST_DIRECTORY
        return chromadb.PersistentClient(path=persist_path)

    @property
    def client(self) -> ClientAPI:
        return self._client

    def get_or_create_collection(self, name: str) -> Collection:
        # Ensure collections are namespaced by embedding function to avoid
        # dimension mismatches when switching models/providers.
        embed_id = getattr(self.embedding_fn, "name", lambda: "unknown")()
        suffix = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(embed_id)).strip("_")
        safe_name = f"{name}__{suffix}" if suffix else name
        return self.client.get_or_create_collection(
            name=safe_name,
            embedding_function=self.embedding_fn,  # type: ignore[arg-type]
            metadata={
                "source": "enterprise-log-analyzer",
                "type": "template",
                "embedding_provider": settings.EMBEDDING_PROVIDER,
                "embedding_id": embed_id,
            },
        )


def collection_name_for_os(os_name: str) -> str:
    os_key = os_name.strip().lower()
    if os_key in {"mac", "macos", "osx"}:
        suffix = "macos"
    elif os_key in {"linux"}:
        suffix = "linux"
    elif os_key in {"windows", "win"}:
        suffix = "windows"
    else:
        suffix = os_key
    return f"{settings.CHROMA_COLLECTION_PREFIX}{suffix}"


