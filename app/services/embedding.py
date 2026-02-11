from __future__ import annotations

from typing import Any, Dict, Iterable, List
import logging
import threading
import time
import os

import numpy as np
import ollama
from openai import OpenAI
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer, AutoModel
import torch

from app.core.config import settings


class SentenceTransformerEmbeddingFunction:
    """Adapter for SentenceTransformer to be used with Chroma as embedding_function.

    The class is callable and returns a list of embeddings for a list of texts.
    """

    def __init__(self, model_name: str) -> None:
        self.model = SentenceTransformer(model_name)

    def __call__(self, input: Iterable[str]) -> List[List[float]]:
        embeddings = self.model.encode(list(input), normalize_embeddings=True)
        if isinstance(embeddings, np.ndarray):
            return embeddings.tolist()
        return [list(vec) for vec in embeddings]

    # Chroma may call .name() to verify embedding function identity
    def name(self) -> str:  # pragma: no cover - simple getter
        return f"sentence-transformers::{self.model.get_sentence_embedding_dimension()}"

    # Some Chroma paths call embed_documents/embed_query when available
    def embed_documents(self, input: Iterable[str]) -> List[List[float]]:
        return self(list(input))

    def embed_query(self, input: str) -> List[List[float]]:
        return self([input])


class LogBERTEmbeddingFunction:
    """LogBERT embeddings for semantic log understanding.

    Uses a class-level singleton for the underlying model to avoid
    thread-safety issues with concurrent model loading (accelerate/
    transformers produce meta tensors when from_pretrained is called
    from multiple threads simultaneously).
    """

    _logged_ready_keys: set[str] = set()

    # ---- Class-level singleton for the model/tokenizer ----
    _lock = threading.Lock()
    _shared_model: AutoModel | None = None
    _shared_tokenizer: AutoTokenizer | None = None
    _shared_device: str | None = None
    _shared_model_name: str | None = None

    @classmethod
    def _ensure_model_loaded(cls, model_name: str, device: str) -> None:
        """Load the model exactly once, guarded by a lock."""
        if cls._shared_model is not None:
            return
        with cls._lock:
            # Double-checked locking
            if cls._shared_model is not None:
                return

            logger = logging.getLogger(__name__)
            logger.info("logbert: loading model %s (target device=%s) ...", model_name, device)

            tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
            model = AutoModel.from_pretrained(model_name, trust_remote_code=True)

            # Verify weights are real (not meta)
            has_meta = any(p.device.type == "meta" for p in model.parameters())
            if has_meta:
                logger.warning("logbert: meta tensors detected, retrying with low_cpu_mem_usage=False")
                model = AutoModel.from_pretrained(
                    model_name,
                    trust_remote_code=True,
                    low_cpu_mem_usage=False,
                )

            # Move to target device
            if device == "cuda" and torch.cuda.is_available():
                model = model.to("cuda")
                actual_device = "cuda"
            else:
                actual_device = "cpu"
            model.eval()

            cls._shared_model = model
            cls._shared_tokenizer = tokenizer
            cls._shared_device = actual_device
            cls._shared_model_name = model_name
            logger.info("logbert embedding provider ready model=%s device=%s", model_name, actual_device)

    def __init__(self, model_name: str = "bert-base-uncased", device: str = "cpu") -> None:
        self.model_name = model_name
        logger = logging.getLogger(__name__)

        # Resolve target device
        if device == "cuda" and torch.cuda.is_available():
            self.device = "cuda"
        else:
            self.device = "cpu"

        try:
            LogBERTEmbeddingFunction._ensure_model_loaded(model_name, self.device)
        except Exception as e:
            logger.error("logbert embedding provider failed to initialize model=%s err=%s", model_name, e)
            raise

    @property
    def model(self) -> AutoModel:
        return LogBERTEmbeddingFunction._shared_model  # type: ignore[return-value]

    @property
    def tokenizer(self) -> AutoTokenizer:
        return LogBERTEmbeddingFunction._shared_tokenizer  # type: ignore[return-value]

    def _mean_pooling(self, model_output: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        token_embeddings = model_output[0]
        input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(
            input_mask_expanded.sum(1), min=1e-9
        )

    def __call__(self, input: Iterable[str]) -> List[List[float]]:
        # Chroma may pass in a variety of types here (str, list[str], tuples, etc.).
        raw_items = list(input)
        if not raw_items:
            return []

        texts: List[str] = []
        for value in raw_items:
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, (list, tuple)):
                try:
                    texts.append(" ".join(map(str, value)))
                except Exception:
                    texts.append(str(value))
            elif value is None:
                texts.append("")
            else:
                texts.append(str(value))

        device = LogBERTEmbeddingFunction._shared_device or "cpu"

        encoded = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )

        # Move inputs to the same device as the model
        encoded = {k: v.to(device) for k, v in encoded.items()}

        with torch.no_grad():
            model_output = self.model(**encoded)

        embeddings = self._mean_pooling(model_output, encoded["attention_mask"])
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

        # Return as numpy arrays -- ChromaDB calls .tolist() on each embedding
        result = embeddings.cpu().numpy()
        return [result[i] for i in range(result.shape[0])]

    def name(self) -> str:
        return f"logbert::{self.model_name}"

    def embed_documents(self, input: Iterable[str]) -> List[List[float]]:
        return self(list(input))

    def embed_query(self, input: str) -> List[List[float]]:
        return self([input])


class LogBERTClientEmbeddingFunction:
    """Client for external LogBERT embedding service.
    
    Connects to the LogBERT microservice which exposes an OpenAI-compatible
    /v1/embeddings endpoint. This decouples the BERT model from the main
    application, allowing for independent scaling and deployment.
    """

    _logged_ready_keys: set[str] = set()

    def __init__(self, base_url: str, model_name: str = "bert-base-uncased") -> None:
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        logger = logging.getLogger(__name__)
        
        self.client = OpenAI(
            api_key="not-needed",
            base_url=f"{self.base_url}/v1",
        )
        
        if base_url not in LogBERTClientEmbeddingFunction._logged_ready_keys:
            try:
                import httpx
                response = httpx.get(f"{self.base_url}/health", timeout=10.0)
                if response.status_code == 200:
                    health = response.json()
                    logger.info(
                        "logbert client ready url=%s model=%s dim=%s",
                        base_url,
                        health.get("model", "unknown"),
                        health.get("embedding_dim", "unknown"),
                    )
                    LogBERTClientEmbeddingFunction._logged_ready_keys.add(base_url)
            except Exception as e:
                logger.warning("logbert service not reachable url=%s err=%s", base_url, e)

    def __call__(self, input: Iterable[str]) -> List[List[float]]:
        texts = list(input)
        if not texts:
            return []

        clean_texts: List[str] = []
        for value in texts:
            if isinstance(value, str):
                clean_texts.append(value)
            elif isinstance(value, (list, tuple)):
                try:
                    clean_texts.append(" ".join(map(str, value)))
                except Exception:
                    clean_texts.append(str(value))
            elif value is None:
                clean_texts.append("")
            else:
                clean_texts.append(str(value))

        response = self.client.embeddings.create(
            model=self.model_name,
            input=clean_texts,
        )
        
        return [emb.embedding for emb in response.data]

    def name(self) -> str:
        return f"logbert-client::{self.model_name}"

    def embed_documents(self, input: Iterable[str]) -> List[List[float]]:
        return self(list(input))

    def embed_query(self, input: str) -> List[List[float]]:
        return self([input])


def embed_single_text(
    embedding_function: SentenceTransformerEmbeddingFunction, text: str
) -> List[List[float]]:
    """
    Correctly embeds a single text string and returns it in the expected
    List[List[float]] format for API compatibility.
    """
    return embedding_function([text])


class OpenAIEmbeddingFunction:
    """OpenAI embeddings adapter compatible with Chroma's embedding_function interface.
    
    This class also works with any OpenAI-compatible API, including:
    - HuggingFace Text Embeddings Inference (TEI)
    - vLLM
    - LocalAI
    - Any server exposing /v1/embeddings endpoint
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.client = OpenAI(
            api_key=api_key or settings.OPENAI_API_KEY or "not-needed",
            organization=settings.OPENAI_ORG_ID if not base_url else None,
            project=settings.OPENAI_PROJECT if not base_url else None,
            base_url=base_url,
        )
        self.model = model
        self._base_url = base_url

    def __call__(self, input: Iterable[str]) -> List[List[float]]:
        inputs = list(input)
        if not inputs:
            return []
        response = self.client.embeddings.create(model=self.model, input=inputs)
        return [emb.embedding for emb in response.data]

    def name(self) -> str:  # pragma: no cover - simple getter
        if self._base_url:
            return f"openai-compatible::{self.model}"
        return f"openai::{self.model}"

    def embed_documents(self, input: Iterable[str]) -> List[List[float]]:
        return self(list(input))

    def embed_query(self, input: str) -> List[List[float]]:
        return self([input])


class OllamaEmbeddingFunction:
    """Ollama embeddings adapter using the official ollama python library."""

    _logged_ready_keys: set[tuple[str, str]] = set()
    _last_error_ts: dict[tuple[str, str], float] = {}

    def __init__(self, base_url: str, model: str) -> None:
        self.client = ollama.Client(host=base_url)
        self.model = model
        logger = logging.getLogger(__name__)
        key = (base_url, model)
        try:
            info = self.client.list()
            if key not in OllamaEmbeddingFunction._logged_ready_keys:
                info_dict: Dict[str, Any] = dict(info) if info else {}
                models_list: List[Any] = info_dict.get("models") or []
                num_models = len(models_list)
                logger.info("ollama embedding provider ready host=%s model=%s models=%d", base_url, model, num_models)
                OllamaEmbeddingFunction._logged_ready_keys.add(key)
            else:
                logger.debug("ollama embedding provider already initialized host=%s model=%s", base_url, model)
        except Exception as e:  # pragma: no cover - network
            now = time.time()
            last = OllamaEmbeddingFunction._last_error_ts.get(key, 0.0)
            if now - last >= 60.0:
                logger.warning("ollama embedding provider not reachable host=%s model=%s err=%s", base_url, model, e)
                OllamaEmbeddingFunction._last_error_ts[key] = now

    def __call__(self, input: Iterable[str]) -> List[List[float]]:
        texts = list(input)
        if not texts:
            return []

        embeddings: List[List[float]] = []
        for text in texts:
            coerced: str
            if isinstance(text, str):
                coerced = text
            elif isinstance(text, (list, tuple)):
                try:
                    coerced = " ".join(map(str, text))
                except Exception:
                    coerced = str(text)
            else:
                coerced = str(text)
            try:
                response = self.client.embeddings(model=self.model, prompt=coerced)
                embedding = response.get("embedding")
                if not isinstance(embedding, list):
                        raise RuntimeError("ollama embeddings response missing 'embedding' list")
                embeddings.append(embedding)
            except ollama.ResponseError as e:  # pragma: no cover - network
                raise RuntimeError(f"ollama embeddings API error: {e.error}") from e
            except Exception as e: # pragma: no cover - unexpected
                raise RuntimeError(f"An unexpected error occurred with ollama embeddings: {e}") from e

        return embeddings

    def name(self) -> str:  # pragma: no cover - simple getter
        return f"ollama::{self.model}"

    def embed_documents(self, input: Iterable[str]) -> List[List[float]]:
        return self(list(input))

    def embed_query(self, input: str) -> List[List[float]]:
        return self([input])
