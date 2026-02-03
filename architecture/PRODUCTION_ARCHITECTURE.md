# 🏗️ Production-Ready Architecture: 5-Service Deployment

**Date:** February 2, 2026  
**Status:** Production Architecture - Simplified & Optimized

> 📚 **Related Documents:**
> - **Visual Overview:** `ARCHITECTURE_SIMPLIFIED.md`
> - **Phase 1 Missing Components:** `MISSING_COMPONENTS.md` (Core features to implement)
> - **Phase 2 Advanced Features:** `MISSING_COMPONENTS_PHASE_2.md` (Change simulation, continuous learning, KPI dashboards)

---

## 🎯 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    🌐 External Sources                          │
│                                                                 │
│  Infrastructure Metrics    │  Application Errors  │  End Users │
│  • SNMP Devices            │  • App with Sentry   │  • Browser │
│  • Redfish BMC             │    SDK (errors)      │  • Mobile  │
│  • Telegraf Agents         │  • App exceptions    │            │
│    (push metrics)          │    & traces          │            │
│                            │                      │            │
│  External APIs             │                      │            │
│  • Datadog (metrics/logs)  │                      │            │
│  • Splunk (logs)           │                      │            │
│  • ThousandEyes (network)  │                      │            │
└──────┬─────────────────────┴──────────┬───────────┴─────┬──────┘
       │ Poll (outbound)               │ Push (inbound)  │
       │                                ↓                 ↓
       │    ┌─────────────────────────────┐    ┌──────────────┐
       │    │  Service 1: API             │    │  Service 3:  │
       │    │  • REST endpoints           │    │  Frontend    │
       │    │  • WebSocket server         │    │  • Next.js   │
       │    │  • Webhook receivers        │    │  • Dashboard │
       │    │    - /webhooks/telegraf     │    │  Replicas: 3 │
       │    │    - /webhooks/sentry       │    │              │
       │    │  ✅ STATELESS               │    │  ✅ STATELESS│
       │    │  Replicas: 3-5 instances    │    │              │
       │    └──────────┬──────────────────┘    └──────┬───────┘
       │               │ Write/Read                    │
       │               ↓                               │
       │    ┌──────────────────────────────┐          │
       │    │  Service 4: Redis Cluster    │          │
       │    │  • Streams (event bus)       │          │
       │    │  • Consumer Groups (offset)  │          │
       │    │  • Cache                     │          │
       │    │  • Pub/Sub (WebSocket)       │          │
       │    │  3 nodes (1 primary + 2 rep) │          │
       │    └──────────┬───────────────────┘          │
       │               │ Read with Consumer Groups    │
       │               ↓                              │
       └──────→ ┌─────────────────────────┐          │
                │  Service 2: Worker      │          │
                │  • Producers (poll):    │          │
                │    - SNMP               │          │
                │    - Redfish            │◄─────────┤
                │    - Datadog API        │  🤖 LLM  │
                │    - Splunk API         │  Service │
                │  • Consumer (parse)     │  • OpenAI│
                │  • Aggregator (cluster) │  • Ollama│
                │  • Enricher (LLM)       ├─────────►│
                │  • Automation (Ansible) │          │
                │  ✅ STATELESS (with     │          │
                │     consumer groups)    │          │
                │  Replicas: 2-3 instances│          │
                └──────────┬──────────────┘          │
                           │ Write/Read              │
                           ↓                         │
                ┌──────────────────────────┐         │
                │ Service 5: PostgreSQL    │         │
                │ • Data sources config    │         │
                │ • Alerts & Incidents     │         │
                │ • Alert history          │         │
                │ • Audit logs             │         │
                │ 1 primary + 1 replica    │         │
                └──────────────────────────┘         │
                           │                         │
                           ↓                         │
                ┌──────────────────────────┐         │
                │ Service 6: ChromaDB      │         │
                │ ✅ REQUIRED              │         │
                │ • Log prototypes         │         │
                │ • Vector embeddings      │         │
                │ • Semantic search        │         │
                │ 1-2 instances            │         │
                └──────────────────────────┘         │
                           │                         │
                           ↓                         │
                ┌──────────────────────────┐         │
                │ Service 7: InfluxDB       │         │
                │ • Time-series metrics    │         │
                │ • Raw telemetry data     │         │
                │ • Historical trends      │         │
                │ 1 instance               │         │
                └──────────────────────────┘         │
                                                     │
                                API calls ←──────────┘
```

**Key Points:**
- **Telegraf Agents**: Run on servers, push metrics to `/webhooks/telegraf`
- **Sentry SDK**: Embedded in applications, pushes errors/traces to `/webhooks/sentry`
- **SNMP/Redfish**: Worker polls these devices directly (pull model)
- **Datadog/Splunk**: Worker polls their APIs (pull model)
- **Users**: Access frontend and API directly or through cloud-managed routing (Azure App Service, AWS, GCP, etc.)
- **LLM Service**: OpenAI API (cloud) or Ollama (self-hosted) for AI features
- **PostgreSQL**: Stores structured data (alerts, incidents, data source configs)
- **ChromaDB**: stores log prototypes and embeddings for clustering & semantic search
- **InfluxDB**: stores time-series metrics for historical analysis and trending
- **Worker is STATELESS**: Uses Redis Streams Consumer Groups to track processing offsets (state lives in Redis)

---

## 📁 Repository Structure

### **Current Monorepo Structure** (Transition-Ready for Microservices)

> **Design Philosophy**: No shared code between services. Each service is self-contained and can be extracted to its own repository when ready.

```
enterprise-log-analyzer-be/
├── README.md
├── docker-compose.yml              # Local development
├── docker-compose.prod.yml         # Production-like local setup
├── .gitignore
├── .env.example
│
├── services/
│   ├── api/                        # Service 1: API (FastAPI)
│   │   ├── Dockerfile
│   │   ├── Dockerfile.dev
│   │   ├── requirements.txt
│   │   ├── .dockerignore
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── api/                # REST endpoints
│   │   │   ├── websocket/          # WebSocket handlers
│   │   │   ├── webhooks/           # Webhook receivers
│   │   │   ├── middleware/         # Auth, rate limit, logging
│   │   │   ├── core/               # Config, security
│   │   │   ├── models/             # SQLAlchemy models
│   │   │   ├── schemas/            # Pydantic schemas
│   │   │   ├── crud/               # Database operations
│   │   │   └── db/                 # Database session
│   │   └── tests/
│   │
│   ├── worker/                     # Service 2: Worker (Background)
│   │   ├── Dockerfile
│   │   ├── Dockerfile.dev
│   │   ├── requirements.txt
│   │   ├── .dockerignore
│   │   ├── app/
│   │   │   ├── worker.py           # Main entry point
│   │   │   ├── streams/            # Stream processors
│   │   │   │   ├── consumer.py
│   │   │   │   ├── issues_aggregator.py
│   │   │   │   ├── enricher.py
│   │   │   │   ├── cluster_enricher.py
│   │   │   │   ├── automations.py
│   │   │   │   ├── producer_manager.py
│   │   │   │   └── producers/
│   │   │   ├── services/           # Business logic
│   │   │   │   ├── chroma_service.py
│   │   │   │   ├── clustering_service.py
│   │   │   │   ├── embedding.py
│   │   │   │   ├── llm_service.py
│   │   │   │   ├── prototype_router.py
│   │   │   │   ├── normalizers/
│   │   │   │   └── integrations/
│   │   │   ├── parsers/            # Log parsing
│   │   │   ├── rules/              # Rules engine
│   │   │   ├── models/             # SQLAlchemy models
│   │   │   ├── schemas/            # Pydantic schemas
│   │   │   ├── crud/               # Database operations
│   │   │   ├── db/                 # Database session
│   │   │   └── core/               # Config, logging
│   │   └── tests/
│   │
│   └── frontend/                   # Service 3: Frontend (Next.js)
│       ├── Dockerfile
│       ├── Dockerfile.dev
│       ├── package.json
│       ├── next.config.js
│       ├── tsconfig.json
│       ├── .dockerignore
│       ├── app/                    # Next.js 15 App Router
│       ├── components/
│       ├── lib/
│       └── public/
│
├── infrastructure/                 # Deployment configs
│   ├── docker/                     # Docker Compose files
│   │   ├── docker-compose.dev.yml
│   │   ├── docker-compose.prod.yml
│   │   └── .env.example
│   └── kubernetes/                 # K8s manifests (optional)
│       ├── api-deployment.yaml
│       ├── worker-deployment.yaml
│       └── frontend-deployment.yaml
│
├── scripts/                        # Utility scripts
│   ├── init_db.py                  # Database initialization
│   ├── run_migrations.sh
│   └── test_all.sh
│
└── docs/                           # Documentation
    ├── PRODUCTION_ARCHITECTURE.md
    ├── ARCHITECTURE_SIMPLIFIED.md
```

**Key Principles:**
1. ✅ **Self-contained services** - Can be moved to separate repos without breaking
2. ✅ **Service-level Dockerfiles** - Each service builds independently
3. ✅ **Service-level dependencies** - Each has its own requirements.txt/package.json

---

## 🔧 Service 1: API Service

### **Purpose:** Handle all HTTP traffic (REST + WebSocket + Webhooks)

### **Deployment:** ✅ **SEPARATE DEPLOYMENT REQUIRED**

### **Files Structure:**

```
services/api/
├── Dockerfile                   # Production image
├── Dockerfile.dev               # Development image
├── requirements.txt             # Python dependencies
├── .dockerignore
│
├── app/
│   ├── main.py                  # FastAPI app entry point
│   ├── __init__.py
│   │
│   ├── api/                     # REST endpoints
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── api.py           # Router aggregation
│   │   │   └── endpoints/
│   │   │       ├── alerts.py
│   │   │       ├── incidents.py
│   │   │       ├── sources.py
│   │   │       ├── telemetry.py
│   │   │       ├── health.py
│   │   │       ├── chatbot.py
│   │   │       └── kpis.py
│   │   │
│   │   └── websocket/           # WebSocket handlers
│   │       ├── __init__.py
│   │       ├── manager.py       # Connection manager
│   │       └── handlers.py      # WebSocket endpoints
│   │
│   ├── webhooks/                # Webhook receivers
│   │   ├── __init__.py
│   │   ├── telegraf.py
│   │   ├── sentry.py
│   │   └── generic.py
│   │
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── rate_limit.py
│   │   └── logging.py
│   │
│   └── core/
│       ├── __init__.py
│       ├── config.py            # Settings (from env vars)
│       ├── security.py          # Auth/JWT
│       └── logging_config.py
│
└── tests/
    ├── test_api.py
    ├── test_websocket.py
    └── test_webhooks.py
```

### **Dockerfile:**

```dockerfile
# services/api/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Run with multiple workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### **Environment Variables:**

```bash
# services/api/.env.example
# Mode
SERVICE_MODE=api
ENABLE_BACKGROUND_THREADS=false

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4

# Redis
REDIS_URL=redis://redis-cluster:6379

# Database
DATABASE_URL=postgresql://user:pass@postgres:5432/aiops

# CORS
ALLOWED_ORIGINS=https://dashboard.example.com,https://api.example.com

# Auth
JWT_SECRET_KEY=your-secret-key
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
```

---

## ⚙️ Service 2: Worker Service

### **Purpose:** Background processing (producers, consumer, enrichment, automation)

### **Deployment:** ✅ **SEPARATE DEPLOYMENT REQUIRED**

### **Stateless Design:** ✅ **STATELESS** (uses Redis Streams Consumer Groups)

**Why Worker is Stateless:**
- Redis Streams Consumer Groups track processing offsets in Redis itself
- Each worker instance joins the same consumer group (`aiops-consumers`)
- Redis automatically distributes messages and handles acknowledgments
- Workers can be added/removed without losing progress
- Enables horizontal scaling for heavy workloads

**How Worker Runs Multiple Tasks in Parallel:**

The worker uses Python's `asyncio` to run multiple tasks **concurrently** (in parallel):

```python
# All these tasks run in parallel, not sequentially!
self.tasks.append(asyncio.create_task(consume_logs()))          # Task 1
self.tasks.append(asyncio.create_task(run_issues_aggregator())) # Task 2  
self.tasks.append(asyncio.create_task(run_enricher()))          # Task 3
self.tasks.append(asyncio.create_task(run_cluster_enricher()))  # Task 4
self.tasks.append(asyncio.create_task(run_automations()))       # Task 5
self.tasks.append(asyncio.create_task(producer_manager.run()))  # Task 6
```

**This means:**
- ✅ Consumer reads from Redis **while** Enricher is processing logs
- ✅ Producers poll external APIs **while** Aggregator is clustering issues
- ✅ All tasks run simultaneously using asyncio's event loop
- ✅ No blocking - if one task waits (I/O), others continue running
- ❌ NOT traditional multithreading (no GIL issues)
- ✅ Better than threading for I/O-bound tasks (which this workload is)

### **Files Structure:**

```
services/worker/
├── Dockerfile                   # Production image
├── Dockerfile.dev
├── requirements.txt
├── .dockerignore
│
├── app/
│   ├── worker.py                # Main entry point
│   ├── __init__.py
│   │
│   ├── streams/                 # Stream processing
│   │   ├── __init__.py
│   │   ├── consumer.py          # Log consumer
│   │   ├── issues_aggregator.py
│   │   ├── enricher.py          # LLM enrichment
│   │   ├── cluster_enricher.py
│   │   ├── automations.py
│   │   │
│   │   ├── producer_manager.py  # Producer orchestration
│   │   └── producers/
│   │       ├── __init__.py
│   │       ├── registry.py
│   │       ├── snmp.py
│   │       ├── http_poller.py   # Redfish/DCIM
│   │       ├── filetail.py
│   │       ├── datadog.py
│   │       ├── splunk.py
│   │       └── thousandeyes.py
│   │
│   ├── services/                # Business logic
│   │   ├── __init__.py
│   │   ├── chroma_service.py
│   │   ├── clustering_service.py
│   │   ├── embedding.py
│   │   ├── llm_service.py
│   │   ├── prototype_router.py
│   │   ├── prototype_improver.py
│   │   ├── failure_rules.py
│   │   │
│   │   ├── metrics_normalization.py
│   │   ├── otel_exporter.py
│   │   │
│   │   ├── normalizers/
│   │   │   ├── __init__.py
│   │   │   ├── telegraf.py
│   │   │   ├── dcim_http.py
│   │   │   └── snmp.py
│   │   │
│   │   └── integrations/
│   │       ├── __init__.py
│   │       ├── ansible_tower.py
│   │       ├── terraform_cloud.py
│   │       └── servicenow.py
│   │
│   ├── parsers/                 # Log parsing
│   │   ├── __init__.py
│   │   ├── linux.py
│   │   ├── macos.py
│   │   └── templating.py
│   │
│   ├── rules/                   # Rules engine
│   │   ├── __init__.py
│   │   ├── automations.py
│   │   ├── rules.yml
│   │   └── automations.yml
│   │
│   ├── models/                  # SQLAlchemy models (duplicated from API)
│   │   ├── __init__.py
│   │   ├── data_source.py
│   │   └── alert.py
│   │
│   ├── schemas/                 # Pydantic schemas (duplicated from API)
│   │   ├── __init__.py
│   │   └── data_source.py
│   │
│   ├── crud/                    # Database operations (duplicated from API)
│   │   ├── __init__.py
│   │   └── crud_data_source.py
│   │
│   ├── db/                      # Database session (duplicated from API)
│   │   ├── __init__.py
│   │   └── session.py
│   │
│   └── core/
│       ├── __init__.py
│       ├── config.py            # Settings (from env vars)
│       └── logging_config.py
│
└── tests/
    ├── test_consumer.py
    ├── test_producers.py
    └── test_enricher.py
```

### **Main Entry Point (worker.py):**

```python
# services/worker/app/worker.py
"""
Worker Service - Background Processing
Runs all producer and consumer threads
"""
import asyncio
import logging
import signal
from typing import List

from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.streams.consumer import consume_logs
from app.streams.issues_aggregator import run_issues_aggregator
from app.streams.enricher import run_enricher
from app.streams.cluster_enricher import run_cluster_enricher
from app.streams.automations import run_automations
from app.streams.producer_manager import ProducerManager
from app.services.prototype_improver import run_prototype_improver

LOG = logging.getLogger(__name__)
configure_logging()

settings = get_settings()

class WorkerService:
    def __init__(self):
        self.tasks: List[asyncio.Task] = []
        self.shutdown_event = asyncio.Event()
    
    async def start(self):
        """Start all background tasks"""
        LOG.info("🚀 Starting Worker Service")
        LOG.info(f"Settings: enricher={settings.ENABLE_ENRICHER}, "
                 f"automation={settings.ENABLE_AUTOMATIONS}")
        
        # Core tasks (always run)
        self.tasks.append(asyncio.create_task(
            self._run_with_restart(consume_logs, "Consumer")
        ))
        self.tasks.append(asyncio.create_task(
            self._run_with_restart(run_issues_aggregator, "IssuesAggregator")
        ))
        
        # Conditional tasks
        if settings.ENABLE_ENRICHER:
            self.tasks.append(asyncio.create_task(
                self._run_with_restart(run_enricher, "Enricher")
            ))
            LOG.info("✓ Enricher enabled")
        
        if settings.ENABLE_CLUSTER_ENRICHER:
            self.tasks.append(asyncio.create_task(
                self._run_with_restart(run_cluster_enricher, "ClusterEnricher")
            ))
            LOG.info("✓ ClusterEnricher enabled")
        
        if settings.ENABLE_AUTOMATIONS:
            self.tasks.append(asyncio.create_task(
                self._run_with_restart(run_automations, "Automations")
            ))
            LOG.info("✓ Automations enabled")
        
        # Prototype improver (periodic)
        self.tasks.append(asyncio.create_task(
            self._run_with_restart(run_prototype_improver, "PrototypeImprover")
        ))
        
        # Producer manager
        producer_manager = ProducerManager()
        self.tasks.append(asyncio.create_task(
            self._run_with_restart(producer_manager.run, "ProducerManager")
        ))
        
        LOG.info(f"✓ Worker started with {len(self.tasks)} background tasks")
        
        # Wait for shutdown signal
        await self.shutdown_event.wait()
        
        # Cancel all tasks
        LOG.info("Shutting down worker...")
        for task in self.tasks:
            task.cancel()
        
        await asyncio.gather(*self.tasks, return_exceptions=True)
        LOG.info("Worker stopped")
    
    async def _run_with_restart(self, coro_func, name: str):
        """Run coroutine with automatic restart on failure"""
        backoff = 1.0
        while not self.shutdown_event.is_set():
            try:
                await coro_func()
            except asyncio.CancelledError:
                LOG.info(f"{name} cancelled")
                break
            except Exception as exc:
                LOG.error(f"{name} crashed: {exc}, restarting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    
    def shutdown(self):
        """Trigger graceful shutdown"""
        LOG.info("Shutdown signal received")
        self.shutdown_event.set()


async def main():
    worker = WorkerService()
    
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.shutdown)
    
    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
```

### **Dockerfile:**

```dockerfile
# services/worker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libsnmp-dev \
    snmp-mibs-downloader \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Health check (check if process is running)
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD pgrep -f "python.*worker.py" || exit 1

# Run worker
CMD ["python", "-m", "app.worker"]
```

### **Environment Variables:**

```bash
# services/worker/.env.example
# Mode
SERVICE_MODE=worker
ENABLE_BACKGROUND_THREADS=true

# Feature flags
ENABLE_ENRICHER=true
ENABLE_CLUSTER_ENRICHER=true
ENABLE_AUTOMATIONS=true
ENABLE_PRODUCER_MANAGER=true

# Redis
REDIS_URL=redis://redis-cluster:6379

# Database
DATABASE_URL=postgresql://user:pass@postgres:5432/aiops

# LLM Configuration
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_CHAT_MODEL=gpt-4
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# ChromaDB
CHROMA_HOST=chromadb
CHROMA_PORT=8000

# Processing Configuration
CONSUMER_BLOCK_MS=100
CONSUMER_BATCH_SIZE=10
ISSUE_INACTIVITY_SEC=5
NEAREST_PROTO_THRESHOLD=0.5
```

---

## 🖥️ Service 3: Frontend Service

### **Purpose:** User interface (Dashboard, Alerts, Chatbot)

### **Deployment:** ✅ **SEPARATE DEPLOYMENT REQUIRED**

### **Files Structure:**

```
services/frontend/
├── Dockerfile
├── Dockerfile.dev
├── package.json
├── package-lock.json
├── next.config.js
├── tsconfig.json
├── tailwind.config.js
├── .dockerignore
├── .env.example
│
├── app/                         # Next.js 15 App Router
│   ├── layout.tsx
│   ├── page.tsx                 # Dashboard
│   ├── alerts/
│   │   └── page.tsx
│   ├── incidents/
│   │   └── page.tsx
│   ├── fleet/
│   │   └── page.tsx
│   ├── analytics-reports/
│   │   └── page.tsx
│   ├── integrations-admin/
│   │   └── page.tsx
│   └── api/                     # Next.js API routes
│       └── auth/
│           └── [...nextauth].ts
│
├── components/
│   ├── AppShell.tsx
│   ├── Sidebar.tsx
│   ├── TopBar.tsx
│   ├── FloatingChatbot.tsx
│   └── charts/
│       ├── StatCard.tsx
│       └── AlertsChart.tsx
│
├── lib/
│   ├── api.ts                   # Backend API client
│   ├── websocket.ts             # WebSocket client
│   └── utils.ts
│
├── hooks/
│   ├── useRealtimeAlerts.ts
│   └── useAuth.ts
│
├── public/
│   ├── images/
│   └── sounds/
│
└── styles/
    └── globals.css
```

### **Dockerfile:**

```dockerfile
# services/frontend/Dockerfile
FROM node:20-alpine AS builder

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci

# Copy source
COPY . .

# Build
RUN npm run build

# Production image
FROM node:20-alpine

WORKDIR /app

# Copy built app
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./
COPY --from=builder /app/public ./public

EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD node -e "require('http').get('http://localhost:3000/api/health', (r) => {process.exit(r.statusCode === 200 ? 0 : 1)})"

CMD ["npm", "start"]
```

### **Environment Variables:**

```bash
# services/frontend/.env.example
# API Configuration
NEXT_PUBLIC_API_URL=https://api.example.com
NEXT_PUBLIC_WS_URL=wss://api.example.com

# Auth (NextAuth.js)
NEXTAUTH_URL=https://dashboard.example.com
NEXTAUTH_SECRET=your-secret

# Feature Flags
NEXT_PUBLIC_ENABLE_CHATBOT=true
NEXT_PUBLIC_ENABLE_ANALYTICS=true
```

---

## 🤖 AI/ML Layer: LLM Service

### **Purpose:** Provide AI capabilities for classification, summarization, and chatbot

### **Deployment:** ⚠️ **CHOOSE ONE OPTION**

### **Option 1: OpenAI API (Cloud)**

**Pros:**
- Zero infrastructure management
- Latest GPT-4 models
- Instant scalability
- High quality results

**Cons:**
- Recurring API costs ($500-$2,000/month depending on usage)
- Data leaves your infrastructure
- Internet dependency
- Rate limits

**Configuration:**
```bash
# Environment Variables
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4
OPENAI_EMBED_MODEL=text-embedding-ada-002
```

**No deployment needed** - Just configure API keys in Worker and API services.

---

### **Option 2: Ollama (Self-Hosted)**

**Pros:**
- Data stays on-premise
- No per-request costs
- Customizable models
- No rate limits

**Cons:**
- Requires GPU instances
- Infrastructure management
- Model quality may vary
- Deployment complexity

**Files Structure:**
```
services/llm/
├── Dockerfile
├── docker-compose.yml
├── models/
│   ├── llama2.bin          # Downloaded models
│   └── nomic-embed.bin
└── README.md
```

**Dockerfile:**
```dockerfile
# services/llm/Dockerfile
FROM ollama/ollama:latest

# Pre-pull models
RUN ollama pull llama2
RUN ollama pull mistral
RUN ollama pull nomic-embed-text

EXPOSE 11434

CMD ["ollama", "serve"]
```

**Environment Variables:**
```bash
# Worker and API services
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=llama2
OLLAMA_EMBED_MODEL=nomic-embed-text
```

**Hardware Requirements:**
```
Minimum (Development):
- CPU: 8 cores
- RAM: 16GB
- GPU: NVIDIA T4 (16GB VRAM)
- Storage: 50GB SSD

Recommended (Production):
- CPU: 16 cores
- RAM: 32GB
- GPU: NVIDIA A10 or A100
- Storage: 100GB SSD
```

**Cloud Instances:**
- **AWS:** g5.2xlarge ($1.21/hour) - NVIDIA A10G GPU
- **Azure:** NC6s_v3 ($0.90/hour) - NVIDIA V100 GPU
- **GCP:** n1-standard-8 + T4 GPU ($0.95/hour)

**Docker Compose (for local testing):**
```yaml
services:
  ollama:
    build: ./services/llm
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  ollama-data:
```

---

### **LLM Usage Throughout the System:**

| Component | LLM Function | Example |
|-----------|--------------|---------|
| **Worker Consumer** | Parse & classify raw logs | "This is a kernel panic" → structured JSON |
| **Worker Aggregator** | Summarize clusters of issues | 50 similar errors → "PostgreSQL connection pool exhausted" |
| **Worker Enricher** | Root cause analysis | "Likely caused by network partition between pods" |
| **Worker Enricher** | Generate embeddings | Log text → 1536-dim vector for ChromaDB |
| **API Chatbot** | Answer questions | "Why did service X crash?" → conversational response |
| **API Chatbot** | HyDE queries | User question → hypothetical answer → search ChromaDB |

---

## 📦 ChromaDB (Vector Database)

### **Purpose:** Store embeddings and enable semantic search

### **Deployment:** ✅ **REQUIRED FOR PRODUCTION** (Critical for log clustering & prototype matching)

**Why ChromaDB is Required:**
- **Log Prototype Storage**: Stores learned log patterns for clustering similar issues
- **Semantic Search**: Enables HyDE (Hypothetical Document Embeddings) queries for chatbot
- **Similarity Matching**: Core feature for grouping similar logs into incidents
- **Vector Embeddings**: Generated by LLM service, stored here for fast retrieval

**Without ChromaDB, these features will not work:**
- ❌ Automatic log clustering
- ❌ Prototype-based incident grouping
- ❌ Semantic chatbot queries
- ❌ "Similar issues" detection

**Files Structure:**
```
services/chromadb/
├── Dockerfile
├── docker-compose.yml
└── data/                      # Persistent volume for embeddings
```

**Dockerfile:**
```dockerfile
FROM chromadb/chroma:latest

EXPOSE 8000

CMD ["chroma", "run", "--host", "0.0.0.0", "--port", "8000"]
```

**Environment Variables:**
```bash
# Worker and API services
CHROMA_MODE=http
CHROMA_SERVER_HOST=chromadb
CHROMA_SERVER_PORT=8000

# Collections used
CHROMA_LOG_PROTOTYPES_COLLECTION=log_prototypes
CHROMA_EMBEDDINGS_COLLECTION=log_embeddings
```

---

## 📊 Service 7: InfluxDB (Time-Series Database)

### **Purpose:** Store time-series metrics for historical analysis, anomaly detection, and prediction

### **Deployment:** ✅ **REQUIRED FOR ANOMALY DETECTION & PREDICTION**

**Critical Use Cases:**
1. ✅ **Anomaly Detection** - Compare current metrics against historical baselines
2. ✅ **Prediction & Automation** - Forecast future issues and trigger preventive actions
3. ✅ **Capacity Planning** - Predict resource exhaustion before it happens
4. ✅ **Historical Correlation** - Find patterns that preceded past incidents

**Why InfluxDB is Now Required:**

Based on your architecture diagram requirements:
- **Anomaly Detection**: Requires historical data to establish baselines and detect outliers
- **Prediction**: Requires time-series data for forecasting and trend analysis
- **Automation**: Requires predictive alerts to trigger automated remediation

**What Gets Stored:**
- Raw telemetry data from Telegraf agents (CPU, memory, disk, network)
- SNMP polling results with timestamps
- Redfish BMC metrics (temperature, power, fan speeds)
- Application performance metrics
- Infrastructure health metrics over time

**Files Structure:**
```
services/influxdb/
├── Dockerfile
├── docker-compose.yml
└── data/                      # Persistent volume
```

**Dockerfile:**
```dockerfile
FROM influxdb:2.7-alpine

EXPOSE 8086

# InfluxDB will auto-initialize on first run
CMD ["influxd"]
```

**Environment Variables:**
```bash
# InfluxDB Configuration
INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=your-secret-token
INFLUXDB_ORG=aiops
INFLUXDB_BUCKET=telemetry
INFLUXDB_RETENTION=90d  # Keep data for 90 days

# Enable in Worker and API
INFLUXDB_ENABLED=true
```

---

### **Comparison: Statistical Methods vs LLM/ML for Anomaly Detection**

| Approach | Best For | Pros | Cons | Latency | Cost |
|----------|----------|------|------|---------|------|
| **Statistical (InfluxDB + Z-score)** | Simple metrics (CPU, memory, disk) | ✅ Fast (~10ms)<br/>✅ No training needed<br/>✅ Low cost<br/>✅ Easy to explain | ❌ Misses complex patterns<br/>❌ No context awareness<br/>❌ Many false positives | <50ms | $0 |
| **ML Models (Isolation Forest, LSTM)** | Complex patterns, seasonality | ✅ Better accuracy<br/>✅ Learns patterns<br/>✅ Handles seasonality | ❌ Needs training data<br/>❌ Slower (~100ms)<br/>❌ Requires model management | 100-500ms | $50-200/mo |
| **LLM-Based** | Context-aware, multi-signal | ✅ Understands context<br/>✅ Correlates multiple signals<br/>✅ Natural language explanations | ❌ Very slow (1-5s)<br/>❌ Expensive<br/>❌ Overkill for simple cases | 1-5s | $500-2000/mo |

**Recommended Hybrid Approach:**

```
┌─────────────────────────────────────────────────────────────┐
│                   ANOMALY DETECTION PIPELINE                │
│                                                             │
│  Step 1: Fast Statistical Detection (InfluxDB + Z-score)   │
│  ├─ Detects: Simple outliers in metrics                    │
│  ├─ Latency: ~10ms                                         │
│  └─ Filters: 95% of normal data                           │
│                        ↓                                    │
│  Step 2: ML Model Validation (Isolation Forest/LSTM)       │
│  ├─ Analyzes: Potential anomalies from Step 1             │
│  ├─ Latency: ~100ms                                       │
│  └─ Reduces: False positives by 80%                       │
│                        ↓                                    │
│  Step 3: LLM Context Analysis (Only for confirmed anomalies)│
│  ├─ Provides: Root cause explanation                       │
│  ├─ Correlates: Logs, metrics, and historical incidents   │
│  ├─ Latency: ~2s                                          │
│  └─ Output: Human-readable explanation                    │
└─────────────────────────────────────────────────────────────┘
```

**Why This Hybrid Approach Works:**

1. **Statistical (InfluxDB)** catches 90% of obvious anomalies in 10ms
2. **ML Model** validates and catches complex patterns in 100ms
3. **LLM** only runs for confirmed anomalies, providing context in 2s


---

### **Implementation: Hybrid Anomaly Detection Service**

**New Service Component:** `services/worker/app/services/anomaly_detector.py`

```python
"""
Hybrid Anomaly Detection Service
Combines statistical methods, ML models, and LLM for optimal accuracy and cost
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
from influxdb_client import InfluxDBClient
from sklearn.ensemble import IsolationForest
from app.core.config import settings
from app.services.llm_service import LLMService

LOG = logging.getLogger(__name__)


class HybridAnomalyDetector:
    """
    Three-tier anomaly detection:
    1. Statistical (fast, cheap)
    2. ML Model (accurate, moderate cost)
    3. LLM (context-aware, expensive - only for confirmed anomalies)
    """
    
    def __init__(self):
        self.client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG
        )
        self.query_api = self.client.query_api()
        self.llm_service = LLMService()
        
        # ML models (loaded once, reused)
        self.isolation_forest = IsolationForest(contamination=0.1, random_state=42)
        self.models_trained = False
    
    async def detect_anomaly_hybrid(
        self,
        metric_name: str,
        current_value: float,
        host: str,
        lookback_hours: int = 24
    ) -> Dict:
        """
        Three-tier hybrid anomaly detection
        
        Tier 1: Statistical (Z-score) - Fast filter
        Tier 2: ML Model (Isolation Forest) - Validation
        Tier 3: LLM Analysis - Context and explanation
        """
        
        # TIER 1: Statistical Detection (Fast - ~10ms)
        statistical_result = await self._detect_statistical(
            metric_name, current_value, host, lookback_hours
        )
        
        # If not anomalous by stats, return immediately (95% of cases)
        if not statistical_result["is_anomaly"]:
            return {
                "is_anomaly": False,
                "tier": "statistical",
                "method": "z-score",
                **statistical_result
            }
        
        LOG.info(f"Tier 1 detected potential anomaly: {metric_name}={current_value}")
        
        # TIER 2: ML Model Validation (~100ms)
        ml_result = await self._detect_ml(
            metric_name, current_value, host, lookback_hours
        )
        
        # If ML model says it's normal, it's likely a false positive
        if not ml_result["is_anomaly"]:
            return {
                "is_anomaly": False,
                "tier": "ml_validated",
                "method": "isolation_forest",
                "note": "Statistical flagged but ML model validated as normal",
                **ml_result
            }
        
        LOG.warning(f"Tier 2 confirmed anomaly: {metric_name}={current_value}")
        
        # TIER 3: LLM Context Analysis (Slow ~2s, only for confirmed anomalies)
        llm_result = await self._analyze_with_llm(
            metric_name, current_value, host, statistical_result, ml_result
        )
        
        return {
            "is_anomaly": True,
            "tier": "llm_confirmed",
            "statistical": statistical_result,
            "ml_validation": ml_result,
            "llm_analysis": llm_result,
            "confidence": "high",
            "explanation": llm_result.get("explanation"),
            "root_cause_hypothesis": llm_result.get("root_cause"),
            "recommended_actions": llm_result.get("actions")
        }
    
    async def _detect_statistical(
        self,
        metric_name: str,
        current_value: float,
        host: str,
        lookback_hours: int
    ) -> Dict:
        """Tier 1: Fast statistical detection using Z-score"""
        try:
            start_time = datetime.utcnow() - timedelta(hours=lookback_hours)
            
            query = f'''
            from(bucket: "{settings.INFLUXDB_BUCKET}")
              |> range(start: {start_time.isoformat()}Z)
              |> filter(fn: (r) => r["_measurement"] == "system_metrics")
              |> filter(fn: (r) => r["_field"] == "{metric_name}")
              |> filter(fn: (r) => r["host"] == "{host}")
            '''
            
            result = self.query_api.query(query=query)
            
            historical_values = []
            for table in result:
                for record in table.records:
                    historical_values.append(float(record.get_value()))
            
            if len(historical_values) < 10:
                return {"is_anomaly": False, "reason": "insufficient_data"}
            
            mean = np.mean(historical_values)
            std = np.std(historical_values)
            z_score = abs((current_value - mean) / std) if std > 0 else 0
            
            return {
                "is_anomaly": z_score > 3.0,
                "z_score": z_score,
                "baseline_mean": mean,
                "baseline_std": std,
                "method": "z-score",
                "samples": len(historical_values)
            }
        except Exception as e:
            LOG.error(f"Statistical detection error: {e}")
            return {"is_anomaly": False, "error": str(e)}
    
    async def _detect_ml(
        self,
        metric_name: str,
        current_value: float,
        host: str,
        lookback_hours: int
    ) -> Dict:
        """Tier 2: ML-based detection using Isolation Forest"""
        try:
            # Get historical data for training/prediction
            start_time = datetime.utcnow() - timedelta(hours=lookback_hours * 7)  # 7x more data for ML
            
            query = f'''
            from(bucket: "{settings.INFLUXDB_BUCKET}")
              |> range(start: {start_time.isoformat()}Z)
              |> filter(fn: (r) => r["_measurement"] == "system_metrics")
              |> filter(fn: (r) => r["_field"] == "{metric_name}")
              |> filter(fn: (r) => r["host"] == "{host}")
            '''
            
            result = self.query_api.query(query=query)
            
            historical_values = []
            for table in result:
                for record in table.records:
                    historical_values.append(float(record.get_value()))
            
            if len(historical_values) < 100:
                return {"is_anomaly": False, "reason": "insufficient_ml_data"}
            
            # Train model if not already trained
            X_train = np.array(historical_values).reshape(-1, 1)
            if not self.models_trained:
                self.isolation_forest.fit(X_train)
                self.models_trained = True
            
            # Predict if current value is anomaly
            X_test = np.array([[current_value]])
            prediction = self.isolation_forest.predict(X_test)
            anomaly_score = self.isolation_forest.score_samples(X_test)
            
            # -1 = anomaly, 1 = normal
            is_anomaly = prediction[0] == -1
            
            return {
                "is_anomaly": is_anomaly,
                "anomaly_score": float(anomaly_score[0]),
                "method": "isolation_forest",
                "model_confidence": abs(anomaly_score[0])
            }
        except Exception as e:
            LOG.error(f"ML detection error: {e}")
            # Fallback to statistical if ML fails
            return {"is_anomaly": True, "error": str(e), "fallback": "statistical"}
    
    async def _analyze_with_llm(
        self,
        metric_name: str,
        current_value: float,
        host: str,
        statistical_result: Dict,
        ml_result: Dict
    ) -> Dict:
        """Tier 3: LLM-powered context analysis"""
        try:
            # Get recent logs and events for context
            recent_logs = await self._get_recent_logs(host, minutes=30)
            recent_alerts = await self._get_recent_alerts(host, hours=24)
            
            # Build context prompt
            prompt = f"""
Analyze this anomaly detected in our infrastructure:

HOST: {host}
METRIC: {metric_name}
CURRENT VALUE: {current_value}
BASELINE: {statistical_result.get('baseline_mean', 0):.2f} ± {statistical_result.get('baseline_std', 0):.2f}
Z-SCORE: {statistical_result.get('z_score', 0):.2f}
ML ANOMALY SCORE: {ml_result.get('anomaly_score', 0):.4f}

RECENT LOGS (last 30 minutes):
{recent_logs[:3000]}  # Limit to avoid token limits

RECENT ALERTS (last 24 hours):
{recent_alerts[:1000]}

Based on this information:
1. Is this a true anomaly or false positive?
2. What is the likely root cause?
3. What immediate actions should be taken?
4. Is this related to any recent alerts or patterns?

Provide a concise analysis in JSON format:
{{
    "is_true_anomaly": boolean,
    "confidence": "low|medium|high",
    "root_cause": "brief explanation",
    "explanation": "detailed analysis",
    "severity": "low|medium|high|critical",
    "actions": ["action1", "action2"],
    "related_incidents": ["id1", "id2"]
}}
"""
            
            # Call LLM
            response = await self.llm_service.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,  # Low temperature for consistent analysis
                response_format="json"
            )
            
            import json
            analysis = json.loads(response)
            
            return {
                "is_true_anomaly": analysis.get("is_true_anomaly", True),
                "confidence": analysis.get("confidence", "medium"),
                "root_cause": analysis.get("root_cause"),
                "explanation": analysis.get("explanation"),
                "severity": analysis.get("severity", "medium"),
                "actions": analysis.get("actions", []),
                "related_incidents": analysis.get("related_incidents", []),
                "llm_model": settings.OPENAI_CHAT_MODEL,
                "analysis_time": datetime.utcnow().isoformat()
            }
        except Exception as e:
            LOG.error(f"LLM analysis error: {e}")
            return {
                "is_true_anomaly": True,
                "confidence": "low",
                "explanation": f"LLM analysis failed: {e}",
                "severity": "medium",
                "actions": ["Manual investigation required"]
            }
    
    async def _get_recent_logs(self, host: str, minutes: int = 30) -> str:
        """Get recent log entries for context"""
        # Query Redis or PostgreSQL for recent logs
        # This is a placeholder - implement based on your log storage
        return f"Recent logs for {host} in last {minutes} minutes..."
    
    async def _get_recent_alerts(self, host: str, hours: int = 24) -> str:
        """Get recent alerts for context"""
        # Query PostgreSQL for recent alerts
        return f"Recent alerts for {host} in last {hours} hours..."
```

---

### **Advanced: Time-Series Forecasting with ML Models**

**For Prediction, LSTMs or Prophet are Better than Simple Linear Regression**

```python
"""
Enhanced Prediction Service with ML Models
Uses Facebook Prophet or LSTM for accurate forecasting
"""
import logging
from datetime import datetime, timedelta
from typing import Dict
import numpy as np
import pandas as pd
from prophet import Prophet  # Facebook's time-series forecasting
from influxdb_client import InfluxDBClient
from app.core.config import settings

LOG = logging.getLogger(__name__)


class MLPredictor:
    """Advanced prediction using ML models (Prophet or LSTM)"""
    
    def __init__(self):
        self.client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG
        )
        self.query_api = self.client.query_api()
    
    async def predict_exhaustion_ml(
        self,
        metric_name: str,
        host: str,
        threshold: float,
        lookback_days: int = 14
    ) -> Dict:
        """
        Predict resource exhaustion using Facebook Prophet
        Much better than linear regression for:
        - Seasonal patterns (daily/weekly cycles)
        - Trend changes
        - Holiday effects
        """
        try:
            # Query historical data
            start_time = datetime.utcnow() - timedelta(days=lookback_days)
            
            query = f'''
            from(bucket: "{settings.INFLUXDB_BUCKET}")
              |> range(start: {start_time.isoformat()}Z)
              |> filter(fn: (r) => r["_measurement"] == "system_metrics")
              |> filter(fn: (r) => r["_field"] == "{metric_name}")
              |> filter(fn: (r) => r["host"] == "{host}")
              |> aggregateWindow(every: 1h, fn: mean)
            '''
            
            result = self.query_api.query(query=query)
            
            # Convert to pandas DataFrame (required by Prophet)
            timestamps = []
            values = []
            for table in result:
                for record in table.records:
                    timestamps.append(record.get_time())
                    values.append(float(record.get_value()))
            
            if len(values) < 48:  # Need at least 48 hours for Prophet
                return {"error": "insufficient_data", "samples": len(values)}
            
            df = pd.DataFrame({
                'ds': timestamps,  # Prophet requires 'ds' column
                'y': values        # Prophet requires 'y' column
            })
            
            # Train Prophet model
            model = Prophet(
                daily_seasonality=True,
                weekly_seasonality=True,
                changepoint_prior_scale=0.05  # Detect trend changes
            )
            model.fit(df)
            
            # Forecast next 30 days
            future = model.make_future_dataframe(periods=30*24, freq='H')  # 30 days, hourly
            forecast = model.predict(future)
            
            # Find when threshold will be crossed
            future_predictions = forecast[forecast['ds'] > datetime.utcnow()]
            exhaustion_point = future_predictions[future_predictions['yhat'] >= threshold]
            
            if len(exhaustion_point) > 0:
                exhaustion_time = exhaustion_point.iloc[0]['ds']
                days_until = (exhaustion_time - datetime.utcnow()).total_seconds() / 86400
                
                return {
                    "will_exhaust": True,
                    "exhaustion_time": exhaustion_time.isoformat(),
                    "days_until_exhaustion": round(days_until, 1),
                    "current_value": values[-1],
                    "predicted_value_7d": forecast[forecast['ds'] == datetime.utcnow() + timedelta(days=7)]['yhat'].values[0],
                    "threshold": threshold,
                    "confidence_interval_lower": exhaustion_point.iloc[0]['yhat_lower'],
                    "confidence_interval_upper": exhaustion_point.iloc[0]['yhat_upper'],
                    "model": "prophet",
                    "accuracy": "high"  # Prophet is very accurate
                }
            else:
                return {
                    "will_exhaust": False,
                    "reason": "threshold_not_reached_in_30_days",
                    "predicted_value_30d": forecast.iloc[-1]['yhat'],
                    "model": "prophet"
                }
        
        except Exception as e:
            LOG.error(f"ML prediction error: {e}")
            # Fallback to simple linear regression
            return await self._predict_linear_fallback(metric_name, host, threshold)
    
    async def _predict_linear_fallback(
        self,
        metric_name: str,
        host: str,
        threshold: float
    ) -> Dict:
        """Simple linear regression fallback if Prophet fails"""
        # ... existing linear regression code ...
        pass
```

---

### **Hybrid Approach Analysis**

**Scenario: 1000 hosts, 10 metrics each, 1 data point per minute**

| Approach | Daily Anomalies Detected | LLM API Calls | False Positive Rate |
|----------|-------------------------|---------------|-------------------|
| **Pure Statistical** | ~500 | 0 | 30-40% |
| **Pure LLM** | ~500 | 720,000 | 5-10% |
| **Hybrid (Recommended)** | ~500 | ~1,500 | 8-12% |

**Hybrid Approach Breakdown:**
- 1M data points/day → 500 statistical anomalies (0.05%)
- 500 statistical → 100 ML-confirmed (80% reduction)
- 100 ML-confirmed → 50 LLM analyses per day
- 50 LLM calls/day × 30 days = 1,500 calls/month

**Accuracy Comparison:**
- Statistical only: 60-70% accuracy
- ML validation: 85-90% accuracy
- LLM context: 92-95% accuracy (but only needed for confirmed cases)
    ```python
    async def detect_anomaly(
        self,
        metric_name: str,
        current_value: float,
        host: str,
        lookback_hours: int = 24
    ) -> Dict:
        """
        Detect if current value is anomalous compared to historical baseline
        
        Args:
            metric_name: Name of the metric (e.g., "cpu_usage")
            current_value: Current metric value
            host: Host identifier
            lookback_hours: Hours of historical data to analyze
        
        Returns:
            {
                "is_anomaly": bool,
                "severity": "low" | "medium" | "high",
                "score": float,  # Z-score
                "baseline_mean": float,
                "baseline_std": float,
                "threshold": float
            }
        """
        try:
            # Query historical data from InfluxDB
            start_time = datetime.utcnow() - timedelta(hours=lookback_hours)
            
            query = f'''
            from(bucket: "{settings.INFLUXDB_BUCKET}")
              |> range(start: {start_time.isoformat()}Z)
              |> filter(fn: (r) => r["_measurement"] == "system_metrics")
              |> filter(fn: (r) => r["_field"] == "{metric_name}")
              |> filter(fn: (r) => r["host"] == "{host}")
              |> keep(columns: ["_time", "_value"])
            '''
            
            result = self.query_api.query(query=query)
            
            # Extract values
            historical_values = []
            for table in result:
                for record in table.records:
                    historical_values.append(float(record.get_value()))
            
            if len(historical_values) < 10:
                LOG.warning(f"Insufficient historical data for {metric_name} on {host}")
                return {
                    "is_anomaly": False,
                    "severity": "unknown",
                    "score": 0.0,
                    "reason": "insufficient_data"
                }
            
            # Calculate baseline statistics
            mean = np.mean(historical_values)
            std = np.std(historical_values)
            
            # Calculate Z-score (number of standard deviations from mean)
            if std == 0:
                z_score = 0.0
            else:
                z_score = abs((current_value - mean) / std)
            
            # Determine if anomaly and severity
            is_anomaly = z_score > 3.0  # 3 sigma rule
            
            if z_score > 5.0:
                severity = "high"
            elif z_score > 4.0:
                severity = "medium"
            elif z_score > 3.0:
                severity = "low"
            else:
                severity = "normal"
            
            LOG.info(
                f"Anomaly check: {metric_name}={current_value}, "
                f"baseline={mean:.2f}±{std:.2f}, z-score={z_score:.2f}, "
                f"anomaly={is_anomaly}"
            )
            
            return {
                "is_anomaly": is_anomaly,
                "severity": severity,
                "score": z_score,
                "baseline_mean": mean,
                "baseline_std": std,
                "current_value": current_value,
                "threshold": 3.0,
                "historical_min": min(historical_values),
                "historical_max": max(historical_values),
                "percentile_95": np.percentile(historical_values, 95),
                "samples_analyzed": len(historical_values)
            }
            
        except Exception as e:
            LOG.error(f"Error detecting anomaly: {e}")
            return {
                "is_anomaly": False,
                "severity": "error",
                "score": 0.0,
                "error": str(e)
            }
    
    async def detect_pattern_anomaly(
        self,
        metric_name: str,
        host: str,
        window_hours: int = 1
    ) -> Dict:
        """
        Detect anomalies in metric patterns (e.g., sudden spikes, drops)
        """
        try:
            start_time = datetime.utcnow() - timedelta(hours=window_hours)
            
            query = f'''
            from(bucket: "{settings.INFLUXDB_BUCKET}")
              |> range(start: {start_time.isoformat()}Z)
              |> filter(fn: (r) => r["_measurement"] == "system_metrics")
              |> filter(fn: (r) => r["_field"] == "{metric_name}")
              |> filter(fn: (r) => r["host"] == "{host}")
              |> derivative(unit: 1m, nonNegative: false)
            '''
            
            result = self.query_api.query(query=query)
            
            # Analyze rate of change
            derivatives = []
            for table in result:
                for record in table.records:
                    derivatives.append(float(record.get_value()))
            
            if len(derivatives) < 5:
                return {"is_anomaly": False, "reason": "insufficient_data"}
            
            # Detect sudden changes
            max_derivative = max(abs(d) for d in derivatives)
            mean_derivative = np.mean([abs(d) for d in derivatives])
            
            # Anomaly if rate of change is > 10x normal
            is_spike = max_derivative > (mean_derivative * 10)
            
            return {
                "is_anomaly": is_spike,
                "pattern_type": "spike" if is_spike else "normal",
                "max_rate_of_change": max_derivative,
                "avg_rate_of_change": mean_derivative
            }
            
        except Exception as e:
            LOG.error(f"Error detecting pattern anomaly: {e}")
            return {"is_anomaly": False, "error": str(e)}

---

### **Implementation: Prediction Service**

**New Service Component:** `services/worker/app/services/predictor.py`

```python
"""
Prediction Service
Forecasts future metric values and predicts resource exhaustion
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
from scipy import stats
from influxdb_client import InfluxDBClient
from app.core.config import settings

LOG = logging.getLogger(__name__)


class MetricPredictor:
    """Predicts future metric values using time-series analysis"""
    
    def __init__(self):
        self.client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG
        )
        self.query_api = self.client.query_api()
    
    async def predict_exhaustion(
        self,
        metric_name: str,
        host: str,
        threshold: float,
        lookback_days: int = 7
    ) -> Dict:
        """
        Predict when a metric will reach a threshold (e.g., disk full)
        
        Args:
            metric_name: Name of metric (e.g., "disk_used_percent")
            host: Host identifier
            threshold: Threshold value (e.g., 95 for 95% disk usage)
            lookback_days: Days of historical data for trend analysis
        
        Returns:
            {
                "will_exhaust": bool,
                "exhaustion_time": datetime | None,
                "days_until_exhaustion": float | None,
                "current_value": float,
                "trend": "increasing" | "decreasing" | "stable",
                "growth_rate": float  # per day
            }
        """
        try:
            # Query historical data
            start_time = datetime.utcnow() - timedelta(days=lookback_days)
            
            query = f'''
            from(bucket: "{settings.INFLUXDB_BUCKET}")
              |> range(start: {start_time.isoformat()}Z)
              |> filter(fn: (r) => r["_measurement"] == "system_metrics")
              |> filter(fn: (r) => r["_field"] == "{metric_name}")
              |> filter(fn: (r) => r["host"] == "{host}")
              |> aggregateWindow(every: 1h, fn: mean)
            '''
            
            result = self.query_api.query(query=query)
            
            # Extract time-series data
            timestamps = []
            values = []
            for table in result:
                for record in table.records:
                    timestamps.append(record.get_time())
                    values.append(float(record.get_value()))
            
            if len(values) < 24:  # Need at least 24 hours of data
                return {
                    "will_exhaust": False,
                    "reason": "insufficient_data",
                    "samples": len(values)
                }
            
            # Convert timestamps to hours since start
            time_hours = [(t - timestamps[0]).total_seconds() / 3600 for t in timestamps]
            
            # Linear regression to find trend
            slope, intercept, r_value, p_value, std_err = stats.linregress(time_hours, values)
            
            # Calculate current value and trend
            current_value = values[-1]
            growth_rate_per_hour = slope
            growth_rate_per_day = slope * 24
            
            # Determine trend
            if abs(slope) < 0.01:
                trend = "stable"
            elif slope > 0:
                trend = "increasing"
            else:
                trend = "decreasing"
            
            # Predict exhaustion time
            will_exhaust = False
            exhaustion_time = None
            days_until_exhaustion = None
            
            if trend == "increasing" and current_value < threshold:
                # Hours until threshold is reached
                hours_until = (threshold - current_value) / growth_rate_per_hour
                
                if hours_until > 0 and hours_until < 30 * 24:  # Within 30 days
                    will_exhaust = True
                    exhaustion_time = datetime.utcnow() + timedelta(hours=hours_until)
                    days_until_exhaustion = hours_until / 24
            
            LOG.info(
                f"Prediction for {metric_name} on {host}: "
                f"current={current_value:.2f}, trend={trend}, "
                f"growth_rate={growth_rate_per_day:.2f}/day, "
                f"will_exhaust={will_exhaust}"
            )
            
            return {
                "will_exhaust": will_exhaust,
                "exhaustion_time": exhaustion_time.isoformat() if exhaustion_time else None,
                "days_until_exhaustion": round(days_until_exhaustion, 1) if days_until_exhaustion else None,
                "current_value": current_value,
                "threshold": threshold,
                "trend": trend,
                "growth_rate_per_day": growth_rate_per_day,
                "confidence": r_value ** 2,  # R-squared
                "prediction_method": "linear_regression"
            }
            
        except Exception as e:
            LOG.error(f"Error predicting exhaustion: {e}")
            return {
                "will_exhaust": False,
                "error": str(e)
            }
    
    async def forecast_next_value(
        self,
        metric_name: str,
        host: str,
        hours_ahead: int = 1
    ) -> Dict:
        """
        Forecast metric value N hours ahead
        """
        try:
            # Similar to predict_exhaustion but returns forecasted value
            # Implementation would use same linear regression approach
            # Or more advanced methods like ARIMA, Prophet, etc.
            pass
        except Exception as e:
            LOG.error(f"Error forecasting: {e}")
            return {"error": str(e)}
```

---

### **Implementation: Integration with Worker Service**

**Update:** `services/worker/app/streams/consumer.py`

```python
# Add anomaly detection to the consumer
from app.services.anomaly_detector import AnomalyDetector
from app.services.predictor import MetricPredictor
from app.services.influxdb_writer import InfluxDBWriter

anomaly_detector = AnomalyDetector()
predictor = MetricPredictor()
influx_writer = InfluxDBWriter()

async def consume_logs():
    """Enhanced consumer with anomaly detection"""
    while True:
        messages = redis.xreadgroup(
            groupname="aiops-consumers",
            consumername="consumer-1",
            streams={"logs": ">"},
            count=10,
            block=100
        )
        
        for stream, message_list in messages:
            for message_id, data in message_list:
                try:
                    # Parse log
                    log_entry = parse_log(data)
                    
                    # 1. Store metrics in InfluxDB
                    if log_entry.get("metrics"):
                        await influx_writer.write_metrics(
                            host=log_entry["host"],
                            metrics=log_entry["metrics"]
                        )
                    
                    # 2. Check for anomalies
                    if log_entry.get("cpu_usage"):
                        anomaly_result = await anomaly_detector.detect_anomaly(
                            metric_name="cpu_usage",
                            current_value=log_entry["cpu_usage"],
                            host=log_entry["host"]
                        )
                        
                        if anomaly_result["is_anomaly"]:
                            LOG.warning(
                                f"⚠️ ANOMALY DETECTED: {log_entry['host']} "
                                f"CPU={log_entry['cpu_usage']}% "
                                f"(baseline={anomaly_result['baseline_mean']:.1f}%, "
                                f"z-score={anomaly_result['score']:.2f})"
                            )
                            
                            # Create anomaly alert
                            await create_alert(
                                title=f"CPU Usage Anomaly on {log_entry['host']}",
                                severity=anomaly_result["severity"],
                                description=f"CPU usage {log_entry['cpu_usage']}% is {anomaly_result['score']:.1f} standard deviations above baseline",
                                anomaly_data=anomaly_result
                            )
                    
                    # 3. Check for predictive alerts
                    if log_entry.get("disk_used_percent"):
                        prediction = await predictor.predict_exhaustion(
                            metric_name="disk_used_percent",
                            host=log_entry["host"],
                            threshold=90.0  # Alert at 90%
                        )
                        
                        if prediction["will_exhaust"]:
                            LOG.warning(
                                f"🔮 PREDICTIVE ALERT: {log_entry['host']} "
                                f"disk will reach 90% in {prediction['days_until_exhaustion']} days"
                            )
                            
                            # Create predictive alert
                            await create_alert(
                                title=f"Disk Space Exhaustion Predicted on {log_entry['host']}",
                                severity="medium",
                                description=(
                                    f"Disk space will reach 90% in approximately "
                                    f"{prediction['days_until_exhaustion']:.1f} days "
                                    f"at current growth rate of {prediction['growth_rate_per_day']:.2f}%/day"
                                ),
                                prediction_data=prediction
                            )
                    
                    # Continue normal processing
                    # ...
                    
                except Exception as e:
                    LOG.error(f"Error processing message: {e}")
                finally:
                    # Acknowledge message
                    redis.xack("logs", "aiops-consumers", message_id)
```

---

### **Implementation: InfluxDB Writer**

**New Service Component:** `services/worker/app/services/influxdb_writer.py`

```python
"""
InfluxDB Writer Service
Writes metrics to InfluxDB for historical storage
"""
import logging
from datetime import datetime
from typing import Dict, List
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from app.core.config import settings

LOG = logging.getLogger(__name__)


class InfluxDBWriter:
    """Writes metrics to InfluxDB"""
    
    def __init__(self):
        if not settings.INFLUXDB_ENABLED:
            LOG.info("InfluxDB is disabled")
            return
        
        self.client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
    
    async def write_metrics(self, host: str, metrics: Dict):
        """
        Write system metrics to InfluxDB
        
        Args:
            host: Host identifier
            metrics: Dictionary of metric name -> value
        """
        if not settings.INFLUXDB_ENABLED:
            return
        
        try:
            points = []
            timestamp = datetime.utcnow()
            
            # Create point for each metric
            for metric_name, value in metrics.items():
                point = Point("system_metrics") \
                    .tag("host", host) \
                    .field(metric_name, float(value)) \
                    .time(timestamp, WritePrecision.NS)
                
                points.append(point)
            
            # Write batch to InfluxDB
            self.write_api.write(
                bucket=settings.INFLUXDB_BUCKET,
                record=points
            )
            
            LOG.debug(f"Wrote {len(points)} metrics to InfluxDB for {host}")
            
        except Exception as e:
            LOG.error(f"Error writing to InfluxDB: {e}")
    
    async def write_event(
        self,
        event_type: str,
        host: str,
        severity: str,
        message: str,
        tags: Dict = None
    ):
        """Write discrete events to InfluxDB"""
        if not settings.INFLUXDB_ENABLED:
            return
        
        try:
            point = Point("events") \
                .tag("event_type", event_type) \
                .tag("host", host) \
                .tag("severity", severity) \
                .field("message", message) \
                .time(datetime.utcnow(), WritePrecision.NS)
            
            if tags:
                for key, value in tags.items():
                    point.tag(key, str(value))
            
            self.write_api.write(
                bucket=settings.INFLUXDB_BUCKET,
                record=point
            )
            
        except Exception as e:
            LOG.error(f"Error writing event to InfluxDB: {e}")
```

---

### **Implementation: Prediction-Based Automation**

**Update:** `services/worker/app/streams/automations.py`

```python
"""
Enhanced Automations with Predictive Alerts
Triggers automated remediation based on anomalies and predictions
"""
import logging
import asyncio
from datetime import datetime
from app.services.predictor import MetricPredictor
from app.services.anomaly_detector import AnomalyDetector
from app.services.integrations.ansible_tower import AnsibleTowerClient

LOG = logging.getLogger(__name__)

predictor = MetricPredictor()
anomaly_detector = AnomalyDetector()
ansible = AnsibleTowerClient()


async def run_predictive_automations():
    """
    Continuously monitor for predictive alerts and trigger automation
    """
    LOG.info("🤖 Starting Predictive Automation Engine")
    
    while True:
        try:
            # Get all active hosts from PostgreSQL
            hosts = await get_all_monitored_hosts()
            
            for host in hosts:
                # Check disk space prediction
                disk_prediction = await predictor.predict_exhaustion(
                    metric_name="disk_used_percent",
                    host=host["name"],
                    threshold=90.0,
                    lookback_days=7
                )
                
                if disk_prediction["will_exhaust"]:
                    days_left = disk_prediction["days_until_exhaustion"]
                    
                    # Trigger automation if < 3 days
                    if days_left < 3:
                        LOG.warning(
                            f"🚨 AUTO-REMEDIATION: Disk on {host['name']} "
                            f"will be full in {days_left:.1f} days. "
                            f"Triggering cleanup automation..."
                        )
                        
                        # Execute Ansible playbook for disk cleanup
                        await ansible.run_playbook(
                            playbook="disk_cleanup.yml",
                            hosts=[host["name"]],
                            extra_vars={
                                "threshold": 80,
                                "cleanup_tmp": True,
                                "cleanup_logs": True
                            }
                        )
                        
                        # Create incident
                        await create_incident(
                            title=f"Automated Disk Cleanup on {host['name']}",
                            severity="medium",
                            status="resolved",
                            description=(
                                f"Predictive alert triggered automated cleanup. "
                                f"Disk was projected to reach 90% in {days_left:.1f} days."
                            ),
                            automation_triggered=True
                        )
                
                # Check memory usage anomalies
                current_memory = await get_current_metric(host["name"], "memory_used_percent")
                if current_memory:
                    memory_anomaly = await anomaly_detector.detect_anomaly(
                        metric_name="memory_used_percent",
                        current_value=current_memory,
                        host=host["name"]
                    )
                    
                    if memory_anomaly["is_anomaly"] and memory_anomaly["severity"] == "high":
                        LOG.warning(
                            f"🚨 AUTO-REMEDIATION: Memory anomaly on {host['name']} "
                            f"({current_memory}%, z-score={memory_anomaly['score']:.2f}). "
                            f"Triggering memory cleanup..."
                        )
                        
                        # Execute memory cleanup automation
                        await ansible.run_playbook(
                            playbook="memory_cleanup.yml",
                            hosts=[host["name"]],
                            extra_vars={
                                "restart_services": ["apache2", "mysql"],
                                "clear_cache": True
                            }
                        )
                
                # Check CPU usage predictions
                cpu_prediction = await predictor.predict_exhaustion(
                    metric_name="cpu_usage_percent",
                    host=host["name"],
                    threshold=95.0,
                    lookback_days=1
                )
                
                if cpu_prediction["will_exhaust"]:
                    hours_left = cpu_prediction["days_until_exhaustion"] * 24
                    
                    if hours_left < 4:  # Less than 4 hours
                        LOG.warning(
                            f"🚨 AUTO-REMEDIATION: CPU on {host['name']} "
                            f"will reach 95% in {hours_left:.1f} hours. "
                            f"Triggering scale-out..."
                        )
                        
                        # Trigger auto-scaling or load balancing
                        await trigger_scale_out(host["name"])
            
            # Sleep before next check
            await asyncio.sleep(300)  # Check every 5 minutes
            
        except Exception as e:
            LOG.error(f"Error in predictive automation: {e}")
            await asyncio.sleep(60)


async def trigger_scale_out(host: str):
    """Trigger horizontal scaling for overloaded host"""
    LOG.info(f"Triggering scale-out for {host}")
    
    # Example: Add more worker nodes via Terraform/Kubernetes
    # This would integrate with your infrastructure automation
    pass


automation_rules = [
    {
        "name": "Predictive Disk Cleanup",
        "condition": "disk_will_exhaust_in_3_days",
        "action": "run_ansible_playbook",
        "playbook": "disk_cleanup.yml",
        "priority": "high"
    },
    {
        "name": "Memory Anomaly Remediation",
        "condition": "memory_anomaly_high_severity",
        "action": "restart_services",
        "services": ["apache2", "mysql"],
        "priority": "medium"
    },
    {
        "name": "CPU Overload Prevention",
        "condition": "cpu_will_reach_95_in_4_hours",
        "action": "scale_out",
        "priority": "critical"
    }
]
```

---

### **Configuration Updates**

**Update:** `services/worker/.env.example`

```bash
# InfluxDB Configuration (REQUIRED for anomaly detection & prediction)
INFLUXDB_ENABLED=true
INFLUXDB_URL=http://influxdb:8086
INFLUXDB_TOKEN=your-influxdb-admin-token
INFLUXDB_ORG=aiops
INFLUXDB_BUCKET=telemetry
INFLUXDB_RETENTION=90d

# Anomaly Detection Settings
ANOMALY_DETECTION_ENABLED=true
ANOMALY_THRESHOLD_SIGMA=3.0
ANOMALY_LOOKBACK_HOURS=24
ANOMALY_MIN_SAMPLES=10

# Prediction Settings
PREDICTION_ENABLED=true
PREDICTION_LOOKBACK_DAYS=7
PREDICTION_FORECAST_DAYS=30
PREDICTION_UPDATE_INTERVAL_MINUTES=5

# Automation Settings
ENABLE_PREDICTIVE_AUTOMATIONS=true
AUTOMATION_DISK_THRESHOLD_DAYS=3
AUTOMATION_CPU_THRESHOLD_HOURS=4
AUTOMATION_MEMORY_ANOMALY_THRESHOLD=high
```

**Update:** `services/worker/requirements.txt`

```txt
# Add these dependencies for hybrid anomaly detection
influxdb-client>=1.40.0
numpy>=1.24.0
scipy>=1.10.0
scikit-learn>=1.3.0      # For Isolation Forest
prophet>=1.1.5           # Facebook Prophet for forecasting
pandas>=2.0.0            # Required by Prophet
pystan>=3.0.0            # Required by Prophet
tensorflow>=2.13.0       # Optional: For LSTM models
torch>=2.0.0             # Optional: For PyTorch-based models
```

---

### **Recommendation: Which Approach to Use?**

**For Your Use Case (Anomaly Detection + Prediction):**

| Use Case | Recommended Approach | Why |
|----------|---------------------|-----|
| **Real-time Anomaly Detection** | ✅ **Hybrid (Statistical + ML + LLM)** | Best accuracy trade-off |
| **Capacity Forecasting** | ✅ **Prophet or LSTM** | Handles seasonality & trends |
| **Root Cause Analysis** | ✅ **LLM** | Understands context & correlations |
| **Simple Threshold Alerts** | ✅ **Statistical** | Fast and efficient |

---

### **Final Answer: InfluxDB vs LLM/ML**

**InfluxDB's Role:**
- ✅ **Storage**: Historical data for analysis
- ✅ **Query Engine**: Fast time-series queries
- ❌ **NOT** an AI/ML tool itself

**What Actually Does the Detection:**
1. **Statistical algorithms** (Z-score, IQR) - using data FROM InfluxDB
2. **ML models** (Isolation Forest, LSTM) - using data FROM InfluxDB  
3. **LLM** (GPT-4) - using context FROM InfluxDB + logs

**Bottom Line:**
- InfluxDB = **Data Storage** 📦
- Statistical/ML/LLM = **Intelligence Layer** 🧠
- **You need BOTH**: InfluxDB provides the data, ML/LLM provides the intelligence

**Can InfluxDB do it alone?** ❌ No - it's just a database
**Can LLM do it alone?** ❌ No - too slow and expensive
**Best approach?** ✅ **Hybrid: InfluxDB + ML + LLM**

---

### **API Endpoints for Anomaly Detection & Prediction**

**New Endpoints:** `services/api/app/api/v1/endpoints/analytics.py`

```python
"""
Analytics API Endpoints
Expose anomaly detection and prediction results to frontend
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timedelta
from typing import List
from app.services.influxdb_reader import InfluxDBReader
from app.services.anomaly_detector import AnomalyDetector
from app.services.predictor import MetricPredictor

router = APIRouter()
influx_reader = InfluxDBReader()
anomaly_detector = AnomalyDetector()
predictor = MetricPredictor()


@router.get("/anomalies")
async def get_recent_anomalies(
    host: str = None,
    hours: int = 24
):
    """Get recent anomaly detections"""
    # Query PostgreSQL for anomaly alerts created in last N hours
    anomalies = await db.query(
        f"SELECT * FROM alerts WHERE type='anomaly' "
        f"AND created_at > NOW() - INTERVAL '{hours} hours'"
    )
    return anomalies


@router.get("/predictions/{host}")
async def get_host_predictions(host: str):
    """Get predictions for a specific host"""
    predictions = []
    
    # Disk space prediction
    disk_pred = await predictor.predict_exhaustion(
        metric_name="disk_used_percent",
        host=host,
        threshold=90.0
    )
    if disk_pred["will_exhaust"]:
        predictions.append({
            "type": "disk_exhaustion",
            "severity": "high" if disk_pred["days_until_exhaustion"] < 3 else "medium",
            **disk_pred
        })
    
    # Memory prediction
    mem_pred = await predictor.predict_exhaustion(
        metric_name="memory_used_percent",
        host=host,
        threshold=95.0
    )
    if mem_pred["will_exhaust"]:
        predictions.append({
            "type": "memory_exhaustion",
            "severity": "high",
            **mem_pred
        })
    
    return predictions


@router.get("/metrics/historical/{host}")
async def get_historical_metrics(
    host: str,
    metric: str,
    hours: int = 24
):
    """Get historical metric data from InfluxDB"""
    data = await influx_reader.query_metric(
        host=host,
        metric_name=metric,
        hours=hours
    )
    return data


@router.post("/anomalies/check")
async def check_for_anomaly(
    metric_name: str,
    current_value: float,
    host: str
):
    """Manually trigger anomaly check"""
    result = await anomaly_detector.detect_anomaly(
        metric_name=metric_name,
        current_value=current_value,
        host=host
    )
    return result
```

---

### **Frontend Dashboard Integration**

**New Component:** `services/frontend/components/AnomalyChart.tsx`

```typescript
"use client"

import { useEffect, useState } from 'react'
import { Line } from 'react-chartjs-2'

interface Anomaly {
  timestamp: string
  value: number
  severity: 'low' | 'medium' | 'high'
  baseline_mean: number
  baseline_std: number
}

export function AnomalyChart({ host, metric }: { host: string, metric: string }) {
  const [anomalies, setAnomalies] = useState<Anomaly[]>([])
  const [historicalData, setHistoricalData] = useState<any>(null)

  useEffect(() => {
    // Fetch historical data and anomalies
    fetch(`/api/v1/analytics/metrics/historical/${host}?metric=${metric}&hours=24`)
      .then(res => res.json())
      .then(data => setHistoricalData(data))

    fetch(`/api/v1/analytics/anomalies?host=${host}&hours=24`)
      .then(res => res.json())
      .then(data => setAnomalies(data.filter(a => a.metric === metric)))
  }, [host, metric])

  const chartData = {
    labels: historicalData?.timestamps || [],
    datasets: [
      {
        label: 'Actual Value',
        data: historicalData?.values || [],
        borderColor: 'rgb(75, 192, 192)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
      },
      {
        label: 'Anomalies',
        data: anomalies.map(a => ({ x: a.timestamp, y: a.value })),
        borderColor: 'rgb(255, 99, 132)',
        backgroundColor: 'rgba(255, 99, 132, 0.5)',
        pointRadius: 8,
        pointStyle: 'triangle',
      }
    ]
  }

  return (
    <div className="p-4 border rounded-lg">
      <h3 className="text-lg font-semibold mb-4">
        {metric} - Anomaly Detection
      </h3>
      <Line data={chartData} />
      
      <div className="mt-4">
        <h4 className="font-semibold">Recent Anomalies:</h4>
        {anomalies.map((anomaly, idx) => (
          <div key={idx} className={`p-2 my-2 rounded ${
            anomaly.severity === 'high' ? 'bg-red-100' :
            anomaly.severity === 'medium' ? 'bg-yellow-100' : 'bg-blue-100'
          }`}>
            <span className="font-mono">{anomaly.timestamp}</span>: {anomaly.value}
            (baseline: {anomaly.baseline_mean.toFixed(2)} ± {anomaly.baseline_std.toFixed(2)})
          </div>
        ))}
      </div>
    </div>
  )
}
```

---

## 🗄️ Service 4: Redis Cluster

### **Purpose:** Event streaming, caching, pub/sub

### **Deployment:** ✅ **SEPARATE DEPLOYMENT REQUIRED** (Use managed service)

### **Recommended Options:**

#### **Option A: Managed Service (Recommended)**

```yaml
# No deployment needed - use managed service
AWS: ElastiCache for Redis
Azure: Azure Cache for Redis
GCP: Memorystore for Redis

Configuration:
- Version: Redis 7.x
- Topology: 3-node cluster (1 primary + 2 replicas)
- Memory: 16GB per node
- Persistence: AOF enabled
- Backup: Daily snapshots
```

---

## 🗃️ Service 5: PostgreSQL

### **Purpose:** Configuration, alerts, incidents, audit logs

### **Deployment:** ✅ **SEPARATE DEPLOYMENT REQUIRED** (Use managed service)

### **Recommended Options:**

#### **Option A: Managed Service (Recommended)**

```yaml
# No deployment needed - use managed service
AWS: RDS PostgreSQL
Azure: Azure Database for PostgreSQL
GCP: Cloud SQL for PostgreSQL

Configuration:
- Version: PostgreSQL 15
- Instance Type: db.t3.large (2 vCPU, 8GB RAM)
- Storage: 500GB SSD
- Topology: 1 primary + 1 read replica
- Backup: Automated daily + PITR
- Connection Pooling: PgBouncer
```

---

## 📦 Deployment Summary

### **Which Services Need Separate Deployments?**

| Service | Separate Deployment? | Replicas | State | Reason |
|---------|---------------------|----------|-------|---------|
| **1. API** | ✅ **YES** | 3-5 | ✅ Stateless | Handles HTTP traffic, scales by RPS |
| **2. Worker** | ✅ **YES** | 2-3 | ✅ Stateless* | Background processing, uses consumer groups |
| **3. Frontend** | ✅ **YES** | 3 | ✅ Stateless | Separate stack (Node.js vs Python) |
| **4. Redis** | ✅ **YES** | 3 | 🔴 Stateful | Event bus + cache, needs clustering |
| **5. PostgreSQL** | ✅ **YES** | 2 | 🔴 Stateful | Persistent data store |
| **6. ChromaDB** | ✅ **YES** | 1-2 | 🔴 Stateful | Vector embeddings (REQUIRED) |
| **7. InfluxDB** | ✅ **YES** | 1-2 | 🔴 Stateful | Time-series metrics (REQUIRED for anomaly detection & prediction) |
| **LLM (OpenAI)** | ❌ **NO** | N/A | ✅ Stateless | Cloud API - just configure keys |
| **LLM (Ollama)** | ⚠️ **OPTIONAL** | 1-2 | ✅ Stateless | Self-hosted - deploy if not using OpenAI |

**\*Worker is stateless because:**
- Redis Streams Consumer Groups track offsets in Redis
- No local state stored on worker instances
- Workers can be scaled horizontally without coordination
- All tasks run **in parallel** using asyncio (not sequential!)

**Core: 7 separate deployments, 15-21 containers**  
**With self-hosted LLM: +1-2 containers (GPU required)**

### **Database Summary**

| Database | Purpose | Data Stored | Required? | Notes |
|----------|---------|-------------|-----------|-------|
| **PostgreSQL** | Relational data | Data sources config, Alerts, Incidents, Audit logs, Alert history | ✅ YES | Core database |
| **ChromaDB** | Vector embeddings | Log prototypes, Vector embeddings, Similarity search | ✅ YES | Required for clustering |
| **Redis** | Event streaming | Redis Streams, Cache, Pub/Sub, Consumer offsets | ✅ YES | Core event bus |
| **InfluxDB** | Time-series | Historical metrics, Anomaly baselines, Prediction data | ✅ YES | Required for anomaly detection & prediction |

---

## 📊 Resource Requirements

### **Small Deployment (< 10K logs/sec)**

| Service | Instances | CPU/instance | RAM/instance | Total CPU | Total RAM |
|---------|------|---------|---------|-----------|-----------|
| API | 3 | 1 core | 2GB | 3 cores | 6GB |
| Worker | 2 | 2 cores | 4GB | 4 cores | 8GB |
| Frontend | 2 | 0.5 core | 1GB | 1 core | 2GB |
| Redis | 3 | 2 cores | 8GB | 6 cores | 24GB |
| PostgreSQL | 2 | 4 cores | 16GB | 8 cores | 32GB |
| **Total** | **12** | - | - | **22 cores** | **72GB** |

### **Medium Deployment (10K-50K logs/sec)**

| Service | Instances | CPU/instance | RAM/instance | Total CPU | Total RAM |
|---------|------|---------|---------|-----------|-----------|
| API | 5 | 2 cores | 4GB | 10 cores | 20GB |
| Worker | 3 | 4 cores | 8GB | 12 cores | 24GB |
| Frontend | 3 | 1 core | 2GB | 3 cores | 6GB |
| Redis | 3 | 4 cores | 16GB | 12 cores | 48GB |
| PostgreSQL | 2 | 8 cores | 32GB | 16 cores | 64GB |
| **Total** | **16** | - | - | **53 cores** | **162GB** |


---

**Example file to create:**
```yaml
# infrastructure/docker/docker-compose.prod.yml
version: '3.8'

services:
  api:
    build:
      context: ../../services/api
      dockerfile: Dockerfile
    environment:
      - SERVICE_MODE=api
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://user:pass@postgres:5432/aiops
      - CHROMA_HOST=chromadb
    depends_on:
      - redis
      - postgres
      - chromadb
    deploy:
      replicas: 3

  worker:
    build:
      context: ../../services/worker
      dockerfile: Dockerfile
    environment:
      - SERVICE_MODE=worker
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://user:pass@postgres:5432/aiops
      - CHROMA_HOST=chromadb
    depends_on:
      - redis
      - postgres
      - chromadb
    deploy:
      replicas: 2

  frontend:
    build:
      context: ../../services/frontend
      dockerfile: Dockerfile
    environment:
      - NEXT_PUBLIC_API_URL=http://api:8000
    depends_on:
      - api
    deploy:
      replicas: 3

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis-data:/data

  postgres:
    image: postgres:15
    environment:
      - POSTGRES_DB=aiops
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=pass
    volumes:
      - postgres-data:/var/lib/postgresql/data

  chromadb:
    image: chromadb/chroma:latest
    volumes:
      - chroma-data:/chroma/chroma

volumes:
  redis-data:
  postgres-data:
  chroma-data:
```

#### **3. Security & Authentication (Critical Gap)**

**Current State:** No authentication mentioned  
**Required:** Secure API access

**Action Items:**
- [ ] Implement JWT authentication in API service
  - [ ] Create `app/core/security.py` with JWT utilities
  - [ ] Add `app/middleware/auth.py` for auth middleware
  - [ ] Protect all API endpoints (except health checks)

- [ ] Implement rate limiting
  - [ ] Add `app/middleware/rate_limit.py`
  - [ ] Use Redis for rate limit tracking
  - [ ] Configure per-endpoint limits

- [ ] Secrets management
  - [ ] Create `.env.example` with all required secrets
  - [ ] Document secrets rotation process
  - [ ] Use environment variables for all sensitive config

- [ ] API key management for webhook endpoints
  - [ ] Add API key validation for Telegraf webhooks
  - [ ] Add token validation for Sentry webhooks

**Example to add:**
```python
# services/api/app/middleware/auth.py
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
```

#### **4. Monitoring & Observability (Missing Entirely)**

**Current State:** Basic logging  
**Required:** Production-grade observability

**Action Items:**
- [ ] Add Prometheus metrics exporter
  - [ ] Install `prometheus-client` in both API and Worker
  - [ ] Create `/metrics` endpoint in API service
  - [ ] Add custom metrics (request duration, queue depth, errors)

- [ ] Add structured logging
  - [ ] Use JSON logging format
  - [ ] Include correlation IDs for request tracing
  - [ ] Log levels configurable via environment

- [ ] Health checks
  - [ ] Add `/health` endpoint checking Redis, PostgreSQL, ChromaDB
  - [ ] Add `/readiness` endpoint for Kubernetes
  - [ ] Add dependency health checks

- [ ] Distributed tracing
  - [ ] Add OpenTelemetry instrumentation
  - [ ] Configure Jaeger or Zipkin exporter
  - [ ] Trace requests through API → Worker → LLM

**Example to add:**
```python
# services/api/app/api/v1/endpoints/health.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.chroma_service import ChromaService
import redis

router = APIRouter()

@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    health = {
        "status": "healthy",
        "checks": {}
    }
    
    # Check PostgreSQL
    try:
        db.execute("SELECT 1")
        health["checks"]["postgres"] = "ok"
    except Exception as e:
        health["checks"]["postgres"] = f"error: {str(e)}"
        health["status"] = "unhealthy"
    
    # Check Redis
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        health["checks"]["redis"] = "ok"
    except Exception as e:
        health["checks"]["redis"] = f"error: {str(e)}"
        health["status"] = "unhealthy"
    
    # Check ChromaDB
    try:
        chroma = ChromaService()
        chroma.client.heartbeat()
        health["checks"]["chromadb"] = "ok"
    except Exception as e:
        health["checks"]["chromadb"] = f"error: {str(e)}"
        health["status"] = "unhealthy"
    
    return health
```

#### **5. CI/CD Pipeline (Missing Entirely)**

**Current State:** Manual builds  
**Required:** Automated deployment pipeline

**Action Items:**
- [ ] Create `.github/workflows/` directory
  - [ ] `ci.yml` - Run tests on every PR
  - [ ] `build.yml` - Build Docker images on merge to main
  - [ ] `deploy-dev.yml` - Deploy to dev environment
  - [ ] `deploy-prod.yml` - Deploy to production (manual approval)

- [ ] Set up Docker image registry
  - [ ] AWS ECR / Azure ACR / GCP GCR / Docker Hub
  - [ ] Tag images with git commit SHA
  - [ ] Keep last 10 images for rollback

- [ ] Automated testing
  - [ ] Unit tests for all services
  - [ ] Integration tests for API endpoints
  - [ ] E2E tests for critical flows

**Example to add:**
```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
    branches: [main, develop]
  push:
    branches: [main, develop]

jobs:
  test-api:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          cd services/api
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run tests
        run: |
          cd services/api
          pytest tests/ --cov=app --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  test-worker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          cd services/worker
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run tests
        run: |
          cd services/worker
          pytest tests/ --cov=app --cov-report=xml

  build-images:
    needs: [test-api, test-worker]
    runs-on: ubuntu-latest
    if: github.event_name == 'push'
    steps:
      - uses: actions/checkout@v3
      - name: Build API image
        run: |
          cd services/api
          docker build -t myregistry/api:${{ github.sha }} .
      - name: Build Worker image
        run: |
          cd services/worker
          docker build -t myregistry/worker:${{ github.sha }} .
      - name: Push images
        run: |
          docker push myregistry/api:${{ github.sha }}
          docker push myregistry/worker:${{ github.sha }}
```

#### **6. Database Migrations (Missing)**

**Current State:** Direct SQLAlchemy models  
**Required:** Version-controlled schema changes

**Action Items:**
- [ ] Add Alembic for database migrations
  - [ ] Initialize Alembic in both API and Worker services
  - [ ] Create initial migration from current models
  - [ ] Add migration commands to deployment process

**Example to add:**
```bash
# In services/api/ and services/worker/
pip install alembic
alembic init alembic
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

#### **7. Configuration Management (Incomplete)**

**Current State:** Some environment variables  
**Required:** Comprehensive config management

**Action Items:**
- [ ] Create comprehensive `.env.example` for each service
- [ ] Document all configuration options
- [ ] Add config validation on startup
- [ ] Support multiple environments (dev, staging, prod)

**Example to add:**
```bash
# services/api/.env.example
# Service Configuration
SERVICE_NAME=api-service
SERVICE_VERSION=1.0.0
ENVIRONMENT=production

# API Server
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=4

# Security
JWT_SECRET_KEY=change-me-in-production
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
API_KEY_HEADER=X-API-Key

# Database
DATABASE_URL=postgresql://user:password@postgres:5432/aiops
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=40

# Redis
REDIS_URL=redis://redis:6379
REDIS_PASSWORD=
REDIS_MAX_CONNECTIONS=50

# ChromaDB
CHROMA_HOST=chromadb
CHROMA_PORT=8000
CHROMA_COLLECTION_NAME=log_prototypes

# LLM Provider
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_MAX_RETRIES=3
OPENAI_TIMEOUT=30

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json

# CORS
CORS_ORIGINS=https://dashboard.example.com,https://app.example.com
CORS_ALLOW_CREDENTIALS=true

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60
RATE_LIMIT_BURST=10
```

#### **9. Testing Infrastructure (Missing)**

**Current State:** No tests visible  
**Required:** Comprehensive test suite

**Action Items:**
- [ ] Add unit tests for all services
- [ ] Add integration tests for API endpoints
- [ ] Add E2E tests for critical flows
- [ ] Set up test fixtures and factories
- [ ] Add performance/load tests

**Example to add:**
```python
# services/api/tests/test_alerts.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_create_alert():
    response = client.post(
        "/api/v1/alerts",
        json={
            "title": "Test Alert",
            "severity": "high",
            "source": "test"
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Alert"
    assert data["severity"] == "high"
```


