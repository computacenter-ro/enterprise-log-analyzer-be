# Telegraf Agent Configuration

This directory contains the Telegraf agent configuration for collecting logs and metrics from macOS servers and sending them to the Enterprise Log Analyzer backend.

## Overview

Telegraf is an open-source agent for collecting, processing, and sending metrics and logs. This configuration enables:

- **Docker container logs** - Logs from all running containers
- **Docker container metrics** - CPU, memory, network, I/O per container
- **macOS system logs** - Unified logs via the `log` command
- **Application log files** - Tail files from `/var/log/` and other locations
- **System metrics** - CPU, memory, disk, network statistics

## Prerequisites

- macOS 10.15+ (Catalina or later)
- [Homebrew](https://brew.sh/) package manager
- Docker Desktop for Mac (if collecting container logs)
- Running Enterprise Log Analyzer backend

## Quick Start

### 1. Install Telegraf

```bash
brew install telegraf
```

### 2. Register a Telegraf Data Source

Create a data source in the backend to get authentication credentials:

```bash
curl -X POST http://localhost:8000/api/v1/sources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mac-server-telegraf",
    "type": "telegraf",
    "enabled": true,
    "config": {}
  }'
```

**Save the response** - you'll need `one_time_token` and `one_time_agent_id`:

```json
{
  "id": 1,
  "name": "mac-server-telegraf",
  "type": "telegraf",
  "enabled": true,
  "config": {
    "token": "abc123...",
    "agent_id": "uuid-here..."
  },
  "one_time_token": "abc123...",
  "one_time_agent_id": "uuid-here..."
}
```

### 3. Configure Environment Variables

```bash
# Add to ~/.zshrc or ~/.bashrc
export TELEGRAF_TOKEN="your-token-from-step-2"
export TELEGRAF_AGENT_ID="your-agent-id-from-step-2"
export BACKEND_URL="http://localhost:8000"
```

Reload your shell:

```bash
source ~/.zshrc  # or source ~/.bashrc
```

### 4. Install Configuration Files

```bash
# Determine Homebrew prefix (Apple Silicon vs Intel)
BREW_PREFIX=$(brew --prefix)

# Copy main configuration
cp telegraf.conf $BREW_PREFIX/etc/telegraf.conf

# Create scripts directory and copy scripts
mkdir -p $BREW_PREFIX/etc/telegraf/scripts
cp scripts/macos_log.sh $BREW_PREFIX/etc/telegraf/scripts/
chmod +x $BREW_PREFIX/etc/telegraf/scripts/macos_log.sh

# Update script path in config
export TELEGRAF_SCRIPTS_PATH="$BREW_PREFIX/etc/telegraf/scripts"
```

### 5. Test Configuration

```bash
# Validate configuration syntax
telegraf --config $BREW_PREFIX/etc/telegraf.conf --test

# Run in debug mode to see output
telegraf --config $BREW_PREFIX/etc/telegraf.conf --debug
```

### 6. Start Telegraf Service

```bash
# Start as a background service
brew services start telegraf

# Check status
brew services list | grep telegraf

# View logs
tail -f $BREW_PREFIX/var/log/telegraf.log
```

## Verification

### Check Telegraf is Running

```bash
brew services list | grep telegraf
# Should show: telegraf started
```

### Check Backend is Receiving Data

```bash
# Check Redis stream length
redis-cli XLEN logs

# Query recent metrics
curl http://localhost:8000/api/v1/telemetry/metrics?limit=10
```

### Check Agent Statistics

```bash
# View agent stats in Redis
redis-cli HGETALL telegraf:agent:1
```

## Configuration Reference

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAF_TOKEN` | Yes | - | Authentication token from `/api/v1/sources` |
| `TELEGRAF_AGENT_ID` | Yes | - | Agent ID from `/api/v1/sources` |
| `BACKEND_URL` | No | `http://localhost:8000` | Backend API URL |
| `HOSTNAME` | No | System hostname | Override hostname tag |
| `ENVIRONMENT` | No | `development` | Environment tag |
| `REGION` | No | `local` | Region/datacenter tag |
| `COLLECTION_INTERVAL` | No | `10s` | Data collection interval |
| `FLUSH_INTERVAL` | No | `10s` | Data flush interval |
| `TELEGRAF_SCRIPTS_PATH` | No | `/opt/homebrew/etc/telegraf/scripts` | Scripts directory |

### macOS Log Script Options

The `macos_log.sh` script supports environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MACOS_LOG_LOOKBACK` | `10s` | How far back to look for logs |
| `MACOS_LOG_LEVEL` | `default` | Minimum level: debug, info, default, error, fault |
| `MACOS_LOG_MAX_ENTRIES` | `100` | Maximum entries per collection |
| `MACOS_LOG_SUBSYSTEM` | - | Filter by subsystem (e.g., `com.apple`) |
| `MACOS_LOG_PROCESS` | - | Filter by process name |

## Data Flow

```
macOS Server
    │
    ├── Docker containers ──────┐
    │   (docker_log plugin)     │
    │                           │
    ├── macOS system logs ──────┼──► Telegraf ──► HTTP POST ──► Backend API
    │   (exec + macos_log.sh)   │                               │
    │                           │                               ▼
    ├── App log files ──────────┤                         Redis Stream
    │   (tail plugin)           │                               │
    │                           │                               ▼
    └── System metrics ─────────┘                         Consumer
        (cpu, mem, disk, etc.)                              │
                                                            ▼
                                                    ChromaDB / PostgreSQL
```

## Input Plugins

### Docker Container Logs (`docker_log`)

Collects logs from all running Docker containers via the Docker API.

**Configuration:**

```toml
[[inputs.docker_log]]
  endpoint = "unix:///var/run/docker.sock"
  from_beginning = false
  timeout = "5s"
```

**Filtering containers:**

```toml
[[inputs.docker_log]]
  # Include only specific containers
  container_name_include = ["api", "worker", "frontend"]

  # Exclude specific containers
  container_name_exclude = ["redis", "postgres"]
```

### Docker Container Metrics (`docker`)

Collects CPU, memory, network, and I/O metrics from containers.

### macOS System Logs (`exec`)

Runs `macos_log.sh` to collect unified logs from macOS.

### Application Log Files (`tail`)

Tails log files from configured paths:

- `/var/log/*.log`
- `/usr/local/var/log/*.log`
- `/opt/homebrew/var/log/*.log`
- `~/Library/Logs/*.log`

### System Metrics

- `cpu` - CPU usage per-core and total
- `mem` - Memory usage
- `disk` - Disk usage and I/O
- `net` - Network statistics
- `system` - Load averages, uptime

## Troubleshooting

### Telegraf won't start

```bash
# Check configuration syntax
telegraf --config /path/to/telegraf.conf --test

# Check for errors in logs
cat $(brew --prefix)/var/log/telegraf.log
```

### No data reaching backend

1. **Verify credentials:**
   ```bash
   echo $TELEGRAF_TOKEN
   echo $TELEGRAF_AGENT_ID
   ```

2. **Test backend connectivity:**
   ```bash
   curl -X POST $BACKEND_URL/api/v1/telemetry/telegraf \
     -H "Content-Type: application/json" \
     -H "X-Telegraf-Token: $TELEGRAF_TOKEN" \
     -d '{"metrics": []}'
   ```

3. **Run Telegraf in debug mode:**
   ```bash
   telegraf --config telegraf.conf --debug 2>&1 | head -100
   ```

### Docker socket permission denied

Ensure Docker Desktop is running and the socket is accessible:

```bash
ls -la /var/run/docker.sock
# Should show: srw-rw---- ... docker.sock
```

### macOS logs not collecting

1. **Grant Full Disk Access:**
   - System Preferences > Security & Privacy > Privacy > Full Disk Access
   - Add Terminal.app (or your terminal emulator)

2. **Test the script manually:**
   ```bash
   ./scripts/macos_log.sh
   ```

3. **Install jq for better parsing:**
   ```bash
   brew install jq
   ```

## Docker Deployment (Alternative)

For Linux servers or CI environments, you can run Telegraf in Docker:

```bash
# Build image
docker build -t enterprise-log-analyzer/telegraf .

# Run container
docker run -d \
  --name telegraf \
  -e TELEGRAF_TOKEN=$TELEGRAF_TOKEN \
  -e TELEGRAF_AGENT_ID=$TELEGRAF_AGENT_ID \
  -e BACKEND_URL=http://host.docker.internal:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  enterprise-log-analyzer/telegraf
```

**Note:** Docker-based Telegraf on macOS cannot access macOS system logs. Use native Homebrew installation for full functionality.

## Files Reference

| File | Purpose |
|------|---------|
| `telegraf.conf` | Main Telegraf configuration |
| `telegraf.env.example` | Environment variable template |
| `scripts/macos_log.sh` | macOS unified log collector |
| `Dockerfile` | Docker image for Linux/CI |
| `docker-compose.yml` | Docker Compose for local testing |

## Security Considerations

1. **Token Security:** Never commit tokens to version control. Use environment variables.

2. **Docker Socket:** The Docker socket grants significant access. Mount as read-only (`:ro`).

3. **Network Security:** In production, use HTTPS for `BACKEND_URL`.

4. **Log Filtering:** Consider filtering sensitive logs using Telegraf processors.

## Support

For issues related to:

- **Telegraf configuration:** Check [Telegraf documentation](https://docs.influxdata.com/telegraf/)
- **Backend ingestion:** Check `/api/v1/telemetry/telegraf` endpoint logs
- **This configuration:** Open an issue in this repository
