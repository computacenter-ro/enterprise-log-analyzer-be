from typing import Any, Dict, List, Optional

import json
import time
from fastapi import APIRouter, Query, HTTPException
import redis.asyncio as aioredis

from app.core.config import get_settings


router = APIRouter()
settings = get_settings()
redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _parse_result(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        # fallback: attempt to coerce single quotes -> double quotes
        try:
            cleaned = raw.replace("'", '"')
            return json.loads(cleaned)
        except Exception:
            return {"raw": raw}


def _parse_env_ids(raw: str | None) -> List[str]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val if x is not None]
    except Exception:
        pass
    return []


def _parse_logs(raw: str | None) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return val
    except Exception:
        return []
    return []


@router.get("")
async def list_alerts(
    limit: int = Query(100, ge=1, le=1000),
    env_id: Optional[str] = Query(None, description="Filter alerts to a specific environment id (or leave empty for all)"),
) -> List[Dict[str, Any]]:
    """List alerts from the last ALERTS_TTL_SEC and include any persisted ones. Includes evidence logs and env metadata."""
    now_ms = int(time.time() * 1000)
    min_id = f"{now_ms - (int(settings.ALERTS_TTL_SEC) * 1000)}-0"

    # Fetch persisted ids once
    try:
        persisted_ids = await redis.smembers(settings.ALERTS_PERSISTED_SET)  # type: ignore[misc]
    except Exception:
        persisted_ids = set()

    # Fetch recent from stream, newest first (bounded).
    # Use min_id "-" to include ALL alerts regardless of age.
    stream_entries = await redis.xrevrange(settings.ALERTS_STREAM, max="+", min="-", count=limit)

    seen_ids: set[str] = set()
    out: List[Dict[str, Any]] = []
    
    # OPTIMIZATION: Pipeline hash fetches for stream entries to get enriched data
    # Stream entries might have limited fields, but hashes have complete alert data
    if stream_entries:
        pipe = redis.pipeline(transaction=False)
        for entry_id, _ in stream_entries:
            pipe.hgetall(f"alert:{entry_id}")
        
        hash_results = await pipe.execute()
        
        for (entry_id, stream_fields), hash_data in zip(stream_entries, hash_results):
            seen_ids.add(entry_id)
            
            # Prefer hash data if available (more complete), fallback to stream fields
            if hash_data:
                result_obj = _parse_result(hash_data.get("result"))
                env_ids = _parse_env_ids(hash_data.get("env_ids"))
                evidence_logs = _parse_logs(hash_data.get("evidence_logs"))
                out.append({
                    "id": entry_id,
                    "type": hash_data.get("type", ""),
                    "os": hash_data.get("os", ""),
                    "issue_key": hash_data.get("issue_key", ""),
                    "summary": hash_data.get("summary") or result_obj.get("summary", ""),
                    "solution": hash_data.get("solution") or result_obj.get("recommendation", ""),
                    "result": result_obj,
                    "persisted": (entry_id in persisted_ids),
                    "env_id": hash_data.get("env_id") or (env_ids[0] if len(env_ids) == 1 else None),
                    "env_ids": env_ids,
                    "logs": evidence_logs,
                    "cluster_id": hash_data.get("cluster_id", ""),
                })
            else:
                # Fallback to stream data if hash doesn't exist
                result_obj = _parse_result(stream_fields.get("result"))
                env_ids = _parse_env_ids(stream_fields.get("env_ids"))
                evidence_logs = _parse_logs(stream_fields.get("evidence_logs"))
                out.append({
                    "id": entry_id,
                    "type": stream_fields.get("type", ""),
                    "os": stream_fields.get("os", ""),
                    "issue_key": stream_fields.get("issue_key", ""),
                    "summary": stream_fields.get("summary") or result_obj.get("summary", ""),
                    "solution": stream_fields.get("solution") or result_obj.get("recommendation", ""),
                    "result": result_obj,
                    "persisted": (entry_id in persisted_ids),
                    "env_id": stream_fields.get("env_id") or (env_ids[0] if len(env_ids) == 1 else None),
                    "env_ids": env_ids,
                    "logs": evidence_logs,
                    "cluster_id": stream_fields.get("cluster_id", ""),
                })

    # If we still need more, include older persisted alerts (outside TTL)
    remaining = max(0, limit - len(out))
    if remaining > 0 and persisted_ids:
        # Older persisted ids not already included; sort by id desc (time component)
        candidates = sorted([pid for pid in persisted_ids if pid not in seen_ids], reverse=True)
        to_fetch = candidates[:remaining]
        if to_fetch:
            pipe = redis.pipeline(transaction=False)
            for pid in to_fetch:
                pipe.hgetall(f"alert:{pid}")
            fetched = await pipe.execute()
            for pid, data in zip(to_fetch, fetched):
                if not data:
                    continue
                result_obj = _parse_result(data.get("result"))
                env_ids = _parse_env_ids(data.get("env_ids"))
                evidence_logs = _parse_logs(data.get("evidence_logs"))
                out.append({
                    "id": pid,
                    "type": data.get("type", ""),
                    "os": data.get("os", ""),
                    "issue_key": data.get("issue_key", ""),
                    "result": result_obj,
                    "persisted": True,
                    "env_id": data.get("env_id") or (env_ids[0] if len(env_ids) == 1 else None),
                    "env_ids": env_ids,
                    "logs": evidence_logs,
                    "cluster_id": data.get("cluster_id", ""),
                })

    # Sort by id (time component) desc and cap to limit
    out.sort(key=lambda a: a.get("id", ""), reverse=True)
    # Filter by env_id if provided
    if env_id:
        out = [a for a in out if env_id in (a.get("env_ids") or []) or a.get("env_id") == env_id]
    return out[:limit]


@router.post("/{entry_id}/persist")
async def persist_alert(entry_id: str) -> Dict[str, Any]:
    """Persist an alert beyond TTL: remove hash expiry and mark persisted set."""
    key = f"alert:{entry_id}"
    exists = await redis.exists(key)
    if not exists:
        # Try to reconstruct from the stream entry if available
        entries = await redis.xrange(settings.ALERTS_STREAM, min=entry_id, max=entry_id, count=1)
        if not entries:
            raise HTTPException(status_code=404, detail="alert not found")
        _, fields = entries[0]
        to_store = {**fields, "id": entry_id}
        await redis.hset(key, mapping=to_store)  # type: ignore[misc]
    # Remove TTL and mark persisted
    await redis.persist(key)  # type: ignore[misc, arg-type]
    await redis.sadd(settings.ALERTS_PERSISTED_SET, entry_id)  # type: ignore[misc, arg-type]
    return {"status": "ok", "id": entry_id}


@router.post("/{entry_id}/feedback")
async def add_feedback(entry_id: str, feedback: str = Query(..., pattern="^(correct|incorrect)$")) -> Dict[str, Any]:
    """Add feedback to an alert."""
    key = f"alert:{entry_id}"
    exists = await redis.exists(key)
    if not exists:
        raise HTTPException(status_code=404, detail="alert not found")

    pipe = redis.pipeline()
    pipe.hset(key, "feedback", feedback)
    if feedback == "correct":
        pipe.sadd(settings.ALERTS_FEEDBACK_CORRECT_SET, entry_id)
        pipe.srem(settings.ALERTS_FEEDBACK_INCORRECT_SET, entry_id)
    else:
        pipe.sadd(settings.ALERTS_FEEDBACK_INCORRECT_SET, entry_id)
        pipe.srem(settings.ALERTS_FEEDBACK_CORRECT_SET, entry_id)
    await pipe.execute()  # type: ignore[misc]
    
    return {"status": "ok", "id": entry_id, "feedback": feedback}


