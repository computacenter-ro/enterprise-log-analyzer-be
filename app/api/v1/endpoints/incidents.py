from typing import Any, Dict, List, Optional, Tuple
import asyncio
import os
import time
import logging
from fastapi import APIRouter, Query

from app.services.cross_correlation import compute_global_clusters

router = APIRouter()
LOG = logging.getLogger(__name__)

_CACHE: dict[Tuple[str, int, int, int], Tuple[float, List[Dict[str, Any]]]] = {}
_CACHE_TTL_SEC: float = 30.0


def _clustering_disabled() -> bool:
    return os.getenv("DISABLE_GLOBAL_CLUSTERING", "false").lower() in {"1", "true", "yes"}


def _extract_env_ids(samples: List[Dict[str, Any]]) -> List[str]:
    envs: List[str] = []
    seen: set[str] = set()
    for s in samples:
        env = (s or {}).get("env_id")
        if env and env not in seen:
            seen.add(env)
            envs.append(env)
    return envs


def _severity_from_medoid(medoid: str) -> str:
    med_l = (medoid or "").lower()
    if any(x in med_l for x in ("failed", "error", "critical", "i/o error", "out of memory", "servfail", "timeout")):
        return "critical"
    return "warning"


@router.get("")
async def list_incidents(
    limit: int = Query(100, ge=1, le=1000),
    env_id: Optional[str] = Query(None, description="Filter incidents to a specific environment id (or leave empty for all)"),
    include_logs: int = Query(8, ge=0, le=50, description="How many evidence logs to include per incident"),
    limit_per_source: int = Query(50, ge=1, le=500, description="Cap logs per source before clustering"),
) -> List[Dict[str, Any]]:
    """
    Return incidents derived from LogBERT clusters (env-scoped when env_id is provided).

    Each incident includes evidence logs (raw + source + os + env_id) so the UI can display proof.
    """
    cache_key = (env_id or "__all__", int(limit), int(include_logs), int(limit_per_source))
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) <= _CACHE_TTL_SEC:
        return cached[1]

    if _clustering_disabled():
        _CACHE[cache_key] = (now, [])
        return []

    try:
        clusters_payload = await asyncio.wait_for(
            asyncio.to_thread(
                compute_global_clusters,
                limit_per_source=limit_per_source,
                include_logs_per_cluster=include_logs,
                env_id=env_id,
                max_items_per_os=600,
            ),
            timeout=30,
        )
        clusters = list(clusters_payload.get("clusters") or [])[: int(limit)]
    except Exception as exc:
        LOG.info("incidents clustering failed err=%s", exc)
        _CACHE[cache_key] = (now, [])
        return []

    incidents: List[Dict[str, Any]] = []
    for c in clusters:
        medoid = str(c.get("medoid_document") or "")
        samples = list(c.get("sample_logs") or [])
        envs = _extract_env_ids(samples)
        incidents.append(
            {
                "id": str(c.get("id") or ""),
                "env_ids": envs,
                "env_id": (envs[0] if len(envs) == 1 else None),
                "summary": medoid,
                "severity": _severity_from_medoid(medoid),
                "size": int(c.get("size") or 0),
                "logs": [
                    {
                        "id": s.get("id"),
                        "raw": s.get("raw"),
                        "source": s.get("source"),
                        "os": s.get("os"),
                        "env_id": s.get("env_id"),
                    }
                    for s in samples
                ],
                "params": clusters_payload.get("params") or {},
            }
        )

    _CACHE[cache_key] = (now, incidents)
    return incidents

