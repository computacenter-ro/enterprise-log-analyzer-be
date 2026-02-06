import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.services.chroma_service import ChromaClientProvider
from app.services.online_clustering import assign_or_create_cluster
from app.parsers.linux import parse_linux_line
from app.parsers.macos import parse_macos_line
from app.parsers.templating import render_templated_line
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


def _os_from_source(source: str | None) -> str:
    if not source:
        return "unknown"
    s = source.lower()
    # Integration sources (simulation / upstream connectors)
    if s.startswith("scom:") or s.startswith("squaredup:"):
        return "windows"
    if s.startswith("catalyst:") or s.startswith("thousandeyes:"):
        return "network"
    if "linux.log" in s:
        return "linux"
    if "mac.log" in s:
        return "macos"
    if "windows" in s:
        return "windows"
    return "unknown"


_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_ISO_TS_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
_LONG_NUM_RE = re.compile(r"\b\d{4,}\b")


def _sanitize_text(text: str) -> str:
    # Strip common high-cardinality tokens that fragment clustering.
    t = text or ""
    t = _ISO_TS_RE.sub("<ts>", t)
    t = _IP_RE.sub("<ip>", t)
    t = _LONG_NUM_RE.sub("<num>", t)
    return t


def _try_parse_json_line(line: str) -> Dict[str, Any] | None:
    s = (line or "").strip()
    if not s:
        return None
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _normalize_json_for_clustering(source: str | None, line: str) -> Tuple[str, Dict[str, str]] | None:
    """Normalize JSON integration payloads into stable text for clustering."""
    obj = _try_parse_json_line(line)
    if not obj:
        return None

    # Only normalize for integration-like sources (avoid touching real syslog-ish lines).
    s = (source or "").lower()
    if not (s.startswith("scom:") or s.startswith("squaredup:") or s.startswith("catalyst:") or s.startswith("thousandeyes:")):
        return None

    env_id = obj.get("EnvironmentId") or obj.get("env_id") or obj.get("environment_id") or ""
    host = obj.get("ComputerName") or obj.get("Host") or obj.get("host") or obj.get("component") or obj.get("Component") or ""

    # Prefer stable, low-cardinality fields; fall back to a pruned JSON dump.
    parts: List[str] = []
    if s.startswith("scom:"):
        channel = obj.get("Channel") or ""
        level = obj.get("LevelDisplayName") or obj.get("level") or ""
        msg = obj.get("Message") or obj.get("message") or ""
        if msg in {None, "None"}:
            msg = ""
        parts = [str(x) for x in ["scom", channel, level, host, msg] if x]
    else:
        # Generic connector payload: build from a stable key subset.
        stable_keys = [
            "type",
            "status",
            "Status",
            "severity",
            "Severity",
            "metric",
            "Metric",
            "test",
            "test_name",
            "TestName",
            "name",
            "Name",
            "service",
            "Service",
            "component",
            "Component",
            "ComputerName",
            "message",
            "Message",
            "error",
            "Error",
            "summary",
            "Summary",
        ]
        for k in stable_keys:
            v = obj.get(k)
            if v in (None, "", "None"):
                continue
            parts.append(f"{k}={v}")

    if not parts:
        # Prune obvious high-cardinality fields then dump deterministically.
        bad_keys = {
            "TimeGenerated",
            "time",
            "ts",
            "timestamp",
            "ip",
            "IP",
            "Id",
            "id",
            "uuid",
            "request_id",
            "ray_id",
        }
        pruned = {k: v for k, v in obj.items() if k not in bad_keys}
        parts = [json.dumps(pruned, sort_keys=True, ensure_ascii=False)]

    content = _sanitize_text(" ".join(str(p) for p in parts if p))
    component = str(host or (s.split(":", 1)[0] if s else "unknown"))
    parsed: Dict[str, str] = {
        "component": component,
        "PID": "",
        "content": content,
    }
    if env_id:
        parsed["env_id"] = str(env_id)

    templated = render_templated_line(component=component, pid=None, content=content)
    return templated, parsed


def _parse_and_template(os_name: str, line: str) -> Tuple[str, Dict[str, str]]:
    parsed: Dict[str, str] | None = None
    if os_name == "linux":
        parsed = parse_linux_line(0, line) or None
    elif os_name == "macos":
        parsed = parse_macos_line(0, line) or None
    if not parsed:
        templated = render_templated_line(component="unknown", pid=None, content=line)
        return templated, {"content": line, "component": "unknown"}
    templated = render_templated_line(
        component=parsed.get("component", ""),
        pid=parsed.get("PID"),
        content=parsed.get("content", ""),
    )
    return templated, parsed


def _issue_key(os_name: str, parsed: Dict[str, str]) -> str:
    component = parsed.get("component", "unknown").lower().strip()
    pid = parsed.get("PID", "").strip()
    return f"{os_name}|{component}|{pid or 'nopid'}"


@dataclass
class Issue:
    os: str
    key: str
    created_at: float
    last_seen_at: float
    logs: List[Dict[str, Any]] = field(default_factory=list)

    def add_log(self, raw: str, templated: str, parsed: Dict[str, str]) -> None:
        now = time.time()
        self.logs.append({
            "raw": raw,
            "templated": templated,
            "parsed": parsed,
            "ts": now,
        })
        self.last_seen_at = now

    def top_logs(self, limit: int) -> List[Dict[str, Any]]:
        # naive heuristic: keep order, cap length
        return self.logs[:limit]


_issues: Dict[str, Issue] = {}


async def _close_and_publish(issue: Issue) -> None:
    # Serialize logs as JSON; Redis stream field values must be strings
    logs_list = [
        {
            "templated": log["templated"],
            "raw": log["raw"],
            "component": log["parsed"].get("component", ""),
            "pid": log["parsed"].get("PID", ""),
            "time": log.get("ts", 0),
        }
        for log in issue.top_logs(settings.ISSUE_MAX_LOGS_FOR_LLM)
    ]
    payload: Dict[str, str] = {
        "os": issue.os,
        "issue_key": issue.key,
        # send compact representation: concatenate templated as a rough summary
        "templated_summary": " \n".join([log["templated"] for log in issue.top_logs(settings.ISSUE_MAX_LOGS_FOR_LLM)]),
        "logs": json.dumps(logs_list),
    }
    await redis.xadd(settings.ISSUES_CANDIDATES_STREAM, payload)  # type: ignore[arg-type]
    LOG.info("published issue os=%s key=%s logs=%d", issue.os, issue.key, len(issue.logs))


async def run_issues_aggregator() -> None:
    """Consume raw logs from 'logs' stream, group them into issues, publish issues when idle."""
    stream = "logs"
    group = "issues_aggregator"
    consumer = "aggregator_1"
    # Create group if it doesn't exist
    try:
        await redis.xgroup_create(stream, group, id="$", mkstream=True)
        LOG.info("group created stream=%s group=%s", stream, group)
    except Exception as exc:
        LOG.info("group exists stream=%s group=%s info=%s", stream, group, exc)

    inactivity = float(settings.ISSUE_INACTIVITY_SEC)

    LOG.info("starting issues aggregator stream=%s group=%s consumer=%s", stream, group, consumer)
    while True:
        # read new messages
        try:
            response = await redis.xreadgroup(group, consumer, {stream: ">"}, count=100, block=1000)
        except Exception as exc:
            LOG.info("xreadgroup failed stream=%s group=%s consumer=%s err=%s", stream, group, consumer, exc)
            await asyncio.sleep(1)
            continue
        now = time.time()
        if response:
            processed = 0
            ack_ids: List[str] = []
            for _, messages in response:
                for msg_id, data in messages:
                    processed_ok = True
                    try:
                        processed += 1
                        source = data.get("source")
                        raw = data.get("line") or ""
                        os_name = _os_from_source(source)

                        normalized = _normalize_json_for_clustering(source, raw)
                        if normalized:
                            templated, parsed = normalized
                        else:
                            templated, parsed = _parse_and_template(os_name, raw)

                        # Online assign/create cluster for this templated log
                        try:
                            cluster_id = assign_or_create_cluster(os_name, templated)
                        except Exception:
                            cluster_id = ""

                        # Attempt to persist cluster_id onto the log doc metadata in logs_<os>
                        try:
                            coll_name = f"{settings.CHROMA_LOG_COLLECTION_PREFIX}{os_name}"
                            collection = _get_provider().get_or_create_collection(coll_name)
                            current = collection.get(ids=[msg_id], include=["metadatas"]) or {}
                            metas = (current.get("metadatas") or [[]])[0] or {}
                            metas["cluster_id"] = cluster_id
                            collection.update(ids=[msg_id], metadatas=[metas])
                        except Exception:
                            pass

                        key = _issue_key(os_name, parsed)
                        issue = _issues.get(key)
                        if issue is None:
                            issue = Issue(os=os_name, key=key, created_at=now, last_seen_at=now)
                            _issues[key] = issue
                        issue.add_log(raw=raw, templated=templated, parsed=parsed)

                        # Track per-cluster size and publish cluster candidate at threshold (and occasionally thereafter).
                        try:
                            if cluster_id:
                                counter_key = f"cluster:count:{os_name}:{cluster_id}"
                                new_count = await redis.incr(counter_key)
                                min_count = int(settings.CLUSTER_MIN_LOGS_FOR_CLASSIFICATION)
                                should_publish = new_count == min_count

                                repub_every = int(getattr(settings, "CLUSTER_CANDIDATE_REPUBLISH_EVERY", 0) or 0)
                                min_interval = float(getattr(settings, "CLUSTER_CANDIDATE_REPUBLISH_MIN_INTERVAL_SEC", 0) or 0)
                                if (not should_publish) and repub_every > 0 and new_count > min_count and (new_count % repub_every == 0):
                                    last_key = f"cluster:last_candidate_ts:{os_name}:{cluster_id}"
                                    try:
                                        last_ts = float(await redis.get(last_key) or 0.0)
                                    except Exception:
                                        last_ts = 0.0
                                    if (now - last_ts) >= min_interval:
                                        should_publish = True
                                        try:
                                            await redis.setex(last_key, 60 * 60, str(now))
                                        except Exception:
                                            pass

                                if should_publish:
                                    env_val = parsed.get("env_id") if isinstance(parsed, dict) else None
                                    sample_logs = [{
                                        "raw": raw,
                                        "templated": templated,
                                        "os": os_name,
                                        "source": source,
                                        "env_id": env_val,
                                    }]
                                    await redis.xadd(settings.CLUSTERS_CANDIDATES_STREAM, {
                                        "os": os_name,
                                        "cluster_id": cluster_id,
                                        "env_ids": json.dumps([env_val] if env_val else []),
                                        "sample_logs": json.dumps(sample_logs),
                                    })
                        except Exception:
                            pass
                    except Exception as exc:
                        processed_ok = False
                        LOG.info("issues aggregator failed message id=%s err=%s", msg_id, exc)
                    finally:
                        if processed_ok:
                            ack_ids.append(msg_id)

            # Ack for this consumer group so the PEL doesn't grow unbounded.
            if ack_ids:
                try:
                    for i in range(0, len(ack_ids), 500):
                        await redis.xack(stream, group, *ack_ids[i:i + 500])
                except Exception:
                    pass
            LOG.debug("aggregated messages=%d open_issues=%d", processed, len(_issues))
        # periodically close idle issues
        to_close: List[str] = []
        for key, issue in _issues.items():
            if now - issue.last_seen_at >= inactivity:
                await _close_and_publish(issue)
                to_close.append(key)
        for key in to_close:
            _issues.pop(key, None)


def attach_issues_aggregator(app):
    async def _run_forever():
        backoff = 1.0
        while True:
            try:
                await run_issues_aggregator()
            except Exception as exc:
                LOG.info("issues aggregator crashed err=%s; restarting in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)

    @app.on_event("startup")
    async def startup_event():
        LOG.info("starting issues aggregator in dedicated thread")
        loop = asyncio.new_event_loop()

        def _runner():
            asyncio.set_event_loop(loop)
            loop.create_task(_run_forever())
            loop.run_forever()

        thread = threading.Thread(target=_runner, name="issues-aggregator-thread", daemon=True)
        thread.start()
        app.state.issues_loop = loop
        app.state.issues_thread = thread

    @app.on_event("shutdown")
    async def shutdown_event():
        LOG.info("stopping issues aggregator thread")
        loop = getattr(app.state, "issues_loop", None)
        thread = getattr(app.state, "issues_thread", None)
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5)


