from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Set, Tuple

from fastapi import APIRouter, HTTPException
import asyncio

from app.core.config import settings
from app.services.chroma_service import ChromaClientProvider
from app.services.cross_correlation import compute_global_clusters


LOG = logging.getLogger(__name__)
router = APIRouter()


def _chroma_disabled() -> bool:
    return os.getenv("DISABLE_GLOBAL_CLUSTERING", "false").lower() in {"1", "true", "yes"}


def _fallback_env_ids() -> Set[str]:
    raw = os.getenv("SIM_ENV_IDS", "env-001,env-002,env-003")
    return {e.strip() for e in raw.split(",") if e.strip()}


def _discovery_timeout_sec() -> float:
    try:
        return float(os.getenv("ENV_DISCOVERY_TIMEOUT_SEC", "2"))
    except Exception:
        return 2.0

_provider: ChromaClientProvider | None = None


def _get_provider() -> ChromaClientProvider:
    global _provider
    if _provider is None:
        _provider = ChromaClientProvider()
    return _provider


def _map_status(status: str) -> str:
    s = (status or "").upper()
    if s == "FAILED":
        return "critical"
    if s == "DEGRADED":
        return "warning"
    return "healthy"


def _map_ci_type(ci_type: str) -> str:
    t = (ci_type or "").lower()
    if t == "server":
        return "server"
    if t in {"db", "database"}:
        return "database"
    if t in {"app"}:
        return "app"
    # treat monitoring/tests as apps for visualization purposes
    if "thousandeyes" in t:
        return "app"
    return "server"


def _extract_host_identifiers(raw: str) -> List[str]:
    """Extract likely host/device identifiers from a JSON log line."""
    try:
        obj = json.loads(raw)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []

    out: List[str] = []

    for k in ("ComputerName", "computerName", "host", "device_name", "device", "hostname", "name", "testName"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
            break

    affected = obj.get("affectedComponent")
    if isinstance(affected, dict):
        n = affected.get("name") or affected.get("id")
        if isinstance(n, str) and n.strip():
            out.append(n.strip())

    for k in ("ip", "device_ip", "deviceIp", "managementIpAddr", "dst_ip", "src_ip"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    seen: set[str] = set()
    deduped: List[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _discover_env_ids(limit_per_collection: int = 500) -> Set[str]:
    """Scan Chroma metadatas to discover env_ids present in ingested logs."""
    provider = _get_provider()
    env_ids: Set[str] = set()
    for os_name in ("linux", "macos", "windows", "network"):
        try:
            coll = provider.get_or_create_collection(f"{settings.CHROMA_LOG_COLLECTION_PREFIX}{os_name}")
            data = coll.get(include=["metadatas"], limit=limit_per_collection) or {}
            metas = data.get("metadatas") or []
            for m in metas:
                env = str((m or {}).get("env_id") or "").strip()
                if env:
                    env_ids.add(env)
        except Exception as exc:
            LOG.info("env discover: failed for os=%s err=%s", os_name, exc)
            continue
    return env_ids


def _load_env_logs(env_id: str, limit_per_collection: int = 300) -> List[Dict[str, Any]]:
    """Load a slice of logs for a specific env_id from each collection."""
    provider = _get_provider()
    out: List[Dict[str, Any]] = []
    for os_name in ("linux", "macos", "windows", "network"):
        try:
            coll = provider.get_or_create_collection(f"{settings.CHROMA_LOG_COLLECTION_PREFIX}{os_name}")
            # Important: filter server-side by env_id; avoids scanning unrelated logs.
            data = coll.get(where={"env_id": env_id}, include=["documents", "metadatas"], limit=limit_per_collection) or {}
            docs = data.get("documents") or []
            metas = data.get("metadatas") or []
            for doc, meta in zip(docs, metas):
                m = meta or {}
                out.append({"document": doc, "meta": m})
        except Exception as exc:
            LOG.info("env logs: failed for os=%s err=%s", os_name, exc)
            continue
    return out


def _build_topology_from_logs(env_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    logs = _load_env_logs(env_id)
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    for entry in logs:
        raw = str((entry.get("meta") or {}).get("raw") or entry.get("document") or "")
        hosts = _extract_host_identifiers(raw)
        for h in hosts:
            if h not in nodes:
                nodes[h] = {"id": h, "label": h, "type": "server", "status": "healthy"}
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                frm = obj.get("from")
                to = obj.get("to")
                if isinstance(frm, str) and isinstance(to, str):
                    edges.append({"from": frm, "to": to, "status": "healthy"})
                deps = obj.get("depends_on")
                if isinstance(deps, list):
                    for d in deps:
                        if isinstance(d, str):
                            edges.append({"from": d, "to": obj.get("id") or obj.get("name") or "", "status": "healthy"})
        except Exception:
            pass

    return list(nodes.values()), edges


def _severity_from_medoid(medoid: str) -> str:
    med_l = (medoid or "").lower()
    if any(x in med_l for x in ("failed", "error", "critical", "i/o error", "out of memory", "servfail")):
        return "critical"
    return "warning"


def _build_correlation(env_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    if os.getenv("DISABLE_GLOBAL_CLUSTERING", "false").lower() in {"1", "true", "yes"}:
        return [], {}, {"disabled": True}
    # Keep env correlation responsive: cap the total work.
    # This endpoint is called on navigation, so it must not monopolize the API.
    try:
        clusters_payload = compute_global_clusters(
            limit_per_source=80,
            include_logs_per_cluster=12,
            env_id=env_id,
            max_items_per_os=400,
        )
    except Exception as exc:
        LOG.info("env correlation clustering failed env=%s err=%s", env_id, exc)
        return [], {}, {"error": "clustering_failed"}
    clusters = list(clusters_payload.get("clusters") or [])

    cluster_overlays: List[Dict[str, Any]] = []
    node_impacts: Dict[str, Dict[str, Any]] = {}
    for c in clusters:
        cid = str(c.get("id") or "")
        if not cid:
            continue
        samples = list(c.get("sample_logs") or [])
        host_counts: Dict[str, int] = {}
        for s in samples:
            raw = str((s or {}).get("raw") or "")
            for host in _extract_host_identifiers(raw):
                host_counts[host] = host_counts.get(host, 0) + 1

        if not host_counts:
            continue

        medoid = str(c.get("medoid_document") or "")
        severity = _severity_from_medoid(medoid)

        cluster_overlays.append(
            {
                "id": cid,
                "size": int(c.get("size") or 0),
                "severity": severity,
                "medoid": medoid,
                "host_breakdown": host_counts,
                "os_breakdown": c.get("os_breakdown") or {},
                "source_breakdown": c.get("source_breakdown") or {},
                "sample_logs": samples[:10],
            }
        )

        for node_id, cnt in host_counts.items():
            ni = node_impacts.setdefault(node_id, {"severity": "healthy", "clusters": []})
            ni["clusters"].append({"id": cid, "weight": int(cnt)})
            if severity == "critical":
                ni["severity"] = "critical"
            elif severity == "warning" and ni["severity"] != "critical":
                ni["severity"] = "warning"

    cluster_overlays.sort(key=lambda x: int(x.get("size") or 0), reverse=True)
    return cluster_overlays, node_impacts, clusters_payload.get("params") or {}


_CORR_CACHE: dict[str, tuple[float, Dict[str, Any]]] = {}
_CORR_CACHE_TTL_SEC: float = 30.0


def _region_coordinates(env_id: str) -> Dict[str, float]:
    """Return lat/lng coordinates for demo environments.
    
    For generic env-xxx IDs, cluster them in the same geographic region
    for better visualization but with slight variations.
    """
    env_id_lower = env_id.lower()
    
    # For generic env IDs like env-001, env-002, env-003, cluster them near US East Coast
    # but with HUGE geographic separation to ensure they appear as distinct points
    if env_id_lower.startswith("env-"):
        env_num = int(''.join(filter(str.isdigit, env_id)) or "1")
        # Extreme coordinates to force separation:
        # env-001: Anchorage, Alaska
        # env-002: Miami, Florida
        # env-003: Honolulu, Hawaii
        locations = [
            {"lat": 61.2181, "lng": -149.9003}, # Anchorage (North West)
            {"lat": 25.7617, "lng": -80.1918},  # Miami (South East)
            {"lat": 21.3069, "lng": -157.8583}, # Honolulu (Deep West)
            {"lat": 44.8113, "lng": -91.4985},  # Eau Claire (North Central)
            {"lat": 32.7157, "lng": -117.1611}, # San Diego (South West)
            {"lat": 42.3601, "lng": -71.0589},  # Boston (North East)
        ]
        return locations[(env_num - 1) % len(locations)]
    
    # US regions with more specific locations
    if "us-east-1" in env_id_lower or "virginia" in env_id_lower:
        return {"lat": 39.0438, "lng": -77.4878}  # Northern Virginia (Ashburn)
    if "us-east" in env_id_lower or "east" in env_id_lower:
        return {"lat": 35.2271, "lng": -80.8431}  # North Carolina (Charlotte)
    if "us-west-2" in env_id_lower or "oregon" in env_id_lower:
        return {"lat": 45.5152, "lng": -122.6784}  # Oregon (Portland)
    if "us-west" in env_id_lower or "west" in env_id_lower:
        return {"lat": 37.4419, "lng": -122.1430}  # California Bay Area
    if "us-west-1" in env_id_lower:
        return {"lat": 36.7783, "lng": -119.4179}  # California (central)
    if ("central" in env_id_lower and "us" in env_id_lower) or "iowa" in env_id_lower:
        return {"lat": 41.2524, "lng": -95.9980}  # Iowa (central US)
    
    # EU regions
    if "eu-west-1" in env_id_lower or "ireland" in env_id_lower:
        return {"lat": 53.3498, "lng": -6.2603}  # Ireland (Dublin)
    if "eu-west-2" in env_id_lower or "london" in env_id_lower:
        return {"lat": 51.5074, "lng": -0.1278}  # London
    if "eu-central-1" in env_id_lower or "frankfurt" in env_id_lower:
        return {"lat": 50.1109, "lng": 8.6821}  # Frankfurt
    if "eu-north-1" in env_id_lower or "stockholm" in env_id_lower:
        return {"lat": 59.3293, "lng": 18.0686}  # Stockholm
    if "eu-west-3" in env_id_lower or "paris" in env_id_lower:
        return {"lat": 48.8566, "lng": 2.3522}  # Paris
    
    # APAC regions
    if "ap-southeast-1" in env_id_lower or "singapore" in env_id_lower:
        return {"lat": 1.3521, "lng": 103.8198}  # Singapore
    if "ap-southeast-2" in env_id_lower or "sydney" in env_id_lower:
        return {"lat": -33.8688, "lng": 151.2093}  # Sydney
    if "ap-northeast-1" in env_id_lower or "tokyo" in env_id_lower:
        return {"lat": 35.6762, "lng": 139.6503}  # Tokyo
    if "ap-northeast-2" in env_id_lower or "seoul" in env_id_lower:
        return {"lat": 37.5665, "lng": 126.9780}  # Seoul
    if "ap-south-1" in env_id_lower or "mumbai" in env_id_lower:
        return {"lat": 19.0760, "lng": 72.8777}  # Mumbai
    if "ap-east-1" in env_id_lower or "hongkong" in env_id_lower or "hong kong" in env_id_lower:
        return {"lat": 22.3193, "lng": 114.1694}  # Hong Kong
    
    # South America
    if "sa-east-1" in env_id_lower or "saopaulo" in env_id_lower or "sao paulo" in env_id_lower:
        return {"lat": -23.5505, "lng": -46.6333}  # Sao Paulo
    
    # Africa
    if "af-south-1" in env_id_lower or "capetown" in env_id_lower or "cape town" in env_id_lower:
        return {"lat": -33.9249, "lng": 18.4241}  # Cape Town
    
    # Default to Northern Virginia for truly unknown
    return {"lat": 39.0438, "lng": -77.4878}  # Ashburn, VA


@router.get("")
async def list_environments() -> Dict[str, Any]:
    if _chroma_disabled():
        env_ids = _fallback_env_ids()
    else:
        try:
            env_ids = await asyncio.wait_for(
                asyncio.to_thread(_discover_env_ids),
                timeout=_discovery_timeout_sec(),
            )
        except Exception as exc:
            LOG.info("env list: discovery failed err=%s", exc)
            env_ids = _fallback_env_ids()
    # Keep this endpoint fast: do NOT run clustering here (it can be expensive and blocks the UI).
    # The detail/correlation endpoints compute overlays on demand.
    if not env_ids:
        return {"items": []}

    out: List[Dict[str, Any]] = []
    for env_id in sorted(env_ids):
        coords = _region_coordinates(env_id)
        out.append(
            {
                "id": env_id,
                "name": env_id.replace("-", " ").title(),
                "region": env_id,
                "status": "healthy",
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
                "clusters": 0,
                "coordinates": coords,
            }
        )
    return {"items": out}


@router.get("/{env_id}")
async def environment_detail(env_id: str) -> Dict[str, Any]:
    if _chroma_disabled():
        env_ids = _fallback_env_ids()
    else:
        try:
            env_ids = await asyncio.wait_for(
                asyncio.to_thread(_discover_env_ids),
                timeout=_discovery_timeout_sec(),
            )
        except Exception as exc:
            LOG.info("env detail: discovery failed err=%s", exc)
            env_ids = _fallback_env_ids()
    if env_id not in env_ids:
        raise HTTPException(status_code=404, detail=f"env_id {env_id} not found in ingested data")

    # IMPORTANT: Keep env detail fast. Correlation overlays are available via /correlation
    # and are fetched separately by the UI.
    if _chroma_disabled():
        nodes, edges = [], []
    else:
        nodes, edges = await asyncio.to_thread(_build_topology_from_logs, env_id)

    return {
        "id": env_id,
        "name": env_id.replace("-", " ").title(),
        "region": None,
        "status": "healthy",
        "topology": {"nodes": nodes, "edges": edges},
        "incidents": [],
        "clusters": [],
        "node_impacts": {},
        "params": {"timestamp": datetime.now(timezone.utc).isoformat()},
    }


@router.get("/{env_id}/correlation")
async def environment_correlation(env_id: str) -> Dict[str, Any]:
    if _chroma_disabled():
        env_ids = _fallback_env_ids()
    else:
        try:
            env_ids = await asyncio.wait_for(
                asyncio.to_thread(_discover_env_ids),
                timeout=_discovery_timeout_sec(),
            )
        except Exception as exc:
            LOG.info("env correlation: discovery failed err=%s", exc)
            env_ids = _fallback_env_ids()
    if env_id not in env_ids:
        raise HTTPException(status_code=404, detail=f"env_id {env_id} not found in ingested data")

    now = time.time()
    cached = _CORR_CACHE.get(env_id)
    if cached and (now - cached[0]) <= _CORR_CACHE_TTL_SEC:
        return cached[1]

    if _chroma_disabled():
        nodes, edges = [], []
        overlays, node_impacts, params = [], {}, {"disabled": True}
    else:
        nodes, edges = await asyncio.to_thread(_build_topology_from_logs, env_id)
        overlays, node_impacts, params = await asyncio.to_thread(_build_correlation, env_id)

    payload = {
        "environment_id": env_id,
        "topology": {"nodes": nodes, "edges": edges},
        "clusters": overlays,
        "node_impacts": node_impacts,
        "params": params,
    }
    _CORR_CACHE[env_id] = (now, payload)
    return payload

