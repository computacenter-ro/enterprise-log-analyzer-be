# Data Ingestion Sources Research

Research on how each data source produces data, what format it comes in, and how it could be integrated into an AIOps log analyzer application.

---

## 1. Telegraf

### What it produces
- **Logs**: Docker container stdout/stderr, file tails, syslog, macOS unified logs
- **Metrics**: CPU, memory, disk, network, Docker container stats, SNMP, custom inputs
- Each data point has: `name`, `tags` (key-value labels), `fields` (numeric/string values), `timestamp`

### How to get data
- **Push model** — Telegraf pushes to us. We expose an HTTP endpoint, Telegraf's `outputs.http` POSTs JSON batches every N seconds
- No API to poll — Telegraf is the agent, we're the receiver

### Data format

Telegraf's native format is **InfluxDB Line Protocol** (one metric per line):
```
cpu,host=server1,cpu=cpu0 usage_idle=92.3,usage_user=5.1 1707500000000000000
docker_log,container_name=webapp,container_image=nginx message="GET /health 200 OK" 1707500001000000000
```

When using `outputs.http` with `data_format = "json"`, Telegraf serializes as a **JSON array** (no wrapper object):
```json
[
  {
    "fields": {"usage_idle": 92.3, "usage_user": 5.1},
    "name": "cpu",
    "tags": {"host": "server1", "cpu": "cpu0"},
    "timestamp": 1707500000
  },
  {
    "fields": {"message": "GET /health 200 OK"},
    "name": "docker_log",
    "tags": {"container_name": "webapp", "container_image": "nginx"},
    "timestamp": 1707500001
  }
]
```

> **Note**: Our backend currently wraps this in `{"metrics": [...]}` via the `TelegrafBatch` schema, but the raw Telegraf output is a plain array.

---

## 2. Datadog

### What it produces
- **Logs**: Application logs with severity, service, tags, attributes
- **Metrics**: Infrastructure + custom metrics as time-series (gauge, count, rate, distribution)
- **Events**: Deployment markers, alerts, custom events
- **Traces**: Distributed tracing spans (APM)

### How to get data
- **Pull** — Query the Logs Search API (`POST /api/v2/logs/events/search`) with time windows, paginate through results
- **Pull** — Query Metrics API (`GET /api/v1/query`) with metric queries and time ranges
- **Push** (alternative) — Datadog can forward logs/events to webhooks

### Logs Search API response
```json
{
  "data": [
    {
      "id": "AgAAAY...",
      "type": "log",
      "attributes": {
        "message": "Connection refused to database host db-primary:5432",
        "status": "error",
        "service": "payment-api",
        "tags": ["env:production", "team:payments"],
        "timestamp": "2026-02-09T14:30:00.000Z",
        "host": "web-prod-03",
        "attributes": {
          "http.method": "POST",
          "http.status_code": 500
        }
      }
    }
  ],
  "links": { "next": "https://..." }
}
```

### Metrics API response (`GET /api/v1/query?query=system.cpu.user{*}&from=...&to=...`)
```json
{
  "status": "ok",
  "query": "system.cpu.user{*}",
  "series": [
    {
      "metric": "system.cpu.user",
      "display_name": "system.cpu.user",
      "type": "gauge",
      "interval": 20,
      "pointlist": [
        [1707500000000, 45.2],
        [1707500020000, 47.8]
      ],
      "scope": "host:web-prod-03,env:production",
      "tag_set": ["host:web-prod-03", "env:production"],
      "unit": [{"name": "percent", "scale_factor": 1.0}, null]
    }
  ],
  "from_date": 1707500000000,
  "to_date": 1707500060000
}
```

> **Note**: Points are in `pointlist` as `[timestamp_ms, value]` arrays, not objects. Tags use `tag_set` (array of `key:value` strings) and `scope` (comma-separated string).

### Authentication
Two keys — `DD-API-KEY` (data submission) + `DD-APPLICATION-KEY` (data retrieval). Both sent as HTTP headers.

---

## 3. Splunk

### What it produces
- **Logs/Events**: Any machine data — application logs, security events, transactions
- **Metrics**: Time-series metrics via Metrics indexes
- **Alerts**: Search-based alert results

### How to get data — two directions

#### A) Pull from Splunk (Search/Export API)
- Create a search job: `POST /services/search/jobs` with an SPL query
- Stream results: `GET /services/search/jobs/export` — streaming JSON lines, one result per line
- Authentication: `Authorization: Bearer <token>` or `Authorization: Splunk <session-key>`

**Search export response (JSON lines, streamed):**
```json
{"preview":false,"offset":0,"result":{"_time":"2026-02-09T14:30:00","_raw":"ERROR: Connection timeout to db-primary","host":"web-03","source":"/var/log/app.log","sourcetype":"app_logs","severity":"error"}}
{"preview":false,"offset":1,"result":{"_time":"2026-02-09T14:30:01","_raw":"WARN: Retry attempt 2/3","host":"web-03","source":"/var/log/app.log","sourcetype":"app_logs","severity":"warn"}}
```

> **Note**: Each line includes `preview` (boolean, true during partial results) and `offset` (result index) alongside `result`.

#### B) Receive from Splunk (HEC - HTTP Event Collector)
- Splunk (or any tool) pushes events to our endpoint in HEC format
- We act as an HEC-compatible receiver
- This is how many tools already export to Splunk — we could intercept this

**HEC event format (what tools push):**
```json
{
  "time": 1707500000,
  "host": "web-03",
  "source": "/var/log/app.log",
  "sourcetype": "app_logs",
  "index": "main",
  "event": {
    "message": "Connection timeout to db-primary",
    "severity": "ERROR",
    "code": 500
  }
}
```

---

## 4. ThousandEyes (Cisco)

### What it produces
- **Alerts**: Real-time notifications for network/web test failures (with severity, state, rule name)
- **Test Results**: Latency, packet loss, HTTP response time, DNS resolution, BGP routing
- **Network Path Data**: Hop-by-hop trace visualization

### How to get data
- **Pull** — REST API v7 (`GET /v7/alerts` for active alerts, `/v7/test-results/{testId}` for results)
- **Push** — Custom webhooks with Handlebars-templated payloads, POSTed on alert trigger/clear

### Alerts API response (`GET /v7/alerts`)
```json
{
  "alerts": [
    {
      "alertId": "550e8400-...",
      "alertType": "http-server",
      "state": "trigger",
      "severity": "major",
      "testId": "12345",
      "testName": "Production API Monitor",
      "ruleName": "High Response Time",
      "dateStart": "2026-02-09T14:30:00Z",
      "dateEnd": null,
      "violationCount": 3,
      "agents": [{"agentId": "1234", "agentName": "London", "active": true}],
      "_links": {"self": {"href": "https://api.thousandeyes.com/v7/alerts/550e8400-..."}}
    }
  ],
  "_links": {
    "self": {"href": "https://api.thousandeyes.com/v7/alerts"}
  }
}
```

### Test Results response (`GET /v7/test-results/{testId}/http-server`)
```json
{
  "results": [
    {
      "agentId": "1234",
      "agentName": "London",
      "roundId": 1707500000,
      "testId": "12345",
      "serverIp": "203.0.113.10",
      "responseTime": 1250,
      "totalTime": 1450,
      "dnsTime": 45,
      "connectTime": 120,
      "sslTime": 85,
      "waitTime": 950,
      "errorType": "Connect",
      "responseCode": 0,
      "date": "2026-02-09T14:30:00Z"
    }
  ],
  "_links": {
    "self": {"href": "https://api.thousandeyes.com/v7/test-results/12345/http-server"}
  }
}
```

### Authentication
OAuth 2.0 Bearer token (`Authorization: Bearer <token>`).

---

## 5. SNMP (Simple Network Management Protocol)

### What it produces
- **Metrics**: Device CPU, memory, interface traffic (bytes in/out), error counters, uptime
- **Status**: Interface up/down, operational state
- **Traps**: Asynchronous events (link down, auth failure, threshold exceeded)

### How to get data — two methods

#### A) Polling (Pull)
- Send `GET`/`GETBULK` requests to device on UDP port 161
- Query specific OIDs (Object Identifiers) — hierarchical numeric addresses
- Returns typed values: integers, strings, counters, gauges
- **Wire format is binary (BER/ASN.1 encoded)** — not JSON or text

**Common OIDs:**
```
1.3.6.1.2.1.1.1.0     -> sysDescr       ("Cisco IOS XE, Version 17.3")
1.3.6.1.2.1.1.3.0     -> sysUpTime      (234567800 hundredths-of-second)
1.3.6.1.2.1.2.2.1.8.1 -> ifOperStatus   interface 1 (1=up, 2=down)
1.3.6.1.2.1.2.2.1.10.1-> ifInOctets     interface 1 (bytes received)
1.3.6.1.4.1.2021.11.9.0 -> CPU user %   (Net-SNMP)
1.3.6.1.4.1.2021.4.6.0  -> Available RAM (Net-SNMP)
```

**Actual wire format** — binary ASN.1/BER packet (hex dump):
```
30 82 00 3f 02 01 01 04 06 70 75 62 6c 69 63 a2
82 00 30 02 04 01 ...
```

**What an SNMP library (pysnmp, gosnmp, net-snmp) returns after decoding:**
```
OID: 1.3.6.1.2.1.2.2.1.10.1  Type: Counter64  Value: 98234567890
OID: 1.3.6.1.2.1.2.2.1.8.1   Type: Integer    Value: 1
```

**Normalized to JSON by our receiver:**
```json
{"oid": "1.3.6.1.2.1.2.2.1.10.1", "type": "Counter64", "value": 98234567890}
{"oid": "1.3.6.1.2.1.2.2.1.8.1",  "type": "Integer",   "value": 1}
```

#### B) Traps (Push)
- Device sends unsolicited UDP packets to a trap receiver on port 162
- Triggered by events: link down, temperature threshold, auth failure
- SNMPv2 INFORM variant requires acknowledgment (more reliable)
- **Wire format is binary (BER/ASN.1)** — same as polling responses

**What the trap receiver decodes from the binary packet:**
```
Source: 192.168.1.1
Trap OID: 1.3.6.1.6.3.1.1.5.3 (linkDown)
Varbind[0]: 1.3.6.1.2.1.2.2.1.1 = 3 (ifIndex)
Varbind[1]: 1.3.6.1.2.1.2.2.1.8 = 2 (ifOperStatus = down)
Uptime: 1234567 (hundredths of second)
```

**Normalized to JSON by our trap receiver:**
```json
{
  "source": "192.168.1.1",
  "trap_oid": "1.3.6.1.6.3.1.1.5.3",
  "trap_name": "linkDown",
  "varbinds": [
    {"oid": "1.3.6.1.2.1.2.2.1.1", "value": 3},
    {"oid": "1.3.6.1.2.1.2.2.1.8", "value": 2}
  ],
  "timestamp": 1707500000
}
```

### Authentication
SNMPv1/v2c uses community strings (plaintext passwords like `"public"`). SNMPv3 adds proper auth (SHA) + encryption (AES) with per-user credentials.

---

## 6. Generic HTTP Polling (DCIM/CMDB: NetBox, Device42)

### What it produces
- **Inventory data**: Devices, racks, sites, IP addresses, VLANs
- **Relationships**: Dependencies between infrastructure components
- **Change events**: What was modified and when

### How to get data
- **Pull** — Standard REST APIs with pagination, filtering, and sorting
- **Push** (NetBox) — Webhooks on create/update/delete events

### NetBox response (`GET /api/dcim/devices/`)
```json
{
  "count": 150,
  "next": "https://netbox/api/dcim/devices/?limit=50&offset=50",
  "results": [
    {
      "id": 123,
      "name": "switch01",
      "device_type": {"manufacturer": {"name": "Cisco"}, "model": "Catalyst 9300"},
      "site": {"name": "NYC-DC1"},
      "status": {"value": "active"},
      "primary_ip4": {"address": "192.168.1.10/24"},
      "serial": "ABC123456",
      "last_updated": "2026-02-09T10:30:00Z"
    }
  ]
}
```

### Device42 response (`GET /api/1.0/devices/`)
```json
{
  "Devices": [
    {
      "device_id": 789,
      "name": "prod-web-01",
      "os": "Ubuntu 22.04",
      "manufacturer": "Dell",
      "hardware": "PowerEdge R740",
      "ip_addresses": [{"ip": "10.0.1.50", "label": "eth0"}],
      "service_level": "production",
      "last_updated": "2026-02-09T10:30:00Z"
    }
  ]
}
```

### Authentication
NetBox uses Bearer tokens (`Authorization: Bearer nbt_xxx`). Device42 uses API keys or Basic auth.

---

## Summary Matrix

| Source | Logs | Metrics | Alerts | Push | Pull | Real-time? |
|--------|:----:|:-------:|:------:|:----:|:----:|:----------:|
| Telegraf | Yes | Yes | - | HTTP/Kafka/InfluxDB | - | Yes |
| Datadog | Yes | Yes | Yes | Webhooks | REST API | Both |
| Splunk | Yes | Yes | Yes | HEC | Search/Export API | Both |
| ThousandEyes | - | Yes | Yes | Webhooks | REST API | Both |
| SNMP | - | Yes | Traps | Traps (UDP 162) | GET/GETBULK (UDP 161) | Both |
| NetBox/Device42 | - | - | - | Webhooks | REST API | Enrichment only |
