from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Sequence, cast

import redis.asyncio as aioredis

from fastapi import FastAPI
from app.core.config import get_settings
from app.services.chroma_service import ChromaClientProvider, collection_name_for_os
from app.services.llm_service import classify_cluster, generate_hypothesis
import threading


settings = get_settings()
redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
LOG = logging.getLogger(__name__)
_provider: ChromaClientProvider | None = None


def _get_provider() -> ChromaClientProvider:
    global _provider
    if _provider is None:
        _provider = ChromaClientProvider()
    return _provider


def _suffix_for_os(os_name: str) -> str:
    key = (os_name or "").strip().lower()
    if key in {"mac", "macos", "osx"}:
        return "macos"
    if key in {"linux"}:
        return "linux"
    if key in {"windows", "win"}:
        return "windows"
    return key or "unknown"


def _logs_collection_name(os_name: str) -> str:
    return f"{settings.CHROMA_LOG_COLLECTION_PREFIX}{_suffix_for_os(os_name)}"


def _proto_collection_name(os_name: str) -> str:
    return f"{settings.CHROMA_PROTO_COLLECTION_PREFIX}{_suffix_for_os(os_name)}"


def _vector_has_values(vec) -> bool:
    """Check if vector has values without triggering numpy truthiness ambiguity."""
    if vec is None:
        return False
    try:
        import numpy as np  # type: ignore
        if isinstance(vec, np.ndarray):  # noqa: SIM401
            return bool(getattr(vec, "size", 0) > 0)
    except Exception:
        pass
    try:
        # Convert to list to avoid numpy array truthiness issues
        if hasattr(vec, 'tolist'):
            vec = vec.tolist()
        return bool(len(vec) > 0)  # type: ignore[arg-type]
    except Exception:
        return False


def _first_result_list(q: Dict[str, Any], key: str) -> list:
    """Safely extract the first list from Chroma query results.

    Avoids numpy truthiness issues (e.g. `np.ndarray or [...]`) by never using boolean
    evaluation on potential numpy arrays.
    """
    val = q.get(key)
    if val is None:
        return []
    try:
        import numpy as np  # type: ignore
        if isinstance(val, np.ndarray):  # noqa: SIM401
            val = val.tolist()
    except Exception:
        pass
    if isinstance(val, list):
        if not val:
            return []
        first = val[0]
        return first if isinstance(first, list) else [first]
    return []


def _get_prototype(os_name: str, cluster_id: str) -> tuple[list[float] | None, str, Dict[str, Any]]:
    collection = _get_provider().get_or_create_collection(_proto_collection_name(os_name))
    data = collection.get(ids=[cluster_id], include=["embeddings", "documents", "metadatas"]) or {}
    embs = data.get("embeddings")
    docs = data.get("documents")
    metas = data.get("metadatas")
    try:
        import numpy as np  # type: ignore
        if isinstance(embs, np.ndarray):  # noqa: SIM401
            embs = embs.tolist()
        if isinstance(docs, np.ndarray):  # noqa: SIM401
            docs = docs.tolist()
        if isinstance(metas, np.ndarray):  # noqa: SIM401
            metas = metas.tolist()
    except Exception:
        pass
    embs_list = embs if isinstance(embs, list) else []
    docs_list = docs if isinstance(docs, list) else []
    metas_list = metas if isinstance(metas, list) else []

    centroid = embs_list[0] if len(embs_list) > 0 else None
    medoid_doc = docs_list[0] if len(docs_list) > 0 else ""
    meta = metas_list[0] if len(metas_list) > 0 else {}
    return centroid, medoid_doc, meta


async def run_cluster_enricher() -> None:
    group = "clusters_enrichers"
    consumer = "cluster_enricher_1"
    try:
        await redis.xgroup_create(settings.CLUSTERS_CANDIDATES_STREAM, group, id="$", mkstream=True)
    except Exception:
        pass

    while True:
        try:
            response = await redis.xreadgroup(group, consumer, {settings.CLUSTERS_CANDIDATES_STREAM: ">"}, count=5, block=1000)
        except Exception as exc:
            LOG.info("cluster enricher read failed err=%s", exc)
            await asyncio.sleep(1)
            continue
        if not response:
            continue
        for _, messages in response:
            for msg_id, data in messages:
                try:
                    os_name = data.get("os") or "unknown"
                    cluster_id = data.get("cluster_id") or ""
                    centroid, medoid_doc, proto_meta = _get_prototype(os_name, cluster_id)

                    # neighbors from templates via centroid
                    neighbors: List[Dict[str, Any]] = []
                    # Avoid numpy truthiness ambiguity on arrays by coercing to list and checking size explicitly
                    centroid_vec: list[float] | None = None
                    if centroid is not None:
                        try:
                            import numpy as np  # type: ignore
                            if isinstance(centroid, np.ndarray):
                                centroid_vec = centroid.tolist()
                            else:
                                centroid_vec = list(centroid)
                        except Exception:
                            centroid_vec = list(centroid) if centroid else None
                    if centroid_vec and len(centroid_vec) > 0:
                        try:
                            tcoll = _get_provider().get_or_create_collection(collection_name_for_os(os_name))
                            q = tcoll.query(query_embeddings=[centroid_vec], n_results=8, include=["documents", "metadatas", "distances"]) or {}
                            ids = _first_result_list(q, "ids")
                            docs = _first_result_list(q, "documents")
                            dists = _first_result_list(q, "distances")
                            metas = _first_result_list(q, "metadatas")
                            for i in range(len(ids)):
                                neighbors.append({
                                    "id": ids[i],
                                    "document": docs[i] if i < len(docs) else "",
                                    "distance": dists[i] if i < len(dists) else None,
                                    "metadata": metas[i] if i < len(metas) else {},
                                })
                        except Exception as e:
                            # Handle ChromaDB HNSW index errors gracefully
                            if "Nothing found on disk" in str(e) or "hnsw segment reader" in str(e):
                                LOG.info("cluster enricher: template collection index corrupted, skipping neighbor lookup id=%s os=%s err=%s", cluster_id, os_name, e)
                            else:
                                raise

                    # HYDE queries using medoid (kept for future use), but retrieval will not use query()
                    seed_logs = [{"templated": medoid_doc}] if medoid_doc else []
                    _queries = generate_hypothesis(os_name, medoid_doc, seed_logs, num_queries=3)  # noqa: F841

                    # retrieve logs within same cluster via where filter (use get instead of query to avoid vector/text requirement)
                    retrieved: List[Dict[str, Any]] = []
                    env_ids_set: set[str] = set()
                    lcoll = _get_provider().get_or_create_collection(_logs_collection_name(os_name))
                    try:
                        res = cast(Dict[str, Any], lcoll.get(where={"cluster_id": cluster_id}, include=["documents", "metadatas"], limit=30)) or {}
                    except Exception:
                        res = {}
                    ids = list(res.get("ids") or [])
                    docs = list(res.get("documents") or [])
                    metas = list(res.get("metadatas") or [])
                    for i in range(len(ids)):
                        meta_i = metas[i] if i < len(metas) else {}
                        env_val = (meta_i or {}).get("env_id")
                        if env_val:
                            env_ids_set.add(str(env_val))
                        retrieved.append({
                            "id": ids[i],
                            "templated": docs[i] if i < len(docs) else "",
                            "raw": (meta_i or {}).get("raw", ""),
                            "source": (meta_i or {}).get("source", ""),
                            "os": (meta_i or {}).get("os", os_name),
                            "env_id": env_val,
                        })

                    # Fallback: use sample logs from candidate if Chroma lookup was empty.
                    if not retrieved:
                        try:
                            sample_logs = json.loads(data.get("sample_logs") or "[]")
                        except Exception:
                            sample_logs = []
                        if isinstance(sample_logs, list) and sample_logs:
                            for s in sample_logs[:10]:
                                if not isinstance(s, dict):
                                    continue
                                env_val = s.get("env_id")
                                if env_val:
                                    env_ids_set.add(str(env_val))
                                retrieved.append({
                                    "id": s.get("id") or "",
                                    "templated": s.get("templated") or "",
                                    "raw": s.get("raw") or "",
                                    "source": s.get("source") or "",
                                    "os": s.get("os") or os_name,
                                    "env_id": env_val,
                                })
                    if not env_ids_set:
                        try:
                            env_ids_list = json.loads(data.get("env_ids") or "[]")
                            for env_val in env_ids_list or []:
                                if env_val:
                                    env_ids_set.add(str(env_val))
                        except Exception:
                            pass

                    result = classify_cluster(os_name, cluster_id, medoid_doc, neighbors, retrieved)
                    
                    # Record LLM metrics if enabled
                    if settings.ENABLE_CLUSTER_METRICS:
                        try:
                            from app.services.cluster_metrics import ClusterMetricsTracker
                            metadata = result.get("_llm_metadata", {})
                            tracker = ClusterMetricsTracker(redis)
                            await tracker.record_llm_call(
                                os_name=os_name,
                                cluster_id=cluster_id,
                                operation="classify_cluster",
                                confidence=result.get("confidence"),
                                tokens_used=metadata.get("tokens", 0),
                                latency_ms=metadata.get("latency_ms", 0),
                                success=metadata.get("success", True),
                            )
                        except Exception:
                            pass  # Don't fail enrichment if metrics fail
                    
                    env_ids_list = list(env_ids_set)
                    payload = {
                        "type": "cluster",
                        "os": os_name,
                        "cluster_id": cluster_id,
                        "failure_type": result.get("failure_type", ""),
                        "confidence": str(result.get("confidence") or ""),
                        "result": json.dumps(result),
                        "env_id": env_ids_list[0] if len(env_ids_list) == 1 else "",
                        "env_ids": json.dumps(env_ids_list),
                        "evidence_logs": json.dumps(retrieved),
                    }
                    entry_id = await redis.xadd(settings.ALERTS_STREAM, payload)  # type: ignore[arg-type]
                    try:
                        import logging as _logging
                        _logging.getLogger("app.kaboom").info(
                            "alert_published id=%s os=%s type=%s cluster_id=%s",
                            entry_id, os_name, "cluster", cluster_id
                        )
                    except Exception:
                        pass

                    # Update prototype metadata with learned label/solution
                    try:
                        pcoll = _get_provider().get_or_create_collection(_proto_collection_name(os_name))
                        meta = dict(proto_meta or {})
                        meta["label"] = result.get("failure_type", meta.get("label", "unknown"))
                        meta["rationale"] = "llm_cluster"
                        if result.get("recommendation"):
                            meta["solution"] = result.get("recommendation")
                        pcoll.update(ids=[cluster_id], metadatas=[meta])
                    except Exception:
                        pass
                except Exception as exc:
                    LOG.exception("cluster enricher processing failed id=%s err=%s", msg_id, exc)
                    try:
                        import logging as _logging
                        _logging.getLogger("app.kaboom").info(
                            "cluster_enricher_failed id=%s os=%s cluster_id=%s err=%s",
                            msg_id, data.get("os"), data.get("cluster_id"), exc
                        )
                    except Exception:
                        pass
                finally:
                    try:
                        await redis.xack(settings.CLUSTERS_CANDIDATES_STREAM, group, msg_id)
                    except Exception:
                        pass


def attach_cluster_enricher(app: FastAPI):
    async def _run_forever():
        backoff = 1.0
        while True:
            try:
                await run_cluster_enricher()
            except Exception as exc:
                LOG.info("cluster enricher crashed err=%s; restarting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)

    @app.on_event("startup")
    async def startup_event():
        if not settings.ENABLE_CLUSTER_ENRICHER:
            return
        LOG.info("starting cluster enricher in dedicated thread")
        loop = asyncio.new_event_loop()

        def _runner():
            asyncio.set_event_loop(loop)
            loop.create_task(_run_forever())
            loop.run_forever()

        thread = threading.Thread(target=_runner, name="cluster-enricher-thread", daemon=True)
        thread.start()
        app.state.cluster_enricher_loop = loop
        app.state.cluster_enricher_thread = thread

    @app.on_event("shutdown")
    async def shutdown_event():
        loop = getattr(app.state, "cluster_enricher_loop", None)
        thread = getattr(app.state, "cluster_enricher_thread", None)
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)






