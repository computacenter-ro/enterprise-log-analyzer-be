# =============================================================================
# Telegraf Docker Image for Enterprise Log Analyzer
# =============================================================================
#
# This Dockerfile builds a Telegraf image configured for the Enterprise Log
# Analyzer backend. It's primarily useful for:
#   - Linux servers (full functionality)
#   - CI/CD pipelines
#   - Development/testing
#
# IMPORTANT: On macOS, running Telegraf natively (via Homebrew) is recommended
# because Docker containers cannot access macOS unified logs directly.
#
# Build:
#   docker build -t enterprise-log-analyzer/telegraf .
#
# Run:
#   docker run -d \
#     --name telegraf \
#     -e TELEGRAF_TOKEN=your-token \
#     -e TELEGRAF_AGENT_ID=your-agent-id \
#     -e BACKEND_URL=http://host.docker.internal:8000 \
#     -v /var/run/docker.sock:/var/run/docker.sock:ro \
#     enterprise-log-analyzer/telegraf
#
# =============================================================================

# -----------------------------------------------------------------------------
# Base Image
# -----------------------------------------------------------------------------
FROM telegraf:1.29-alpine

# -----------------------------------------------------------------------------
# Labels (OCI Standard)
# -----------------------------------------------------------------------------
LABEL org.opencontainers.image.title="Enterprise Log Analyzer Telegraf Agent"
LABEL org.opencontainers.image.description="Telegraf agent configured for Enterprise Log Analyzer telemetry ingestion"
LABEL org.opencontainers.image.vendor="Enterprise Log Analyzer"
LABEL org.opencontainers.image.source="https://github.com/your-org/enterprise-log-analyzer-be"

# -----------------------------------------------------------------------------
# Install Additional Tools
# -----------------------------------------------------------------------------
RUN apk add --no-cache \
    bash \
    jq \
    curl \
    ca-certificates \
    && rm -rf /var/cache/apk/*

# -----------------------------------------------------------------------------
# Create Non-Root User (Security Best Practice)
# -----------------------------------------------------------------------------
# Note: Telegraf needs access to Docker socket, so we add to docker group
# The docker group GID should match the host's docker group
ARG DOCKER_GID=999
RUN addgroup -g ${DOCKER_GID} docker || true \
    && adduser -D -u 1000 telegraf \
    && addgroup telegraf docker || true

# -----------------------------------------------------------------------------
# Copy Configuration Files
# -----------------------------------------------------------------------------
COPY telegraf.conf /etc/telegraf/telegraf.conf
COPY scripts/ /etc/telegraf/scripts/

# Make scripts executable
RUN chmod +x /etc/telegraf/scripts/*.sh 2>/dev/null || true

# -----------------------------------------------------------------------------
# Environment Variables (Defaults)
# -----------------------------------------------------------------------------
ENV TELEGRAF_TOKEN=""
ENV TELEGRAF_AGENT_ID=""
ENV BACKEND_URL="http://localhost:8000"
ENV ENVIRONMENT="production"
ENV REGION="docker"
ENV SERVICE_GROUP="enterprise-log-analyzer"
ENV COLLECTION_INTERVAL="10s"
ENV FLUSH_INTERVAL="10s"
ENV TELEGRAF_SCRIPTS_PATH="/etc/telegraf/scripts"

# -----------------------------------------------------------------------------
# Health Check
# -----------------------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD pgrep telegraf || exit 1

# -----------------------------------------------------------------------------
# Expose Ports (if using Telegraf as a receiver)
# -----------------------------------------------------------------------------
# Uncomment if you want to expose metrics endpoint
# EXPOSE 9273

# -----------------------------------------------------------------------------
# Volume Mounts (Document expected mounts)
# -----------------------------------------------------------------------------
# /var/run/docker.sock - Docker socket for container metrics/logs
# /var/log             - Host logs (Linux only)
VOLUME ["/var/run/docker.sock"]

# -----------------------------------------------------------------------------
# Entry Point
# -----------------------------------------------------------------------------
# Run as root to access Docker socket
# For production, consider mounting socket with appropriate permissions
USER root

ENTRYPOINT ["/entrypoint.sh"]
CMD ["telegraf"]
