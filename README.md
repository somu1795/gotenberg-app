# Gotenberg Gateway

A robust, production-ready reverse proxy for the [Gotenberg](https://gotenberg.dev/) document conversion API. Handles concurrency control, circuit breaking, and graceful degradation — so you can safely expose Gotenberg to the internet without it crashing under load.

## How It Works

```
Internet ──→ [Caddy/VPS] ──→ [Gateway :9225] ──→ [Gotenberg :9125]
               TLS              │                      │
               reverse       Pure pass-through    Hardened container
               proxy         Admission control    Read-only filesystem
                             Circuit breaker      SSRF deny list
                             Per-IP fairness      Network isolated
```

The gateway acts as a **pure pass-through proxy** — it never decodes or inspects request bodies. All content-level security (SSRF protection, file access) is handled at the Gotenberg container level.

## Features

| Feature | Description |
|---------|-------------|
| 🚦 **Concurrency Control** | Bounded concurrent jobs + wait queue. Serves as many users as possible, tells the rest "try again" |
| 🔄 **Circuit Breaker** | Auto-detects when Gotenberg is failing. Stops sending requests, recovers automatically |
| ⚖️ **Per-IP Fairness** | Max 2 concurrent + 5 queued per IP. One user can't starve others |
| 📋 **Route Whitelisting** | Only allows configured Gotenberg API routes |
| 🌐 **IP Filtering** | Allowlist/blocklist with CIDR support |
| 📏 **Upload Size Limits** | Reject oversized requests before they hit Gotenberg |
| 🔒 **Security Headers** | X-Content-Type-Options, X-Frame-Options, etc. |
| 📊 **Structured Logging** | JSON request logs with UUID tracing |
| ❤️ **Rich Health Checks** | Queue depth, active jobs, circuit state, uptime, Gotenberg status |
| ⚡ **Async** | Built on FastAPI + httpx for non-blocking I/O |
| 🐳 **Hardened Container** | Read-only FS, dropped capabilities, SSRF deny list, network isolation |

## Quick Start

### Production (Docker Compose)

Both gateway and Gotenberg run as Docker containers on an isolated internal network.

```
[Gateway container :9225] ──→ [Gotenberg container :9125]
  Built from Dockerfile          Official image (hardened)
  └── internal Docker network (no internet egress) ──┘
```

**Files used:** `docker-compose.yml` + `Dockerfile`

```bash
./start.sh           # Build and start both containers
./stop.sh            # Stop both containers
docker compose logs -f  # View logs
```

### Development (auto-reload)

Gotenberg runs in Docker, gateway runs on your machine with auto-reload.
The `Dockerfile` and `docker-compose.yml` are NOT used in this mode.

```
localhost ──→ [uvicorn gateway :9225] ──→ [Gotenberg container :9125]
              auto-reloads on save        docker run (in start.sh)
```

```bash
./start.sh --dev     # Start Gotenberg container + uvicorn --reload
./stop.sh --dev      # Stop both
```

### Try It

```bash
# Health check
curl http://localhost:9225/health | python3 -m json.tool

# Convert a URL to PDF
curl -X POST http://localhost:9225/forms/chromium/convert/url \
  --form url=https://example.com -o example.pdf

# Convert HTML to PDF
curl -X POST http://localhost:9225/forms/chromium/convert/html \
  -F "files=@index.html" -o output.pdf
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GATEWAY_PORT` | Gateway listen port | `9225` |
| `GATEWAY_HOST` | Gateway bind host | `0.0.0.0` |
| `GOTENBERG_URL` | Upstream Gotenberg URL | `http://localhost:9125` |
| `GATEWAY_MAX_CONCURRENT` | Max simultaneous Gotenberg jobs | `10` |
| `GATEWAY_MAX_QUEUE` | Max queued requests | `50` |
| `GATEWAY_LOG_LEVEL` | Log level (DEBUG/INFO/WARNING/ERROR) | `INFO` |

### config.yaml

See [config.yaml](config.yaml) for the full configuration reference with comments.

#### Sizing Guide

| Gotenberg RAM | `max_concurrent` | `max_queue` | Approx. throughput |
|---------------|-------------------|-------------|-------------------|
| 2 GB | 3–5 | 20 | ~100/min |
| 4 GB | 5–10 | 50 | ~200/min |
| 6 GB | 10–15 | 100 | ~400/min |
| 8 GB | 15–25 | 200 | ~800/min |
| 40 GB | 50–60 | 300 | ~1200/min |

#### Concurrency Settings

```yaml
concurrency:
  max_concurrent: 10    # Simultaneous Gotenberg jobs
  max_queue: 50         # Waiting room size
  queue_timeout: 60     # Max wait in queue (seconds)
  per_ip_concurrent: 2  # Max concurrent per IP
  per_ip_queue: 5       # Max queued per IP
```

#### Circuit Breaker

```yaml
circuit_breaker:
  failure_threshold: 5   # Failures before opening circuit
  recovery_timeout: 30   # Seconds before allowing probe request
```

#### Restrict IPs

```yaml
security:
  ip_allowlist:
    - "203.0.113.0/24"
  ip_blocklist:
    - "198.51.100.42"
```

## Architecture

### Request Flow

```
Request arrives
  │
  ├── Slot free? ──→ Run immediately
  │
  ├── Queue has room? ──→ Wait in queue (up to 60s)
  │                       └── Timeout → 408 "Timed out, retry"
  │
  └── Queue full? ──→ 503 "Service busy, retry in ~Xs"
```

### Handling Extreme Load & DDoS

If there is a massive spike of **10,000+ simultaneous requests**, here is exactly how the Gateway shields your system:

#### 1. Single-IP DDoS (Script Kiddie / Spam)
If a single malicious IP hammers the server at once:
* They instantly hit their `per_ip_concurrent` limit (default 2) and their `per_ip_queue` limit (default 5).
* The gateway immediately rejects their remaining 9,993 requests with a seamless `503 Service Unavailable`.
* Gotenberg processes their 2 documents oblivious to the spam, while the remaining 8 global capacity slots are **fully accessible and unharmed for genuine users.**

#### 2. Botnet DDoS (Thousands of unique IPs)
If 10,000 unique malicious IPs attack your node simultaneously:
* The gateway allows 10 concurrent slots and 50 queue slots to fill up globally.
* The remaining 9,940 requests are instantly fast-rejected at the reverse proxy layer (yielding `503` + `Retry-After`). 
* Because Python's `asyncio` handles 10,000 connection rejections in milliseconds natively, your server's CPU hardly flinches. Meanwhile, Gotenberg sits comfortably in its container executing exactly 10 PDFs safely without ever crashing, exceeding RAM, or experiencing lag.

#### 3. High Genuine Load (Viral Traffic)
When legitimate workflows spike:
* The queue predictably smooths the load. Up to 50 requests can temporarily wait in line.
* Because the server maintains a tight grip on active workers (10 max), Gotenberg isn't slowing down attempting to memory-swap the workloads; it processes those 10 efficiently, then immediately slides the next queued items in over minutes. 
* Any legitimate client that exceeds the queue length receives a structured `503 Retry-After`, allowing automated upstream services to gracefully back off completely natively without losing data or risking system instability.

### Middleware Stack

```
1. CORS
2. Request Context (assign UUID, extract client IP)
3. Access Logging (structured JSON)
4. IP Filter (allowlist/blocklist)
5. Concurrency Control (semaphore + queue + per-IP fairness)
6. Max Body Size
7. Route Whitelist (block non-Gotenberg paths)
8. Security Headers
9. Proxy → Gotenberg
```

### Security Layers

| Layer | What it prevents | Where |
|-------|-----------------|-------|
| **Gotenberg deny list** | SSRF to internal IPs, cloud metadata | Container |
| **Read-only filesystem** | Persistent modification, backdoors | Container |
| **Dropped capabilities** | Kernel-level exploits | Container |
| **no-new-privileges** | Privilege escalation | Container |
| **Resource limits** | Resource exhaustion DoS | Container |
| **Route whitelist** | Access to non-API paths | Gateway |
| **Per-IP fairness** | Single-user monopolization | Gateway |
| **Circuit breaker** | Cascading failures | Gateway |

## API Endpoints

### Gateway Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Gateway info (capacity, features) |
| GET | `/health` | Health check (jobs, circuit breaker, Gotenberg) |
| GET | `/docs` | Swagger API documentation |

### Proxied Gotenberg Endpoints

| Method | Path | Description |
|--------|------|-------------|
| ALL | `/forms/*` | All native Gotenberg conversion routes (Chromium, LibreOffice, PDFEngines, etc) are transparently and natively supported without hardcoded updates required. |

### Response Codes

| Code | Meaning |
|------|---------|
| `200` | Success |
| `403` | Route not whitelisted or IP blocked |
| `408` | Request timed out waiting in queue |
| `413` | Upload too large |
| `502` | Cannot connect to Gotenberg |
| `503` | Service busy (queue full) or circuit breaker open |
| `504` | Gotenberg timed out |

### Info Endpoint Response (`/`)

```json
{
  "service": "Gotenberg Gateway",
  "version": "2.0.0",
  "status": "running",
  "docs": "/docs",
  "health": "/health",
  "client": {
    "ip": "127.0.0.1",
    "active_jobs": 0,
    "queued_jobs": 0
  },
  "capacity": {
    "max_concurrent": 10,
    "max_queue": 50,
    "per_ip_concurrent": 2,
    "per_ip_queue": 5,
    "active_jobs": 0,
    "queued_jobs": 0
  },
  "features": {
    "circuit_breaker": "enabled",
    "max_upload_size_mb": 5.0
  }
}
```

#### Info Output Explained:
* **`client`**: Displays your detected IP, securely parsed even through Cloudflare/Caddy via headers. It additionally displays your specific current active conversion jobs and how many requests you specifically have waiting in the queue.
* **`capacity`**: Reflects the global capacity of the service. `max_concurrent` is how many concurrent jobs the server processes simultaneously. `per_ip_concurrent` limits how many of those your IP can monopolize. `active_jobs` reflects global instantaneous usage.
* **`features`**: Gateway-level enforcements like `max_upload_size_mb` ensuring your request drops at the proxy level without flooding external memory.

### Health Check Response (`/health`)

```json
{
  "status": "healthy",
  "gateway": {
    "uptime_seconds": 86400,
    "active_jobs": 7,
    "queued_jobs": 3,
    "total_processed": 15234,
    "total_rejected": 42,
    "total_queue_timeouts": 5
  },
  "circuit_breaker": {
    "state": "closed",
    "failure_count": 0,
    "failure_threshold": 5,
    "recovery_timeout_seconds": 30
  },
  "gotenberg": {
    "status": "healthy",
    "status_code": 200
  }
}
```

#### Health Output & Circuit Breaker Explained:
* **`gateway`**: Lifetime statistical insights. **Queue Timeouts** represent requests that waited too long in the queue without getting a slot and were rejected `HTTP 408`. **Total Rejected** is `HTTP 503` dropouts when the queue was simply full.
* **`circuit_breaker`**: Protects Gotenberg from falling into a spiral of death. 
  * If Gotenberg actively crashes or hits RAM limits and consistently times out (`failure_timeout = 5`), the circuit breaker transitions from `"closed"` (healthy flow) to **`"open"`**.
  * When `"open"`, the Gateway immediately rejects all incoming requests with `503 Service Busy` — *without* passing them to Gotenberg — saving Gotenberg from compounding requests while it recovers memory.
  * After `recovery_timeout_seconds` (e.g. 30s), it enters `"half-open"` state, passing exactly 1 request through. If it succeeds, the circuit fully closes back to normal operation. 
* **`gotenberg.status`**: The live result of an internal `HTTP 200` ping from the proxy to the Gotenberg container, proving the upstream engine is natively alive.

## Testing

```bash
# Install dependencies
pip install -r requirements.txt
pip install pytest pytest-asyncio

# Run all tests (73 tests)
python -m pytest tests/ -v

# Run specific modules
python -m pytest tests/test_concurrency.py -v   # Concurrency + circuit breaker
python -m pytest tests/test_gateway.py -v        # Endpoints + middleware
python -m pytest tests/test_config.py -v         # Configuration
python -m pytest tests/test_ssrf.py -v           # Pass-through verification
python -m pytest tests/test_integration.py -v    # E2E (needs Gotenberg)
```

> **Note:** Integration tests require a running Gotenberg container. They auto-skip if Gotenberg is unreachable.

## Production Deployment

### 1. Start the stack

```bash
./start.sh                       # Build + start both containers
docker compose logs -f           # View logs
docker compose logs -f gateway   # Gateway logs only
./stop.sh                        # Stop
```

### 2. Reverse proxy with Caddy (TLS termination)

Add to your Caddyfile on the VPS:

```
pdf.yourdomain.com {
    reverse_proxy your-tailscale-ip:9225 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        transport http {
            read_timeout 120s
            write_timeout 120s
        }
    }
}
```

Caddy automatically provisions and renews TLS certificates via Let's Encrypt.

> **Why Caddy over HAProxy/Nginx?** Your gateway already handles all the "smart" logic (concurrency, circuit breaking, fairness). The reverse proxy only needs to terminate TLS and forward bytes. Caddy does this with auto-TLS and zero config files to manage. HAProxy's advantages (TCP-level balancing, 100k+ connections) aren't relevant for a document conversion API.

## Project Structure

```
gotenberg-app/
├── main.py                      # App factory, middleware stack, endpoints
├── proxy.py                     # Reverse proxy + circuit breaker integration
├── config.py                    # Configuration loader (YAML + env vars)
├── config.yaml                  # Default configuration
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Gateway Docker image (used by docker-compose)
├── docker-compose.yml           # Full stack (gateway + Gotenberg)
├── start.sh                     # Start script (compose or dev mode)
├── stop.sh                      # Stop script
├── middleware/
│   ├── concurrency.py           # Semaphore + queue + per-IP fairness
│   ├── circuit_breaker.py       # Circuit breaker (closed/open/half-open)
│   ├── security.py              # Route whitelist, IP filter, headers, body size
│   └── logging.py               # Request ID, access logging
└── tests/
    ├── conftest.py              # Test fixtures
    ├── test_concurrency.py      # Concurrency + circuit breaker tests
    ├── test_gateway.py          # Endpoint + middleware tests
    ├── test_config.py           # Configuration tests
    ├── test_ssrf.py             # Pass-through verification
    └── test_integration.py      # E2E with real Gotenberg
```

## License

MIT
