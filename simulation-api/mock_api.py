from __future__ import annotations

import asyncio
import base64
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import json
import os
from pathlib import Path
from fastapi import FastAPI, Header, Request, Response, Body
from pydantic import BaseModel
from faker import Faker
import httpx


# -----------------------------
# Configuration knobs
# -----------------------------
SIM_HOURS_PER_TICK: float = 24.0
DAMAGE_MULTIPLIER: float = 5000.0
SEED_RANDOM: Optional[int] = 42
SIM_USE_LLM: bool = True
GLOBAL_BURST_COOLDOWN_TICKS: int = 5
GLOBAL_BURST_PROBABILITY: float = 0.03

# Supported environments (the app should think these are real)
ENVIRONMENTS: List[Dict[str, Any]] = [
    {"env_id": "env-001", "name": "US East Prod", "region": "us-east-1", "coordinates": {"lat": 37.77, "lng": -77.42}},
    {"env_id": "env-002", "name": "US West Staging", "region": "us-west-2", "coordinates": {"lat": 37.77, "lng": -122.42}},
    {"env_id": "env-003", "name": "EU Prod", "region": "eu-central-1", "coordinates": {"lat": 50.11, "lng": 8.68}},
]
DEFAULT_ENV_ID: str = ENVIRONMENTS[0]["env_id"]

# -----------------------------
# App and globals
# -----------------------------
fake = Faker()
if SEED_RANDOM is not None:
    random.seed(SEED_RANDOM)

app = FastAPI(title="Stateful Simulation API")


Status = str  # "OPERATIONAL" | "DEGRADED" | "FAILED"

IssueId = str


@dataclass
class SimComponent:
    comp_id: str
    name: str
    component_type: str  # e.g., cpu, memory, disk, nic, motherboard, psu, fan, sensor
    beta: float = 2.0
    eta: float = 2000.0
    health: float = 100.0
    age_hours: float = 0.0
    status: Status = "OPERATIONAL"
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulatedCI:
    ci_id: str
    name: str
    ci_type: str  # e.g., server, db, app, thousandeyes_test
    beta: float = 2.0
    eta: float = 2000.0  # hours characteristic life
    health: float = 100.0
    age_hours: float = 0.0
    status: Status = "OPERATIONAL"
    depends_on: List[str] = field(default_factory=list)
    ip_address: Optional[str] = None
    open_ports: List[int] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    components: List[SimComponent] = field(default_factory=list)

@dataclass
class SimIssue:
    """A simulated cross-host issue to drive correlation."""
    issue_id: IssueId
    key: str  # stable-ish human key, e.g. "db_pool_exhaustion"
    title: str
    signature: str  # internal signature (MUST NOT be emitted into logs)
    severity: str  # "warning" | "critical"
    created_at: str
    affected_ci_ids: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)


# CMDB: id -> CI (across all envs, prefixed with env_id::ci_id)
CMDB: Dict[str, SimulatedCI] = {}

# Issues: id -> Issue (issue_id unique; meta carries env_id)
ISSUES: Dict[IssueId, SimIssue] = {}

# Communication pairs for NetFlow generation: (env_id, src_ci_id, dst_ci_id, dst_port)
COMMUNICATIONS: List[Tuple[str, str, str, int]] = []

# Flow cache (array response) with env_id on each record
FLOWS: List[Dict[str, Any]] = []
FLOWS_MAX: int = 300
_GLOBAL_BURST_TICK: int = 0

# Simulation controls
_PAUSED: bool = False
_SPEC_PATH_ENV: str = "SIM_SPEC_PATH"
_OLLAMA_URL: Optional[str] = None
_OLLAMA_MODEL: str = "llama3.2:3b"


# -----------------------------
# Utilities
# -----------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rand_ip(env_idx: int = 0) -> str:
    # Give each env a distinct /16 to avoid collisions
    return f"10.{env_idx + 10}.{random.randint(0, 254)}.{random.randint(1, 254)}"


def _worst_status(statuses: List[Status]) -> Status:
    if any(s == "FAILED" for s in statuses):
        return "FAILED"
    if any(s == "DEGRADED" for s in statuses):
        return "DEGRADED"
    return "OPERATIONAL"


def _status_from_health(health: float) -> Status:
    if health <= 0:
        return "FAILED"
    if health <= 50:
        return "DEGRADED"
    return "OPERATIONAL"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _load_spec() -> Dict[str, Any]:
    """Load spec JSON from path.
    Priority:
      1) SIM_SPEC_PATH env (absolute or relative)
      2) ./spec.json in this directory
      3) ./spec.example.json in this directory
    """
    here = Path(__file__).resolve().parent
    candidates: List[Path] = []
    env_path = os.environ.get(_SPEC_PATH_ENV)
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            p = (here / p).resolve()
        candidates.append(p)
    candidates.append((here / "spec.json").resolve())
    candidates.append((here / "spec.example.json").resolve())
    for cand in candidates:
        try:
            if cand.exists():
                with cand.open("r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            continue
    return {"cis": [], "communications": [], "sim": {}}


def _load_parent_env_var(name: str) -> Optional[str]:
    """Load a variable from parent project's .env if present."""
    try:
        here = Path(__file__).resolve().parent
        env_path = (here.parent / ".env")
        if not env_path.exists():
            return None
        val: Optional[str] = None
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == name:
                    val = v
                    break
        return val
    except Exception:
        return None


def _env_prefix(env_id: str) -> str:
    return f"{env_id}::"


def _strip_prefix(ci_id: str) -> str:
    if "::" in ci_id:
        return ci_id.split("::", 1)[1]
    return ci_id


def _build_env_from_spec(env: Dict[str, Any], loaded: Dict[str, Any], env_idx: int) -> None:
    env_id = env.get("env_id") or DEFAULT_ENV_ID
    prefix = _env_prefix(env_id)

    # Build CIs for this environment (prefixed IDs, metadata carries env_id)
    for obj in loaded.get("cis", []):
        base_ci_id = str(obj.get("ci_id"))
        ci_id = prefix + base_ci_id
        base_name = str(obj.get("name") or base_ci_id)
        ci = SimulatedCI(
            ci_id=ci_id,
            name=f"{env_id}-{base_name}",
            ci_type=str(obj.get("ci_type")),
            beta=float(obj.get("beta", 2.0)),
            eta=float(obj.get("eta", 2000.0)),
            health=float(obj.get("health", 100.0)),
            age_hours=float(obj.get("age_hours", 0.0)),
            status=str(obj.get("status", "OPERATIONAL")),
            depends_on=[prefix + str(x) for x in (obj.get("depends_on") or [])],
            ip_address=str(obj.get("ip_address")) if obj.get("ip_address") else _rand_ip(env_idx),
            open_ports=[int(p) for p in (obj.get("open_ports") or [])],
            meta={**dict(obj.get("meta") or {}), "env_id": env_id, "env_name": env.get("name"), "region": env.get("region")},
        )
        comps: List[SimComponent] = []
        for c in obj.get("components", []) or []:
            comps.append(
                SimComponent(
                    comp_id=prefix + str(c.get("comp_id") or f"{base_ci_id}:{c.get('component_type','comp')}"),
                    name=str(c.get("name") or str(c.get("component_type") or "component")),
                    component_type=str(c.get("component_type") or "component"),
                    beta=float(c.get("beta", 2.0)),
                    eta=float(c.get("eta", 2000.0)),
                    health=float(c.get("health", 100.0)),
                    age_hours=float(c.get("age_hours", 0.0)),
                    status=str(c.get("status", "OPERATIONAL")),
                    meta={**dict(c.get("meta") or {}), "env_id": env_id},
                )
            )
        ci.components = comps
        CMDB[ci.ci_id] = ci

    # Communications for this env
    for comm in loaded.get("communications", []):
        src = str(comm.get("src"))
        dst = str(comm.get("dst"))
        dport = int(comm.get("dst_port", 0))
        if src and dst and dport:
            COMMUNICATIONS.append((env_id, prefix + src, prefix + dst, dport))


def _env_cis(env_id: str) -> List[SimulatedCI]:
    return [ci for ci in CMDB.values() if ci.meta.get("env_id") == env_id]


def _env_comm(env_id: str) -> List[Tuple[str, str, str, int]]:
    return [c for c in COMMUNICATIONS if c[0] == env_id]


def _trigger_global_burst(tick: int) -> None:
    """Occasionally trigger the same symptom family across multiple envs."""
    global _GLOBAL_BURST_TICK
    if tick - _GLOBAL_BURST_TICK < GLOBAL_BURST_COOLDOWN_TICKS:
        return
    if random.random() > GLOBAL_BURST_PROBABILITY:
        return
    _GLOBAL_BURST_TICK = tick
    if len(ENVIRONMENTS) < 2:
        return
    chosen_envs = random.sample([e["env_id"] for e in ENVIRONMENTS], k=min(2, len(ENVIRONMENTS)))
    burst_key = random.choice(["db_pool_exhaustion", "tls_handshake_timeout", "dns_servfail", "nic_flap", "disk_io_error"])
    for env_id in chosen_envs:
        req = InjectIssueRequest(env_id=env_id, key=burst_key, severity="critical", affected_count=3, propagate_to_dependents=True)
        # fire and forget
        import anyio
        anyio.from_thread.run(_run_inject_sync, req)


def _run_inject_sync(req: InjectIssueRequest) -> None:
    """Helper to call async injector from sync context."""
    import anyio
    anyio.run(sim_inject_issue, req)


def initialize_cmdb(spec: Optional[Dict[str, Any]] = None) -> None:
    CMDB.clear()
    COMMUNICATIONS.clear()
    FLOWS.clear()
    ISSUES.clear()

    loaded = spec if spec is not None else _load_spec()

    # Apply simulation settings
    global SIM_HOURS_PER_TICK, DAMAGE_MULTIPLIER, SEED_RANDOM, FLOWS_MAX, SIM_USE_LLM
    sim_cfg: Dict[str, Any] = loaded.get("sim", {}) or {}
    if "hours_per_tick" in sim_cfg:
        SIM_HOURS_PER_TICK = float(sim_cfg.get("hours_per_tick"))
    if "damage_multiplier" in sim_cfg:
        DAMAGE_MULTIPLIER = float(sim_cfg.get("damage_multiplier"))
    if "seed" in sim_cfg:
        SEED_RANDOM = int(sim_cfg.get("seed"))
        random.seed(SEED_RANDOM)
    if "flows_max" in sim_cfg:
        FLOWS_MAX = int(sim_cfg.get("flows_max"))
    if "use_llm" in sim_cfg:
        SIM_USE_LLM = bool(sim_cfg.get("use_llm"))

    # Build each environment from the same base spec (acts like multiple sites)
    for idx, env in enumerate(ENVIRONMENTS):
        _build_env_from_spec(env, loaded, env_idx=idx)


async def _simulation_tick() -> None:
    global FLOWS
    tick = 0
    while True:
        try:
            if not _PAUSED:
                tick += 1
                _trigger_global_burst(tick)
                # Advance time and degrade health (Weibull-like)
                for ci in CMDB.values():
                    # Advance CI age and compute CI-level damage
                    ci.age_hours += SIM_HOURS_PER_TICK
                    base_damage = DAMAGE_MULTIPLIER * ((ci.age_hours / max(ci.eta, 1.0)) ** ci.beta)
                    base_health = _clamp(100.0 - base_damage, 0.0, 100.0)

                    # Components aging and damage
                    comp_min_health = 100.0
                    comp_statuses: List[Status] = []
                    for comp in ci.components:
                        comp.age_hours += SIM_HOURS_PER_TICK
                        c_damage = DAMAGE_MULTIPLIER * ((comp.age_hours / max(comp.eta, 1.0)) ** comp.beta)
                        comp.health = _clamp(100.0 - c_damage, 0.0, 100.0)
                        comp.status = _status_from_health(comp.health)
                        comp_min_health = min(comp_min_health, comp.health)
                        comp_statuses.append(comp.status)

                    # Combine CI health with components (worst-of)
                    if ci.components:
                        ci.health = min(base_health, comp_min_health)
                        ci.status = _worst_status([_status_from_health(ci.health)] + comp_statuses)
                    else:
                        ci.health = base_health
                        ci.status = _status_from_health(ci.health)

                    # If an injected issue is active on this CI, re-apply impact to keep it sticky
                    active_issue_id = str(ci.meta.get("active_issue_id") or "")
                    if active_issue_id and active_issue_id in ISSUES:
                        issue = ISSUES[active_issue_id]
                        sev = str(issue.severity or "").lower()
                        target_status = "FAILED" if sev == "critical" else "DEGRADED"
                        forced_health = 0.0 if target_status == "FAILED" else 40.0
                        comp_type = str(ci.meta.get("active_issue_component_type") or "").strip()
                        if comp_type:
                            comp = next((c for c in ci.components if c.component_type.lower() == comp_type.lower()), None)
                            if comp is not None:
                                comp.health = min(comp.health, forced_health)
                                comp.status = target_status
                        ci.health = min(ci.health, forced_health)
                        ci.status = _worst_status([ci.status, target_status])

                # Propagate dependency status (worst-of children)
                for ci in CMDB.values():
                    if ci.depends_on:
                        deps = [CMDB[d].status for d in ci.depends_on if d in CMDB]
                        if deps:
                            ci.status = _worst_status([ci.status] + deps)

                # Generate NetFlow records based on communications (per env)
                flows_now: List[Dict[str, Any]] = []
                for env_id, src_id, dst_id, dport in COMMUNICATIONS:
                    src = CMDB.get(src_id)
                    dst = CMDB.get(dst_id)
                    if not src or not dst:
                        continue
                    # Consider NIC component state if present
                    def nic_state(ci: SimulatedCI) -> Status:
                        nics = [c for c in ci.components if c.component_type.lower() in {"nic", "network", "ethernet"}]
                        if not nics:
                            return ci.status
                        return _worst_status([x.status for x in nics])

                    s_state = nic_state(src)
                    d_state = nic_state(dst)
                    if "FAILED" in {s_state, d_state}:
                        continue
                    # Bytes scale by status
                    if "DEGRADED" in {s_state, d_state}:
                        bytes_count = random.randint(10_000, 80_000)
                    else:
                        bytes_count = random.randint(400_000, 2_000_000)
                    flows_now.append(
                        {
                            "env_id": env_id,
                            "timestamp": _now_iso(),
                            "src_ip": src.ip_address or _rand_ip(),
                            "dst_ip": dst.ip_address or _rand_ip(),
                            "src_port": random.randint(1024, 65535),
                            "dst_port": dport,
                            "protocol": "tcp",
                            "bytes": bytes_count,
                        }
                    )
                if flows_now:
                    FLOWS.extend(flows_now)
                    if len(FLOWS) > FLOWS_MAX:
                        FLOWS = FLOWS[-FLOWS_MAX:]
        except Exception:
            # Keep ticking even if one iteration fails
            pass
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def _startup() -> None:
    # Determine Ollama URL: env overrides, else parent .env, else default
    global _OLLAMA_URL
    _OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL") or _load_parent_env_var("OLLAMA_BASE_URL") or "http://localhost:11434"
    initialize_cmdb()
    asyncio.create_task(_simulation_tick())


# -----------------------------
# Runtime controls
# -----------------------------
@app.post("/api/v1/sim/pause")
async def sim_pause() -> Dict[str, Any]:
    global _PAUSED
    _PAUSED = True
    return {"ok": True, "paused": _PAUSED}


@app.post("/api/v1/sim/resume")
async def sim_resume() -> Dict[str, Any]:
    global _PAUSED
    _PAUSED = False
    return {"ok": True, "paused": _PAUSED}


@app.post("/api/v1/sim/reset")
async def sim_reset() -> Dict[str, Any]:
    initialize_cmdb()
    return {"ok": True}

class InjectIssueRequest(BaseModel):
    env_id: str = DEFAULT_ENV_ID
    key: str = "synthetic_issue"
    title: str | None = None
    severity: str = "critical"  # "warning" | "critical"
    # Impact selection
    affected_ci_ids: List[str] | None = None
    affected_count: int = 3
    # Optional: force specific component type to fail on each impacted CI
    component_type: str | None = "nic"
    # If true, also degrade direct dependents of affected CIs
    propagate_to_dependents: bool = True


def _pick_affected_ci_ids(req: InjectIssueRequest) -> List[str]:
    env_prefix = _env_prefix(req.env_id)
    if req.affected_ci_ids:
        return [env_prefix + x if not x.startswith(env_prefix) else x for x in req.affected_ci_ids if (env_prefix + x if not x.startswith(env_prefix) else x) in CMDB]
    # Prefer servers/dbs/apps for more realistic “server correlation”
    candidates = [ci.ci_id for ci in CMDB.values() if ci.meta.get("env_id") == req.env_id and ci.ci_type in {"server", "db", "app"}]
    if not candidates:
        candidates = [ci.ci_id for ci in CMDB.values() if ci.meta.get("env_id") == req.env_id]
    k = max(1, min(int(req.affected_count or 1), len(candidates)))
    return random.sample(candidates, k=k)


def _mark_ci_impacted(ci: SimulatedCI, issue_id: IssueId, *, component_type: str | None, severity: str) -> None:
    # Attach issue marker so log generation can reference it
    ci.meta["active_issue_id"] = issue_id
    ci.meta["active_issue_severity"] = severity
    if component_type:
        ci.meta["active_issue_component_type"] = component_type
    # Force a component failure/degradation (drives status + makes logs more realistic)
    target_status = "FAILED" if str(severity).lower() == "critical" else "DEGRADED"
    if component_type:
        comp = next((c for c in ci.components if c.component_type.lower() == component_type.lower()), None)
        if comp is None:
            # create the component if missing
            comp = SimComponent(
                comp_id=f"{ci.ci_id}:{component_type}",
                name=component_type,
                component_type=component_type,
                beta=2.0,
                eta=2000.0,
                health=100.0,
                age_hours=ci.age_hours,
                status="OPERATIONAL",
                meta={},
            )
            ci.components.append(comp)
        comp.health = 0.0 if target_status == "FAILED" else min(comp.health, 40.0)
        comp.status = target_status
    # CI health should reflect the impact
    ci.health = 0.0 if target_status == "FAILED" else min(ci.health, 40.0)
    ci.status = target_status


@app.post("/api/v1/sim/issues/inject")
async def sim_inject_issue(req: InjectIssueRequest) -> Dict[str, Any]:
    """Inject a cross-host issue so generated logs correlate via shared signature."""
    issue_id: IssueId = f"ISSUE-{uuid.uuid4().hex[:8]}"
    title = (req.title or f"{req.key.replace('_', ' ').title()} detected").strip()
    severity = (req.severity or "critical").lower()
    affected = _pick_affected_ci_ids(req)

    issue = SimIssue(
        issue_id=issue_id,
        key=req.key,
        title=title,
        # IMPORTANT: signature is kept INTERNAL for debugging/ground-truth only.
        # It must never be embedded into emitted logs, otherwise correlation becomes trivial.
        signature=f"internal::{req.key}::{issue_id}",
        severity=severity,
        created_at=_now_iso(),
        affected_ci_ids=affected[:],
        meta={**_issue_params_for_key(req.key), "env_id": req.env_id},
    )
    ISSUES[issue_id] = issue

    # Mark impacted
    for cid in affected:
        ci = CMDB.get(cid)
        if not ci:
            continue
        _mark_ci_impacted(ci, issue_id, component_type=req.component_type, severity=severity)

    # Optionally propagate to dependents (keeps “blast radius” realistic)
    if req.propagate_to_dependents:
        dependents = [ci for ci in CMDB.values() if any(d in set(affected) for d in ci.depends_on)]
        for ci in dependents:
            # Don’t override if already explicitly impacted
            if ci.meta.get("active_issue_id"):
                continue
            _mark_ci_impacted(ci, issue_id, component_type=None, severity="warning")
            issue.affected_ci_ids.append(ci.ci_id)

    return {"ok": True, "issue": issue.__dict__}


@app.post("/env/{env_id}/api/v1/sim/issues/inject")
async def sim_inject_issue_env(env_id: str, req: InjectIssueRequest) -> Dict[str, Any]:
    req.env_id = env_id
    return await sim_inject_issue(req)


@app.post("/api/v1/sim/issues/clear")
async def sim_clear_issues() -> Dict[str, Any]:
    """Clear injected issues and remove CI markers (does not reset CMDB spec)."""
    ISSUES.clear()
    for ci in CMDB.values():
        ci.meta.pop("active_issue_id", None)
        ci.meta.pop("active_issue_severity", None)
        ci.meta.pop("active_issue_component_type", None)
    return {"ok": True}


@app.post("/env/{env_id}/api/v1/sim/issues/clear")
async def sim_clear_issues_env(env_id: str) -> Dict[str, Any]:
    # Clear only issues for env
    to_clear = [iid for iid, iss_ in ISSUES.items() if iss_.meta.get("env_id") == env_id]
    for iid in to_clear:
        ISSUES.pop(iid, None)
    for ci in _env_cis(env_id):
        for c in ci.components:
            c.meta.pop("active_issue_id", None)
            c.meta.pop("active_issue_severity", None)
            c.status = _status_from_health(c.health)
        ci.meta.pop("active_issue_id", None)
        ci.meta.pop("active_issue_severity", None)
        ci.status = _status_from_health(ci.health)
    return {"ok": True}


@app.get("/api/v1/sim/issues")
async def sim_list_issues() -> Dict[str, Any]:
    return {"items": [iss_.__dict__ for iss_ in ISSUES.values()], "now": _now_iso()}


@app.get("/env/{env_id}/api/v1/sim/issues")
async def sim_list_issues_env(env_id: str) -> Dict[str, Any]:
    return {"items": [iss_.__dict__ for iss_ in ISSUES.values() if iss_.meta.get("env_id") == env_id], "now": _now_iso()}


@app.post("/api/v1/sim/issues/inject_global")
async def sim_inject_global_issue() -> Dict[str, Any]:
    """Force a cross-environment burst for testing."""
    _trigger_global_burst(random.randint(1, 1_000_000))
    return {"ok": True, "now": _now_iso()}


@app.post("/api/v1/sim/speed")
async def sim_speed(hours_per_tick: Optional[float] = None, damage_multiplier: Optional[float] = None) -> Dict[str, Any]:
    global SIM_HOURS_PER_TICK, DAMAGE_MULTIPLIER
    if hours_per_tick is not None:
        SIM_HOURS_PER_TICK = float(hours_per_tick)
    if damage_multiplier is not None:
        DAMAGE_MULTIPLIER = float(damage_multiplier)
    return {"ok": True, "SIM_HOURS_PER_TICK": SIM_HOURS_PER_TICK, "DAMAGE_MULTIPLIER": DAMAGE_MULTIPLIER}


# -----------------------------
# LLM (Ollama) for log generation
# -----------------------------
async def _gen_text(prompt: str) -> str:
    if not SIM_USE_LLM:
        return fake.sentence(nb_words=10)
    base = os.environ.get("OLLAMA_BASE_URL") or _OLLAMA_URL
    if not base:
        return fake.sentence(nb_words=10)
    url = f"{base.rstrip('/')}/api/generate"
    payload = {"model": _OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            text = data.get("response") or ""
            return (text or "").strip()[:300] or fake.sentence(nb_words=10)
    except Exception:
        return fake.sentence(nb_words=10)


# -----------------------------
# LLM (Ollama) JSON-mode helpers
# -----------------------------
async def _ollama_chat_json(messages: List[Dict[str, str]], *, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    base = os.environ.get("OLLAMA_BASE_URL") or _OLLAMA_URL
    if not base:
        return None
    url = f"{base.rstrip('/')}/api/chat"
    payload = {
        "model": _OLLAMA_MODEL,
        "messages": messages,
        "format": "json",
        "options": {"temperature": 0.2, "top_p": 0.95},
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg = (data.get("message") or {}).get("content") or ""
            if not msg:
                return None
            try:
                return json.loads(msg)
            except Exception:
                msg_str = str(msg).strip()
                if msg_str.startswith("```"):
                    msg_str = msg_str.strip("`")
                    parts = msg_str.split("\n", 1)
                    if len(parts) == 2 and parts[0].strip().lower() in {"json", "javascript"}:
                        msg_str = parts[1]
                try:
                    return json.loads(msg_str)
                except Exception:
                    return None
    except Exception:
        return None


def _coerce_float(x: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return _clamp(v, lo, hi)
    except Exception:
        return default


def _coerce_str(x: Any, default: str) -> str:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if not s:
            return default
        if s.lower() in {"none", "null"}:
            return default
        return s
    except Exception:
        return default


async def _gen_title(prefix: str, ci_name: str, status: str) -> str:
    if not SIM_USE_LLM:
        return f"{prefix} {ci_name} {status.lower()}"
    messages = [
        {"role": "system", "content": "Return JSON: {\"title\":\"...\"}. Keep under 6 words; no trailing punctuation."},
        {"role": "user", "content": json.dumps({"prefix": prefix, "name": ci_name, "status": status})},
    ]
    data = await _ollama_chat_json(messages)
    if isinstance(data, dict):
        t = _coerce_str(data.get("title"), "")
        if t:
            return t
    return f"{prefix} {ci_name} {status.lower()}"


async def _gen_message(channel: str, ci_name: str, detail: str) -> str:
    if not SIM_USE_LLM:
        return f"{ci_name} {detail}"
    messages = [
        {"role": "system", "content": "Return JSON: {\"message\":\"...\"}. 10-18 words, realistic ops log phrasing."},
        {"role": "user", "content": json.dumps({"channel": channel, "host": ci_name, "detail": detail})},
    ]
    data = await _ollama_chat_json(messages)
    if isinstance(data, dict):
        t = _coerce_str(data.get("message"), "")
        if t:
            return t
    return f"{ci_name} {detail}"


def _issue_params_for_key(key: str) -> Dict[str, Any]:
    """Generate stable-ish parameters for symptom families (no unique IDs)."""
    k = (key or "").strip().lower()
    if k == "db_pool_exhaustion":
        return {
            "pool_name": random.choice(["HikariPool-1", "HikariPool-2", "db-pool"]),
            "timeout_ms": random.choice([10000, 20000, 30000]),
            "sqlstate": random.choice(["08001", "08006", "53300"]),
        }
    if k == "tls_handshake_timeout":
        return {"timeout_s": random.choice([5, 8, 10]), "peer": random.choice(["api-gateway", "auth-service", "payments"])}
    if k == "dns_servfail":
        return {"domain": random.choice(["internal.service.local", "corp.example", "svc.cluster.local"])}
    if k == "nic_flap":
        return {"iface": random.choice(["eth0", "ens192", "enp0s3"]), "driver": random.choice(["ixgbe", "e1000e", "vmxnet3"])}
    if k == "disk_io_error":
        return {"device": random.choice(["sda", "nvme0n1", "xvda"]), "errno": random.choice([5, 110, 28])}
    if k == "oom_killer":
        return {"process": random.choice(["java", "python", "node", "nginx"]), "cgroup": random.choice(["system.slice", "kubepods.slice"])}
    # Default: generic latency/timeouts
    return {"timeout_ms": random.choice([5000, 10000, 15000]), "service": random.choice(["api", "worker", "scheduler"])}


def _symptom_detail(issue: SimIssue, ci: SimulatedCI, comp: Optional[SimComponent], *, target_status: str) -> str:
    """Return a symptom string for logs (no issue IDs, no scenario keys)."""
    k = (issue.key or "").strip().lower()
    p = issue.meta or {}

    if k == "db_pool_exhaustion":
        pool = str(p.get("pool_name") or "HikariPool-1")
        tms = int(p.get("timeout_ms") or 30000)
        sqlstate = str(p.get("sqlstate") or "08001")
        return f"{pool} - Connection is not available, request timed out after {tms}ms (SQLSTATE {sqlstate})"

    if k == "tls_handshake_timeout":
        timeout_s = int(p.get("timeout_s") or 10)
        peer = str(p.get("peer") or "upstream")
        return f"TLS handshake timeout after {timeout_s}s while connecting to {peer}"

    if k == "dns_servfail":
        domain = str(p.get("domain") or "svc.cluster.local")
        return f"dns: lookup api.{domain} on 10.0.0.2:53: server misbehaving (SERVFAIL)"

    if k == "nic_flap":
        iface = str(p.get("iface") or "eth0")
        driver = str(p.get("driver") or "ixgbe")
        if target_status == "FAILED":
            return f"NETDEV WATCHDOG: {iface} ({driver}): transmit queue 0 timed out; resetting adapter"
        return f"{iface}: Link is Down; carrier lost; attempting renegotiation"

    if k == "disk_io_error":
        dev = str(p.get("device") or "sda")
        errno = int(p.get("errno") or 5)
        return f"blk_update_request: I/O error, dev {dev}, sector {random.randint(1000, 900000)} (errno={errno})"

    if k == "oom_killer":
        proc = str(p.get("process") or "java")
        cgroup = str(p.get("cgroup") or "system.slice")
        return f"Out of memory: Killed process {proc} (pid {random.randint(1000, 65000)}) in {cgroup}; memory cgroup out of memory"

    timeout_ms = int(p.get("timeout_ms") or 10000)
    svc = str(p.get("service") or "api")
    return f"upstream request to {svc} timed out after {timeout_ms}ms; retry budget exceeded"


async def _gen_te_alert(ci_name: str, target_status: str) -> Dict[str, Any]:
    if not SIM_USE_LLM:
        severity = "warning" if target_status == "DEGRADED" else "critical"
        return {
            "ruleName": "Performance threshold exceeded",
            "testName": ci_name,
            "severity": severity,
            "summary": f"Target status {target_status}",
            "startTime": _now_iso(),
        }
    messages = [
        {
            "role": "system",
            "content": (
                "Return ONLY a JSON object with keys: ruleName, testName, severity, summary, startTime. "
                "severity must be one of: warning, critical. startTime must be ISO8601."
            ),
        },
        {"role": "user", "content": json.dumps({"testName": ci_name, "status": target_status})},
    ]
    data = await _ollama_chat_json(messages)
    severity = "warning" if target_status == "DEGRADED" else "critical"
    if not isinstance(data, dict):
        return {
            "ruleName": "Performance threshold exceeded",
            "testName": ci_name,
            "severity": severity,
            "summary": f"Target status {target_status}",
            "startTime": _now_iso(),
        }
    if any(
        str(data.get(k) or "").strip().lower() in {"", "none", "null"}
        for k in ("ruleName", "testName", "severity", "summary", "startTime")
    ):
        return {
            "ruleName": "Performance threshold exceeded",
            "testName": ci_name,
            "severity": severity,
            "summary": f"Target status {target_status}",
            "startTime": _now_iso(),
        }
    return {
        "ruleName": _coerce_str(data.get("ruleName"), "Performance threshold exceeded"),
        "testName": _coerce_str(data.get("testName"), ci_name),
        "severity": _coerce_str(data.get("severity"), severity),
        "summary": _coerce_str(data.get("summary"), f"Target status {target_status}"),
        "startTime": _coerce_str(data.get("startTime"), _now_iso()),
    }


async def _gen_te_test(ci_name: str, target_status: str) -> Dict[str, Any]:
    if not SIM_USE_LLM:
        if target_status == "OPERATIONAL":
            latency, loss, availability = random.uniform(30, 80), random.uniform(0.0, 0.3), random.uniform(99.0, 100.0)
        elif target_status == "DEGRADED":
            latency, loss, availability = random.uniform(150, 400), random.uniform(1.0, 5.0), random.uniform(92.0, 97.0)
        else:
            latency, loss, availability = random.uniform(400, 1000), random.uniform(10.0, 30.0), random.uniform(0.0, 60.0)
        return {
            "testId": abs(hash(ci_name)) % 100000,
            "testName": ci_name,
            "type": "http-server",
            "metrics": {
                "latencyMs": round(latency, 1),
                "loss": round(loss, 2),
                "availability": round(availability, 1),
            },
        }
    fewshot = {
        "testId": 10123,
        "testName": "HTTP Test - cart",
        "type": "http-server",
        "metrics": {"latencyMs": 245.7, "loss": 1.8, "availability": 94.2},
    }
    messages = [
        {"role": "system", "content": "You generate ThousandEyes test JSON objects. Keep metrics realistic and within bounds."},
        {"role": "user", "content": json.dumps({"example": fewshot})},
        {"role": "user", "content": json.dumps({"request": {"testName": ci_name, "status": target_status}})},
    ]
    data = await _ollama_chat_json(messages)
    if not isinstance(data, dict):
        return await _gen_te_test(ci_name, target_status="OPERATIONAL")
    metrics = data.get("metrics") or {}
    if target_status == "OPERATIONAL":
        lat_lo, lat_hi = 20.0, 120.0
        loss_lo, loss_hi = 0.0, 0.5
        avail_lo, avail_hi = 98.0, 100.0
    elif target_status == "DEGRADED":
        lat_lo, lat_hi = 120.0, 500.0
        loss_lo, loss_hi = 0.5, 6.0
        avail_lo, avail_hi = 90.0, 98.0
    else:
        lat_lo, lat_hi = 300.0, 1500.0
        loss_lo, loss_hi = 5.0, 50.0
        avail_lo, avail_hi = 0.0, 80.0
    return {
        "testId": int(data.get("testId") or abs(hash(ci_name)) % 100000),
        "testName": _coerce_str(data.get("testName"), ci_name),
        "type": _coerce_str(data.get("type"), "http-server"),
        "metrics": {
            "latencyMs": round(_coerce_float(metrics.get("latencyMs"), lat_lo, lat_hi, lat_hi - 1), 1),
            "loss": round(_coerce_float(metrics.get("loss"), loss_lo, loss_hi, loss_lo + 0.1), 2),
            "availability": round(_coerce_float(metrics.get("availability"), avail_lo, avail_hi, avail_hi - 1), 1),
        },
    }


# -----------------------------
# SCOM (System Center Operations Manager)
# -----------------------------
@app.post("/OperationsManager/authenticate")
async def scom_authenticate(request: Request):
    # Body is often JSON string of base64-encoded "(Network):DOMAIN\\username:password"
    decoded_user = "(Network):mock\\user:pass"
    try:
        raw = await request.body()
        if not raw:
            return {"status": "ok", "user": decoded_user}
        text = raw.decode("utf-8", errors="ignore").strip()
        # If JSON string, strip quotes
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        # Attempt base64 decode
        try:
            decoded_user = base64.b64decode(text).decode("utf-8", errors="ignore") or decoded_user
        except Exception:
            # If not base64, accept raw
            decoded_user = text or decoded_user
    except Exception:
        pass
    return {"status": "ok", "user": decoded_user}


@app.post("/env/{env_id}/OperationsManager/authenticate")
async def scom_authenticate_env(env_id: str, request: Request):
    return await scom_authenticate(request)


@app.get("/OperationsManager")
async def scom_init_csrf(response: Response):
    response.headers["X-CSRF-Token"] = fake.uuid4()
    return {"ok": True}


async def _scom_alerts(env_id: str) -> Dict[str, Any]:
    items = []
    for ci in _env_cis(env_id):
        if ci.status == "OPERATIONAL":
            continue
        sev = "Warning" if ci.status == "DEGRADED" else "Error"
        title = await _gen_title("Alert", ci.name, ci.status)
        # If an injected issue is active, do NOT include issue identifiers in the alert.
        # Emit only symptoms so correlation must be discovered (e.g., via LogBERT similarity).
        issue_id = str(ci.meta.get("active_issue_id") or "")
        issue = ISSUES.get(issue_id) if issue_id else None
        detail = _symptom_detail(issue, ci, None, target_status=ci.status) if issue else f"status {ci.status}"
        items.append(
            {
                "Id": fake.uuid4(),
                "Name": (title or f"{ci.name} {sev.lower()}"),
                "Severity": sev,
                "Priority": random.choice(["Low", "Medium", "High"]),
                "MonitoringObjectDisplayName": ci.name,
                "LastModified": _now_iso(),
                "Description": detail,
                "ip": ci.ip_address or None,
                "EnvironmentId": env_id,
            }
        )
    # Include a few background infos
    for _ in range(random.randint(1, 3)):
        items.append(
            {
                "Id": fake.uuid4(),
                "Name": fake.sentence(nb_words=3),
                "Severity": "Information",
                "Priority": random.choice(["Low", "Medium"]),
                "MonitoringObjectDisplayName": fake.hostname(),
                "LastModified": _now_iso(),
            }
        )
    return {"items": items}


@app.post("/OperationsManager/data/alert")
async def scom_alerts_default(_: Any = Body(default=None)):
    return await _scom_alerts(DEFAULT_ENV_ID)


@app.post("/env/{env_id}/OperationsManager/data/alert")
async def scom_alerts_env(env_id: str, _: Any = Body(default=None)):
    return await _scom_alerts(env_id)


async def _scom_performance(env_id: str) -> Dict[str, Any]:
    items = []
    for ci in _env_cis(env_id):
        if ci.ci_type not in {"server", "db"}:
            continue
        # Prefer component-specific metrics if present
        cpu_comp = next((c for c in ci.components if c.component_type.lower() == "cpu"), None)
        mem_comp = next((c for c in ci.components if c.component_type.lower() in {"memory", "ram"}), None)
        disk_comp = next((c for c in ci.components if c.component_type.lower() in {"disk", "storage"}), None)

        base_h = ci.health
        cpu_h = cpu_comp.health if cpu_comp else base_h
        mem_h = mem_comp.health if mem_comp else base_h
        disk_h = disk_comp.health if disk_comp else base_h

        cpu = 100.0 - cpu_h + random.uniform(0, 5)
        mem_avail = max(50.0, 16000.0 * (mem_h / 100.0) + random.uniform(-200.0, 200.0))
        disk_read = max(0.0, (100.0 - disk_h) / 1000.0 + random.uniform(0, 0.01))
        items.extend(
            [
                {
                    "ObjectName": "Processor",
                    "CounterName": "Processor Time",
                    "InstanceName": random.choice(["_Total", "0"]),
                    "Value": round(_clamp(cpu, 0.0, 100.0), 2),
                    "ComputerName": ci.name,
                    "Timestamp": _now_iso(),
                    "EnvironmentId": env_id,
                },
                {
                    "ObjectName": "Memory",
                    "CounterName": "Available MBytes",
                    "InstanceName": "",
                    "Value": round(_clamp(mem_avail, 0.0, 64000.0), 2),
                    "ComputerName": ci.name,
                    "Timestamp": _now_iso(),
                    "EnvironmentId": env_id,
                },
                {
                    "ObjectName": "LogicalDisk",
                    "CounterName": "Avg. Disk sec/Read",
                    "InstanceName": random.choice(["C:", "sda"]),
                    "Value": round(disk_read, 4),
                    "ComputerName": ci.name,
                    "Timestamp": _now_iso(),
                    "EnvironmentId": env_id,
                },
            ]
        )
    return {"items": items}


@app.post("/OperationsManager/data/performance")
async def scom_performance_default(_: Any = Body(default=None)):
    return await _scom_performance(DEFAULT_ENV_ID)


@app.post("/env/{env_id}/OperationsManager/data/performance")
async def scom_performance_env(env_id: str, _: Any = Body(default=None)):
    return await _scom_performance(env_id)


async def _scom_events(env_id: str) -> Dict[str, Any]:
    items = []
    for ci in _env_cis(env_id):
        if ci.status == "OPERATIONAL":
            continue
        level = "Warning" if ci.status == "DEGRADED" else "Error"
        issue_id = str(ci.meta.get("active_issue_id") or "")
        issue = ISSUES.get(issue_id) if issue_id else None
        # If any critical component failed, emit component-specific event
        critical_types = {"motherboard", "psu", "fan", "cpu", "memory", "nic"}
        emitted = False
        for comp in ci.components:
            if comp.component_type.lower() in critical_types and comp.status != "OPERATIONAL":
                # IMPORTANT: do NOT embed issue IDs/signatures in the log text.
                # Instead, inject symptom text that is similar across affected hosts.
                base_detail = _symptom_detail(issue, ci, comp, target_status=comp.status) if issue else f"component {comp.component_type} {comp.status}"
                msg = await _gen_message("Hardware", ci.name, base_detail)
                items.append(
                    {
                        "LevelDisplayName": "Error" if comp.status == "FAILED" else "Warning",
                        "ComputerName": ci.name,
                        "Channel": "Hardware",
                        "Message": msg,
                        "TimeGenerated": _now_iso(),
                        "ip": ci.ip_address or None,
                        "EnvironmentId": env_id,
                    }
                )
                emitted = True
        if not emitted:
            base_detail = _symptom_detail(issue, ci, None, target_status=ci.status) if issue else f"status {ci.status}"
            msg = await _gen_message("Application", ci.name, base_detail)
            items.append(
                {
                    "LevelDisplayName": level,
                    "ComputerName": ci.name,
                    "Channel": "Application",
                    "Message": msg,
                    "TimeGenerated": _now_iso(),
                    "ip": ci.ip_address or None,
                    "EnvironmentId": env_id,
                }
            )
    return {"items": items}


@app.post("/OperationsManager/data/event")
async def scom_events_default(_: Any = Body(default=None)):
    return await _scom_events(DEFAULT_ENV_ID)


@app.post("/env/{env_id}/OperationsManager/data/event")
async def scom_events_env(env_id: str, _: Any = Body(default=None)):
    return await _scom_events(env_id)


# -----------------------------
# SquaredUp (API key)
# -----------------------------
def _require_api_key(x_api_key: Optional[str]) -> None:
    # No-op enforcement for demo; accept any provided/empty
    return None


@app.get("/api/health")
async def squaredup_health_default(x_api_key: Optional[str] = Header(None, convert_underscores=False)):
    _require_api_key(x_api_key)
    return await _squaredup_health(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/api/health")
async def squaredup_health_env(env_id: str, x_api_key: Optional[str] = Header(None, convert_underscores=False)):
    _require_api_key(x_api_key)
    return await _squaredup_health(env_id)


async def _squaredup_health(env_id: str) -> Dict[str, Any]:
    items = []
    for ci in _env_cis(env_id):
        if ci.ci_type not in {"app", "server", "db"}:
            continue
        state = "ok" if ci.status == "OPERATIONAL" else ("degraded" if ci.status == "DEGRADED" else "critical")
        items.append({"name": ci.name, "state": state, "updated": _now_iso(), "env_id": env_id})
    return {"items": items}


@app.get("/api/alerts")
async def squaredup_alerts_default(x_api_key: Optional[str] = Header(None, convert_underscores=False)):
    _require_api_key(x_api_key)
    return await _squaredup_alerts(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/api/alerts")
async def squaredup_alerts_env(env_id: str, x_api_key: Optional[str] = Header(None, convert_underscores=False)):
    _require_api_key(x_api_key)
    return await _squaredup_alerts(env_id)


async def _squaredup_alerts(env_id: str) -> Dict[str, Any]:
    severities = {"OPERATIONAL": "info", "DEGRADED": "warning", "FAILED": "critical"}
    items = []
    for ci in _env_cis(env_id):
        if ci.status == "OPERATIONAL":
            continue
        title = await _gen_title("Incident", ci.name, ci.status)
        items.append(
            {
                "id": fake.uuid4(),
                "title": title,
                "severity": severities[ci.status],
                "created": _now_iso(),
                "env_id": env_id,
            }
        )
    return {"items": items}


@app.get("/api/dependencies")
async def squaredup_dependencies_default(x_api_key: Optional[str] = Header(None, convert_underscores=False)):
    _require_api_key(x_api_key)
    return await _squaredup_dependencies(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/api/dependencies")
async def squaredup_dependencies_env(env_id: str, x_api_key: Optional[str] = Header(None, convert_underscores=False)):
    _require_api_key(x_api_key)
    return await _squaredup_dependencies(env_id)


async def _squaredup_dependencies(env_id: str) -> Dict[str, Any]:
    edges = []
    for ci in _env_cis(env_id):
        for dep in ci.depends_on:
            edges.append({"from": dep, "to": ci.ci_id, "env_id": env_id})
    return {"items": edges}


# -----------------------------
# Cisco Catalyst Center (DNAC)
# -----------------------------
@app.post("/dna/system/api/v1/auth/token")
async def catalyst_auth(_: Request):
    return {"Token": fake.uuid4()}


@app.post("/env/{env_id}/dna/system/api/v1/auth/token")
async def catalyst_auth_env(env_id: str, _: Request):
    return {"Token": fake.uuid4()}


def _catalyst_network_health(env_id: str) -> Dict[str, Any]:
    scores = []
    for ci in _env_cis(env_id):
        if ci.ci_type in {"server", "db"}:
            if ci.status == "OPERATIONAL":
                scores.append(random.uniform(90, 99))
            elif ci.status == "DEGRADED":
                scores.append(random.uniform(70, 85))
            else:
                scores.append(random.uniform(20, 40))
    avg = round(sum(scores) / len(scores), 1) if scores else round(random.uniform(60, 98), 1)
    return {"networkHealthAverage": avg, "healthScore": avg, "time": _now_iso(), "env_id": env_id}


@app.get("/dna/intent/api/v1/network-health")
async def catalyst_network_health_default():
    return [_catalyst_network_health(DEFAULT_ENV_ID)]


@app.get("/env/{env_id}/dna/intent/api/v1/network-health")
async def catalyst_network_health_env(env_id: str):
    return [_catalyst_network_health(env_id)]


def _catalyst_client_health(env_id: str) -> List[Dict[str, Any]]:
    # Keep simple demo sites driven by network health bias
    sites = [f"{env_id}-site-{i:02d}" for i in range(1, 4)]
    items = []
    for s in sites:
        base = 85.0
        if any(ci.status != "OPERATIONAL" for ci in _env_cis(env_id) if ci.ci_type in {"server", "db"}):
            base = 72.0
        items.append({"site": s, "healthScore": round(random.uniform(base - 10, base + 10), 1), "env_id": env_id})
    return items


@app.get("/dna/intent/api/v1/client-health")
async def catalyst_client_health_default():
    return _catalyst_client_health(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/dna/intent/api/v1/client-health")
async def catalyst_client_health_env(env_id: str):
    return _catalyst_client_health(env_id)


def _catalyst_device_health(env_id: str) -> List[Dict[str, Any]]:
    devices = []
    for ci in _env_cis(env_id):
        if ci.ci_type not in {"server", "db"}:
            continue
        if ci.status == "OPERATIONAL":
            score = random.uniform(90, 99)
        elif ci.status == "DEGRADED":
            score = random.uniform(65, 85)
        else:
            score = random.uniform(20, 45)
        devices.append({"hostname": ci.name, "managementIpAddr": ci.ip_address or _rand_ip(), "overallHealth": round(score, 1), "env_id": env_id})
    return devices


@app.get("/dna/intent/api/v1/device-health")
async def catalyst_device_health_default():
    return _catalyst_device_health(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/dna/intent/api/v1/device-health")
async def catalyst_device_health_env(env_id: str):
    return _catalyst_device_health(env_id)


@app.get("/dna/intent/api/v1/events")
async def catalyst_events():
    severities = ["info", "minor", "major", "critical"]
    out = []
    for ci in _env_cis(DEFAULT_ENV_ID):
        sev = "info"
        if ci.status == "DEGRADED":
            sev = "major"
        elif ci.status == "FAILED":
            sev = "critical"
        name = await _gen_title("Event", ci.ci_id, ci.status)
        out.append({"name": name, "severity": sev, "device": ci.name, "device_ip": ci.ip_address or _rand_ip(), "time": _now_iso(), "env_id": DEFAULT_ENV_ID})
    return out


@app.get("/env/{env_id}/dna/intent/api/v1/events")
async def catalyst_events_env(env_id: str):
    severities = ["info", "minor", "major", "critical"]
    out = []
    for ci in _env_cis(env_id):
        sev = "info"
        if ci.status == "DEGRADED":
            sev = "major"
        elif ci.status == "FAILED":
            sev = "critical"
        name = await _gen_title("Event", ci.ci_id, ci.status)
        out.append({"name": name, "severity": sev, "device": ci.name, "device_ip": ci.ip_address or _rand_ip(), "time": _now_iso(), "env_id": env_id})
    return out


# -----------------------------
# ThousandEyes (extended mode)
# -----------------------------
def _check_te_auth(authorization: Optional[str], x_te_auth_token: Optional[str]) -> None:
    # Accept any for demo; presence is enough
    return None


@app.get("/v6/alerts.json")
async def te_alerts(window: Optional[str] = None, authorization: Optional[str] = Header(None), x_te_auth_token: Optional[str] = Header(None, convert_underscores=False)):
    _check_te_auth(authorization, x_te_auth_token)
    return await _te_alerts(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/v6/alerts.json")
async def te_alerts_env(env_id: str, window: Optional[str] = None, authorization: Optional[str] = Header(None), x_te_auth_token: Optional[str] = Header(None, convert_underscores=False)):
    _check_te_auth(authorization, x_te_auth_token)
    return await _te_alerts(env_id)


async def _te_alerts(env_id: str) -> Dict[str, Any]:
    alerts: List[Dict[str, Any]] = []
    for ci in _env_cis(env_id):
        if ci.ci_type != "thousandeyes_test":
            continue
        # Look at target status
        target_status = "OPERATIONAL"
        if ci.depends_on:
            deps = [CMDB[d].status for d in ci.depends_on if d in CMDB]
            target_status = _worst_status(deps) if deps else "OPERATIONAL"
        if target_status == "OPERATIONAL":
            continue
        alert = await _gen_te_alert(ci.name, target_status)
        alert["env_id"] = env_id
        alerts.append(alert)
    return {"alerts": alerts}


@app.get("/v6/tests.json")
async def te_tests(authorization: Optional[str] = Header(None), x_te_auth_token: Optional[str] = Header(None, convert_underscores=False)):
    _check_te_auth(authorization, x_te_auth_token)
    return await _te_tests(DEFAULT_ENV_ID)


@app.get("/env/{env_id}/v6/tests.json")
async def te_tests_env(env_id: str, authorization: Optional[str] = Header(None), x_te_auth_token: Optional[str] = Header(None, convert_underscores=False)):
    _check_te_auth(authorization, x_te_auth_token)
    return await _te_tests(env_id)


async def _te_tests(env_id: str) -> Dict[str, Any]:
    tests: List[Dict[str, Any]] = []
    for ci in _env_cis(env_id):
        if ci.ci_type != "thousandeyes_test":
            continue
        # Target app/server
        target_status = "OPERATIONAL"
        if ci.depends_on:
            deps = [CMDB[d].status for d in ci.depends_on if d in CMDB]
            target_status = _worst_status(deps) if deps else "OPERATIONAL"
        test = await _gen_te_test(ci.name, target_status)
        test["env_id"] = env_id
        tests.append(test)
    return {"tests": tests}


# -----------------------------
# NetFlow (demo feed)
# -----------------------------
@app.get("/api/v1/netflow")
async def netflow_feed():
    # Return as array of flow objects for simplicity (all envs)
    return FLOWS[-FLOWS_MAX:]


@app.get("/env/{env_id}/api/v1/netflow")
async def netflow_feed_env(env_id: str):
    return [f for f in FLOWS[-FLOWS_MAX:] if f.get("env_id") == env_id]


@app.get("/api/v1/cmdb/topology")
async def cmdb_topology() -> Dict[str, Any]:
    """Return nodes/edges for all simulated environments."""
    return await cmdb_topology_env(DEFAULT_ENV_ID, include_all=True)


@app.get("/env/{env_id}/api/v1/cmdb/topology")
async def cmdb_topology_env(env_id: str, include_all: bool = False) -> Dict[str, Any]:
    """Return nodes/edges for a specific environment (or all if include_all)."""
    nodes: List[Dict[str, Any]] = []
    cis_list = list(CMDB.values()) if include_all and env_id == DEFAULT_ENV_ID else list(_env_cis(env_id))
    for ci in cis_list:
        nodes.append(
            {
                "id": ci.ci_id,
                "name": ci.name,
                "type": ci.ci_type,
                "status": ci.status,
                "health": round(ci.health, 2),
                "ip": ci.ip_address,
                "env_id": ci.meta.get("env_id"),
                "depends_on": list(ci.depends_on or []),
                "components": [
                    {
                        "id": c.comp_id,
                        "name": c.name,
                        "type": c.component_type,
                        "status": c.status,
                        "health": round(c.health, 2),
                    }
                    for c in (ci.components or [])
                ],
            }
        )
    edges: List[Dict[str, Any]] = []
    for ci in cis_list:
        for dep in ci.depends_on:
            edges.append({"from": dep, "to": ci.ci_id, "env_id": ci.meta.get("env_id")})
    return {"nodes": nodes, "edges": edges, "now": _now_iso(), "env_id": env_id if not include_all else "all"}


# Root
@app.get("/")
async def root():
    return {"service": "stateful-sim", "now": _now_iso(), "paused": _PAUSED}


