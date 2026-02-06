from __future__ import annotations

import uuid
import asyncio
import logging

from app.services.prototype_router import nearest_prototype
from app.services.chroma_service import ChromaClientProvider
from app.core.config import settings
from redis.exceptions import ConnectionError as RedisConnectionError

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


def _proto_collection_name(os_name: str) -> str:
    return f"{settings.CHROMA_PROTO_COLLECTION_PREFIX}{_suffix_for_os(os_name)}"


def _coerce_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        try:
            return " ".join(map(str, value))
        except Exception:
            return str(value)
    if value is None:
        return ""
    return str(value)


def assign_or_create_cluster(os_name: str, templated: str | object, *, threshold: float | None = None) -> str:
    """Assign templated text to nearest prototype within threshold or create a new cluster.

    Returns the cluster_id (prototype id).
    """
    thresh = threshold if threshold is not None else settings.ONLINE_CLUSTER_DISTANCE_THRESHOLD

    text = _coerce_text(templated)

    try:
        nearest = nearest_prototype(os_name, text, k=1)
    except Exception as exc:
        LOG.warning("online clustering: prototype lookup failed os=%s err=%s", os_name, exc)
        nearest = []

    distance = None
    is_new_cluster = False
    
    if nearest:
        try:
            dist = nearest[0].get("distance")
            cid = str(nearest[0].get("id") or "")
        except Exception:
            dist = None
            cid = ""
        if isinstance(dist, (int, float)) and dist <= thresh and cid:
            distance = dist
            # Record assignment metrics
            if settings.ENABLE_CLUSTER_METRICS:
                _record_online_metrics(os_name, cid, distance, False)
            return cid

    # Create a new prototype seeded with this templated line as its medoid/centroid
    cid = f"cluster_{uuid.uuid4().hex[:12]}"
    is_new_cluster = True
    distance = distance if distance is not None else 0.0
    
    collection_name = _proto_collection_name(os_name)
    text_len = len(text or "")
    try:
        provider = _get_provider()
        collection = provider.get_or_create_collection(collection_name)
        existing = -1
        try:
            cnt = collection.count()  # type: ignore[attr-defined]
            existing = int(cnt) if isinstance(cnt, int) else -1
        except Exception:
            pass
        LOG.debug(
            "online clustering: persisting prototype os=%s cluster=%s collection=%s text_len=%d existing=%d",
            os_name,
            cid,
            collection_name,
            text_len,
            existing,
        )
        collection.add(
            ids=[cid],
            documents=[text],
            metadatas=[{
                "os": os_name,
                "label": "unknown",
                "rationale": "online",
                "size": 1,
                "exemplar_count": 0,
                "created_by": "online",
            }],
        )
    except Exception as exc:
        LOG.exception(
            "online clustering: failed to persist prototype os=%s cluster=%s collection=%s text_len=%d",
            os_name,
            cid,
            collection_name,
            text_len,
        )
        try:
            import logging as _logging
            _logging.getLogger("app.kaboom").info(
                "persist_prototype_failed os=%s cluster=%s collection=%s err=%s",
                os_name,
                cid,
                collection_name,
                exc,
            )
        except Exception:
            pass
    
    # Record new cluster creation
    if settings.ENABLE_CLUSTER_METRICS:
        _record_online_metrics(os_name, cid, distance, is_new_cluster)
    
    return cid


def _record_online_metrics(os_name: str, cluster_id: str, distance: float, is_new_cluster: bool) -> None:
    """Record online clustering metrics asynchronously."""
    if not settings.ENABLE_CLUSTER_METRICS:
        return
        
    # Use a simple approach without creating tasks to avoid "Task was destroyed" errors
    try:
        import redis.asyncio as aioredis
        from app.services.cluster_metrics import ClusterMetricsTracker
        
        async def _record_sync(os_name: str, cluster_id: str, distance: float, is_new_cluster: bool) -> None:
            """Synchronous version of metrics recording."""
            redis_client = None
            try:
                redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
                tracker = ClusterMetricsTracker(redis_client)
                await tracker.record_online_cluster_assignment(os_name, cluster_id, distance, is_new_cluster)
            except RedisConnectionError as exc:
                LOG.debug(
                    "online clustering: metrics skipped os=%s cluster=%s reason=redis_unreachable err=%s",
                    os_name,
                    cluster_id,
                    exc,
                )
            except Exception as exc:
                LOG.debug(
                    "online clustering: metrics failed os=%s cluster=%s err=%s",
                    os_name,
                    cluster_id,
                    exc,
                )
            finally:
                if redis_client is not None:
                    await redis_client.close()
        
        # Create a new event loop just for this operation to avoid conflicts
        try:
            loop = asyncio.get_running_loop()
            # If there's a running loop, use asyncio.run_coroutine_threadsafe
            import threading
            
            def _run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                new_loop.run_until_complete(_record_sync(os_name, cluster_id, distance, is_new_cluster))
                new_loop.close()
            
            thread = threading.Thread(target=_run_in_thread, daemon=True)
            thread.start()
            
        except RuntimeError:
            # No running loop, create a new one
            asyncio.run(_record_sync(os_name, cluster_id, distance, is_new_cluster))
            
    except Exception as exc:
        LOG.debug("online clustering: metrics creation failed os=%s cluster=%s err=%s", os_name, cluster_id, exc)






