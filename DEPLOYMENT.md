## Deployment Guide (Backend)

This document explains how to deploy the backend and required services using
Docker or Kubernetes. The backend depends on Redis, Postgres, and Chroma.

### Docker Compose (backend only)

From `enterprise-log-analyzer-be/`:

```bash
docker compose up -d
```

This starts:

- `app` (FastAPI API)
- `redis`
- `postgres`
- `producer` (optional log producer)

If you want the full demo stack (frontend + mock API + logbert + ollama),
deploy those separately or use a combined stack repo.

### Chroma (recommended: HTTP mode)

For stability, run Chroma as a separate HTTP service and point the API to it:

```text
CHROMA_MODE=http
CHROMA_SERVER_HOST=chroma
CHROMA_SERVER_PORT=8000
```

### Kubernetes

#### 1) Build and push images

```bash
docker build -t registry.example.com/ela-backend:latest .
docker push registry.example.com/ela-backend:latest
```

If you need the full demo stack, also build/push:

- frontend image (from the FE repo)
- logbert service
- mock API

#### 2) Required services

- `postgres` (persistent volume)
- `redis`
- `chroma` (persistent volume, HTTP on port 8000)
- `backend`
- optional: `ollama`, `logbert`, `mock-api`

#### 3) Required environment variables

```text
POSTGRES_SERVER=postgres
POSTGRES_USER=fastapi
POSTGRES_PASSWORD=fastapi
POSTGRES_DB=fastapi

REDIS_URL=redis://redis:6379/0

CHROMA_MODE=http
CHROMA_SERVER_HOST=chroma
CHROMA_SERVER_PORT=8000
```

If you use LogBERT and Ollama:

```text
EMBEDDING_PROVIDER=logbert
LOGBERT_BASE_URL=http://logbert:8080
LOGBERT_MODEL_NAME=bert-base-uncased

LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_CHAT_MODEL=mistral
```

#### 4) Health checks

- Backend: `GET /api/v1/health`
- Chroma: `GET /api/v1/heartbeat` (or `/api/v1/health` depending on version)

