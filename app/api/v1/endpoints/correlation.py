from __future__ import annotations

from typing import Any, Dict, Tuple, List
import asyncio
import time
import os
import logging
import re

from fastapi import APIRouter, Query

from app.core.config import settings
import redis.asyncio as aioredis
from app.services.cross_correlation import (
    compute_global_clusters,
    build_graph_from_clusters,
    compute_global_prototype_clusters_hdbscan,
)

router = APIRouter()
LOG = logging.getLogger(__name__)
redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _normalize_key(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\d+", "<n>", s)
    s = re.sub(r"\s+", " ", s)
    return s[:180]


def _redis_key_from_line(line: str) -> str:
    if not line:
        return "empty"
    try:
        import json as _json
        obj = _json.loads(line)
        if isinstance(obj, dict):
            parts: List[str] = []
            for k in ("type", "ruleName", "testName", "summary", "Message", "Name"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            if parts:
                return _normalize_key(" | ".join(parts))
    except Exception:
        pass
    return _normalize_key(line)


async def _compute_redis_clusters(limit: int, min_size: int, include_logs_per_cluster: int) -> Dict[str, Any]:
    entries = await redis.xrevrange("logs", max="+", min="-", count=limit)
    groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for msg_id, data in entries:
        line = data.get("line") or ""
        key = _redis_key_from_line(line)
        groups.setdefault(key, []).append((msg_id, data))

    clusters: List[Dict[str, Any]] = []
    for idx, (key, items) in enumerate(groups.items()):
        if len(items) < min_size:
            continue
        samples: List[Dict[str, Any]] = []
        src_counts: Dict[str, int] = {}
        os_counts: Dict[str, int] = {}
        for msg_id, data in items[: max(0, include_logs_per_cluster)]:
            src = str(data.get("source") or "")
            os_hint = src.split(":", 1)[1] if ":" in src else "unknown"
            src_counts[src] = src_counts.get(src, 0) + 1
            os_counts[os_hint] = os_counts.get(os_hint, 0) + 1
            samples.append({
                "id": msg_id,
                "document": data.get("line") or "",
                "os": os_hint,
                "source": src,
                "raw": data.get("line") or "",
            })
        clusters.append({
            "id": f"gcluster_{idx}",
            "size": len(items),
            "centroid": [],
            "medoid_document": key,
            "source_breakdown": src_counts,
            "os_breakdown": os_counts,
            "sample_logs": samples,
        })

    return {
        "params": {
            "algorithm": "grouped",
            "basis": "redis",
            "limit": limit,
            "min_size": min_size,
            "include_logs_per_cluster": include_logs_per_cluster,
        },
        "clusters": clusters,
    }

_CACHE: dict[Tuple[str, Tuple[Tuple[str, Any], ...]], Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL_SEC: float = 30.0


def _cache_key(route: str, params: Dict[str, Any]) -> Tuple[str, Tuple[Tuple[str, Any], ...]]:
    items: list[Tuple[str, Any]] = []
    for k, v in sorted(params.items()):
        # stringify unhashables defensively
        if isinstance(v, (list, dict, set, tuple)):
            items.append((k, str(v)))
        else:
            items.append((k, v))
    return (route, tuple(items))


def _cache_get(route: str, params: Dict[str, Any]) -> Dict[str, Any] | None:
    key = _cache_key(route, params)
    cached = _CACHE.get(key)
    if not cached:
        return None
    ts, payload = cached
    if (time.time() - ts) > _CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_set(route: str, params: Dict[str, Any], payload: Dict[str, Any]) -> None:
    _CACHE[_cache_key(route, params)] = (time.time(), payload)


@router.get("/correlation/global", response_model=Dict[str, Any])
async def get_global_correlation(
    limit_per_source: int = Query(200, ge=1, le=2000, description="Max logs per distinct source (for logs basis)"),
    threshold: float | None = Query(None, description="Override cluster distance threshold (for single-pass logs)"),
    min_size: int | None = Query(None, description="Override minimum cluster size (for single-pass logs)"),
    include_logs_per_cluster: int = Query(20, ge=0, le=200, description="Sample logs per cluster in response"),
    algorithm: str = Query("hdbscan", description='Clustering algorithm: "hdbscan" | "single_pass"'),
    basis: str = Query("prototypes", description='Clustering basis: "prototypes" | "logs"'),
    min_cluster_size: int = Query(5, ge=2, le=1000, description="HDBSCAN min_cluster_size when algorithm=hdbscan"),
    min_samples: int | None = Query(None, description="HDBSCAN min_samples when algorithm=hdbscan (default=min_cluster_size)"),
) -> Dict[str, Any]:
    """Compute cross-source clusters across all OS.
    
    Default: HDBSCAN over prototypes (basis=prototypes).
    Fallback: single-pass over logs when basis=logs.
    """
    # Allow disabling HDBSCAN to avoid instability in constrained environments
    if os.getenv("DISABLE_HDBSCAN", "false").lower() in {"1", "true", "yes"}:
        algorithm = "single_pass"
        basis = "logs"
        # Keep correlation lightweight in safe mode
        limit_per_source = min(limit_per_source, 20)
        include_logs_per_cluster = min(include_logs_per_cluster, 10)

    params0 = {
        "limit_per_source": limit_per_source,
        "threshold": threshold,
        "min_size": min_size,
        "include_logs_per_cluster": include_logs_per_cluster,
        "algorithm": algorithm,
        "basis": basis,
        "min_cluster_size": min_cluster_size,
        "min_samples": min_samples,
    }
    cached = _cache_get("global", params0)
    if cached is not None:
        return cached

    if os.getenv("DISABLE_GLOBAL_CLUSTERING", "false").lower() in {"1", "true", "yes"}:
        payload = {"clusters": [], "params": {"disabled": True}}
        _cache_set("global", params0, payload)
        return payload

    if os.getenv("CORRELATION_FALLBACK_REDIS", "false").lower() in {"1", "true", "yes"}:
        payload = await _compute_redis_clusters(
            limit=300,
            min_size=max(2, int(min_cluster_size)),
            include_logs_per_cluster=include_logs_per_cluster,
        )
        _cache_set("global", params0, payload)
        return payload

    # Preferred path: HDBSCAN over prototypes
    if basis == "prototypes" and algorithm == "hdbscan":
        try:
            proto_result = await asyncio.to_thread(
                compute_global_prototype_clusters_hdbscan,
                min_cluster_size=min_cluster_size,
                min_samples=min_samples,
                include_logs_per_cluster=include_logs_per_cluster,
            )
        except Exception as exc:
            LOG.info("global correlation clustering failed err=%s", exc)
            payload = {"clusters": [], "params": {"error": "clustering_failed"}}
            _cache_set("global", params0, payload)
            return payload
        clusters = proto_result.get("clusters") or []
        if clusters:
            _cache_set("global", params0, proto_result)
            return proto_result

        # Demo-friendly fallback: if no prototype clusters found, fall back to
        # logs-based single-pass clustering so the UI can still display something.
        fallback_threshold = (
            threshold if threshold is not None else settings.CLUSTER_DISTANCE_THRESHOLD
        )
        # Be slightly more permissive than the default to encourage forming clusters
        default_min = getattr(settings, "CLUSTER_MIN_SIZE", 5)
        fallback_min_size = (
            min_size if min_size is not None else max(2, int(default_min) // 2)
        )
        try:
            logs_result = await asyncio.to_thread(
                compute_global_clusters,
                limit_per_source=limit_per_source,
                threshold=fallback_threshold,
                min_size=fallback_min_size,
                include_logs_per_cluster=include_logs_per_cluster,
            )
        except Exception as exc:
            LOG.info("global correlation logs fallback failed err=%s", exc)
            payload = {"clusters": [], "params": {"error": "clustering_failed"}}
            _cache_set("global", params0, payload)
            return payload
        params = logs_result.setdefault("params", {})
        params.setdefault("basis", "logs")
        params.setdefault("algorithm", "single_pass")
        _cache_set("global", params0, logs_result)
        return logs_result

    # Explicit logs-based path (or non-HDBSCAN algorithm)
    try:
        out = await asyncio.to_thread(
            compute_global_clusters,
            limit_per_source=limit_per_source,
            threshold=threshold,
            min_size=min_size,
            include_logs_per_cluster=include_logs_per_cluster,
            max_items_per_os=200,
        )
    except Exception as exc:
        LOG.info("global correlation logs failed err=%s", exc)
        payload = {"clusters": [], "params": {"error": "clustering_failed"}}
        _cache_set("global", params0, payload)
        return payload
    _cache_set("global", params0, out)
    return out


@router.get("/correlation/graph", response_model=Dict[str, Any])
async def get_global_correlation_graph(
    limit_per_source: int = Query(200, ge=1, le=2000, description="Max logs per distinct source (for logs basis)"),
    threshold: float | None = Query(None, description="Override cluster distance threshold (for single-pass logs)"),
    min_size: int | None = Query(None, description="Override minimum cluster size (for single-pass logs)"),
    include_logs_per_cluster: int = Query(5, ge=0, le=50, description="Keep sample size small for graph view"),
    algorithm: str = Query("hdbscan", description='Clustering algorithm: "hdbscan" | "single_pass"'),
    basis: str = Query("prototypes", description='Clustering basis: "prototypes" | "logs"'),
    min_cluster_size: int = Query(5, ge=2, le=1000, description="HDBSCAN min_cluster_size when algorithm=hdbscan"),
    min_samples: int | None = Query(None, description="HDBSCAN min_samples when algorithm=hdbscan (default=min_cluster_size)"),
) -> Dict[str, Any]:
    """Return graph representation of cross-source clusters."""
    if os.getenv("DISABLE_HDBSCAN", "false").lower() in {"1", "true", "yes"}:
        algorithm = "single_pass"
        basis = "logs"
        limit_per_source = min(limit_per_source, 20)
        include_logs_per_cluster = min(include_logs_per_cluster, 5)

    params0 = {
        "limit_per_source": limit_per_source,
        "threshold": threshold,
        "min_size": min_size,
        "include_logs_per_cluster": include_logs_per_cluster,
        "algorithm": algorithm,
        "basis": basis,
        "min_cluster_size": min_cluster_size,
        "min_samples": min_samples,
    }
    cached = _cache_get("graph", params0)
    if cached is not None:
        return cached

    if os.getenv("DISABLE_GLOBAL_CLUSTERING", "false").lower() in {"1", "true", "yes"}:
        payload = {"nodes": [], "edges": [], "params": {"disabled": True}}
        _cache_set("graph", params0, payload)
        return payload

    if os.getenv("CORRELATION_FALLBACK_REDIS", "false").lower() in {"1", "true", "yes"}:
        base = await _compute_redis_clusters(
            limit=300,
            min_size=max(2, int(min_cluster_size)),
            include_logs_per_cluster=include_logs_per_cluster,
        )
        out = build_graph_from_clusters(base)
        _cache_set("graph", params0, out)
        return out

    base: Dict[str, Any]

    if basis == "prototypes" and algorithm == "hdbscan":
        try:
            proto_result = await asyncio.to_thread(
                compute_global_prototype_clusters_hdbscan,
                min_cluster_size=min_cluster_size,
                min_samples=min_samples,
                include_logs_per_cluster=include_logs_per_cluster,
            )
        except Exception as exc:
            LOG.info("graph correlation clustering failed err=%s", exc)
            payload = {"nodes": [], "edges": [], "params": {"error": "clustering_failed"}}
            _cache_set("graph", params0, payload)
            return payload
        clusters = proto_result.get("clusters") or []
        if clusters:
            base = proto_result
        else:
            # Same demo-friendly fallback as /correlation/global
            fallback_threshold = (
                threshold if threshold is not None else settings.CLUSTER_DISTANCE_THRESHOLD
            )
            default_min = getattr(settings, "CLUSTER_MIN_SIZE", 5)
            fallback_min_size = (
                min_size if min_size is not None else max(2, int(default_min) // 2)
            )
            try:
                base = await asyncio.to_thread(
                    compute_global_clusters,
                    limit_per_source=limit_per_source,
                    threshold=fallback_threshold,
                    min_size=fallback_min_size,
                    include_logs_per_cluster=include_logs_per_cluster,
                )
            except Exception as exc:
                LOG.info("graph correlation logs fallback failed err=%s", exc)
                payload = {"nodes": [], "edges": [], "params": {"error": "clustering_failed"}}
                _cache_set("graph", params0, payload)
                return payload
            params = base.setdefault("params", {})
            params.setdefault("basis", "logs")
            params.setdefault("algorithm", "single_pass")
    else:
        try:
            base = await asyncio.to_thread(
                compute_global_clusters,
                limit_per_source=limit_per_source,
                threshold=threshold,
                min_size=min_size,
                include_logs_per_cluster=include_logs_per_cluster,
                max_items_per_os=200,
            )
        except Exception as exc:
            LOG.info("graph correlation logs failed err=%s", exc)
            payload = {"nodes": [], "edges": [], "params": {"error": "clustering_failed"}}
            _cache_set("graph", params0, payload)
            return payload

    out = build_graph_from_clusters(base)
    _cache_set("graph", params0, out)
    return out





