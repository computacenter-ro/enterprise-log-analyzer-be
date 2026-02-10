# syntax=docker/dockerfile:1

###############################################
# Builder (install Python deps)
###############################################
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

###############################################
# Runtime
###############################################
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "mock_api:app", "--host", "0.0.0.0", "--port", "8000"]
