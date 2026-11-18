# extronsis-exporter

[![CI](https://github.com/avxugent/extronsis_exporter/actions/workflows/ci.yml/badge.svg)](https://github.com/avxugent/extronsis_exporter/actions/workflows/ci.yml)
[![CodeQL](https://github.com/avxugent/extronsis_exporter/actions/workflows/codeql.yml/badge.svg)](https://github.com/avxugent/extronsis_exporter/actions/workflows/codeql.yml)
[![Docker build](https://github.com/avxugent/extronsis_exporter/actions/workflows/docker.yml/badge.svg)](https://github.com/avxugent/extronsis_exporter/actions/workflows/docker.yml)
[![Dependabot](https://img.shields.io/badge/dependabot-enabled-025e8c?logo=dependabot)](https://github.com/avxugent/extronsis_exporter/security/dependabot)
[![Container image](https://img.shields.io/badge/ghcr.io-avxugent%2Fextron--exporter-2496ed?logo=docker&logoColor=white)](https://github.com/avxugent/extronsis_exporter/pkgs/container/extron-exporter)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![Code style: ruff](https://img.shields.io/badge/lint-ruff-261230?logo=ruff)

A [Prometheus](https://prometheus.io/) exporter for [Extron](https://www.extron.com/) AV devices — video switches, controllers, touch panels, DSPs, and anything else that exposes a web UI over HTTPS with a firmware/software version shown in a `<div id="version">` element.

Rather than speaking a device-specific control protocol, the exporter fetches the device's own status page over HTTPS and parses the version out of the HTML. This makes it work generically across Extron's product line instead of being limited to devices that support a particular protocol.

---

## Features

- Probes any device exposing an HTTPS web UI with a `<div id="version">` element
- Exposes basic connectivity metrics: reachability, request duration, HTTP status code
- Exposes the firmware/software version parsed from the page
- Tolerates self-signed certificates (TLS verification is opt-in per device)
- **Supports URL-parameter scraping** — target any device at scrape time without touching the config file
- Graceful shutdown on `SIGINT` / `SIGTERM`
- Docker image included

---

## Exposed Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `probe_success` | Gauge | `device`, `host` | `1` if the device's web UI was reachable and responded successfully, `0` otherwise |
| `probe_duration_seconds` | Gauge | `device`, `host` | Duration of the probe request in seconds |
| `probe_http_status_code` | Gauge | `device`, `host` | HTTP status code returned by the device (`0` if no response was received) |
| `extron_firmware_info` | Info | `device`, `host`, `version` | Firmware/software version parsed from `<div id="version">` (omitted if not found) |

### Example output

```
# HELP probe_success 1 if the device's web UI was reachable and responded successfully, 0 otherwise.
# TYPE probe_success gauge
probe_success{device="switch-room-101",host="192.168.1.10"} 1.0

# HELP probe_duration_seconds Duration of the probe request in seconds.
# TYPE probe_duration_seconds gauge
probe_duration_seconds{device="switch-room-101",host="192.168.1.10"} 0.184

# HELP probe_http_status_code HTTP status code returned by the device (0 if no response was received).
# TYPE probe_http_status_code gauge
probe_http_status_code{device="switch-room-101",host="192.168.1.10"} 200.0

# HELP extron_firmware_info Firmware/software version parsed from the device's web UI (div#version).
# TYPE extron_firmware_info gauge
extron_firmware_info{device="switch-room-101",host="192.168.1.10",version="1.08"} 1.0
```

---

## Requirements

- Python 3.10+
- Network access to the device's HTTPS web UI

### Python dependencies

```
prometheus-client>=0.20.0
PyYAML>=6.0.1
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
# Edit config.yaml — the devices list is optional (see Scraping Modes below)
```

### 3. Run

```bash
python -m extronsis_exporter -c config.yaml
```

Metrics are available at `http://localhost:9877/probe`.

---

## Docker

### Build and run with Docker Compose

```bash
cp config.example.yaml config.yaml
# Edit config.yaml
docker compose up -d
```

### Build and run manually

```bash
docker build -t extronsis-exporter .
docker run -d \
  -p 9877:9877 \
  -v $(pwd)/config.yaml:/etc/extronsis-exporter/config.yaml:ro \
  extronsis-exporter
```

### Build and push a multiarch release

```bash
./build-and-push.sh            # tags :latest and :<git-short-sha>
./build-and-push.sh v1.2.0     # also tags :v1.2.0
```

Builds `linux/amd64` and `linux/arm64` and pushes to `ghcr.io/avxugent/extron-exporter`
on the GitHub Container Registry. Requires `docker login ghcr.io` (a personal access token with
`write:packages`) for a user with push access. The same image/tags are also
built and pushed automatically by `.github/workflows/docker.yml` on every push to `main`.

---

## Endpoints

| Path | Description |
|------|-------------|
| `GET /probe` | Probe Extron device(s) and return their metrics |
| `GET /metrics` | Exporter self-metrics (process info, request counters, probe duration histogram) |
| `GET /healthz` | Liveness check — always returns `200 OK` |

## Probing Modes

The `/probe` endpoint supports two modes, which can be used independently or together.

### Config-file mode

Devices are listed in `config.yaml`. A plain `GET /probe` scrapes all of them.

```yaml
devices:
  - name: switch-room-101
    host: 192.168.1.10
```

Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: extron_probe
    metrics_path: /probe
    static_configs:
      - targets: ["localhost:9877"]

  - job_name: extron_exporter
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:9877"]
```

### URL-parameter mode

The target device is specified entirely via query parameters on each scrape
request. No config-file entry is required for the target device. This is the
recommended approach when managing many devices through Prometheus relabeling.

```
GET /probe?host=192.168.1.10&name=room-101
```

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `host` | **yes** | — | Hostname or IP address of the device |
| `name` | no | value of `host` | Label used in all Prometheus metrics |
| `port` | no | `443` | HTTPS port |
| `path` | no | `/www/index.html` | Path of the page to fetch |
| `timeout` | no | `10.0` | Request timeout in seconds |
| `verify_tls` | no | `false` | Verify the device's TLS certificate |

Prometheus scrape config using `params` and relabeling:

```yaml
scrape_configs:
  - job_name: extron_probe
    metrics_path: /probe
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_host
      - source_labels: [__param_host]
        target_label: instance
      - target_label: __address__
        replacement: localhost:9877   # address of the exporter
    static_configs:
      - targets:
          - 192.168.1.10   # Extron device 1
          - 192.168.1.11   # Extron device 2

  - job_name: extron_exporter
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:9877"]
```

---

## Configuration Reference

```yaml
# HTTP server
listen_host: "0.0.0.0"   # Interface to listen on
listen_port: 9877          # Port to listen on

# Access control (all optional, see "Securing the exporter")
# allowed_clients: [10.1.2.3, 10.1.0.0/24]
# auth: {username: prometheus, password: change-me}
# allowed_hosts: ['^switch\d+\.example\.org$']
# allowed_ports: [443]

# Logging
log_level: INFO            # DEBUG | INFO | WARNING | ERROR | CRITICAL

# Devices
devices:
  - name: switch-room-101  # Unique name used as a Prometheus label
    host: 192.168.1.10     # Hostname or IP address
    port: 443               # HTTPS port
    path: /www/index.html   # Path of the page showing #version
    timeout: 10.0            # Request timeout in seconds
    verify_tls: false        # Most Extron devices use self-signed certs
```

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EXTRONSIS_CONFIG` | Path to the configuration file | `config.yaml` |
| `EXTRONSIS_ALLOWED_CLIENTS` | Comma-separated IPs/CIDRs allowed to call the endpoints (overrides `allowed_clients`) | — |
| `EXTRONSIS_AUTH_USERNAME` / `EXTRONSIS_AUTH_PASSWORD` | HTTP Basic auth credentials (override `auth`) | — |

### CLI flags

```
usage: extronsis-exporter [-h] [-c FILE] [--host HOST] [--port PORT] [--log-level LEVEL]

options:
  -c, --config FILE     Path to the YAML configuration file
  --host HOST           Override the HTTP listen host
  --port PORT           Override the HTTP listen port
  --log-level LEVEL     Override the log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
```

---

## Securing the exporter

By default `/`, `/probe` and `/metrics` are open and `/probe?host=...` can target any host and
path, so restrict access to the port (9877) to your Prometheus server(s). Three optional,
independent controls are built in; all default to off and log a warning at startup. `/healthz` is
never restricted.

**Client IP allowlist** — only listed source IPs/CIDRs may call `/`, `/probe` and `/metrics`
(others get `403`). The `EXTRONSIS_ALLOWED_CLIENTS` env var (comma-separated) overrides the file.
Behind a reverse proxy the exporter only sees the proxy's address, so enforce the ACL at the proxy
there.

```yaml
allowed_clients:
  - 10.1.2.3          # Prometheus server
  - 10.1.0.0/24
```

**HTTP Basic auth** — set `auth.username`/`auth.password`, or the `EXTRONSIS_AUTH_USERNAME` /
`EXTRONSIS_AUTH_PASSWORD` env vars (these override the file; setting only one fails at startup).
The exporter serves plain HTTP, so run it behind a TLS-terminating proxy.

```yaml
# prometheus.yml
scrape_configs:
  - job_name: extron
    metrics_path: /probe
    basic_auth:
      username: prometheus
      password_file: /etc/prometheus/extron.pass
```

**URL-mode target allowlist** — `allowed_hosts` (regexes, fullmatched against the literal `host`,
no DNS) and `allowed_ports` (default `[443]`) restrict what `/probe?host=...` may contact; other
targets get `403` and are never contacted. Devices under `devices:` are not checked.

```yaml
allowed_hosts:
  - '^switch\d+\.example\.org$'
```

The client allowlist is checked first (`403`), then Basic auth (`401`).

---

## How probing works

The exporter opens an HTTPS connection to the device, issues a `GET` for
`path` (default `/`), and:

- Considers the probe successful (`probe_success=1`) if the response status
  is in the 2xx/3xx range.
- Parses the response body for the first `<div id="version">` element and
  reports its text content via `extron_firmware_info`. If no such element is
  found, the info metric is simply omitted for that scrape.
- By default does **not** verify the device's TLS certificate, since most
  Extron devices use self-signed certificates out of the box. Set
  `verify_tls: true` per device to enforce verification.

---

## License

MIT

---

## Development

```bash
pip install -r requirements-dev.txt
pytest --cov          # tests (no network or devices needed)
ruff check .          # lint + static security checks (bandit rules)
pip-audit -r requirements.txt   # known-vulnerable dependencies
```

### CI and security checks

| Check | Where | What it covers |
|-------|-------|----------------|
| Lint | `ci.yml` | `ruff` (incl. bandit `S` rules) |
| Tests | `ci.yml` | `pytest` on Python 3.10 – 3.15 |
| `pip-audit` | `ci.yml` | Known CVEs in `requirements.txt` |
| Trivy | `ci.yml` | HIGH/CRITICAL fixable CVEs in the Docker image (base image + deps) |
| CodeQL | `codeql.yml` | Static analysis (`security-extended`) |
| Dependency review | `dependency-review.yml` | Blocks PRs that add high-severity vulnerable dependencies |
| Smoke test | `ci.yml` | Real process: auth, allowlists, clean SIGTERM (`scripts/smoke-test.sh`) |
| Dependabot | `dependabot.yml` | Weekly updates for pip, the Docker base image and GitHub Actions |

`ci.yml` also runs weekly, so newly disclosed CVEs surface without a push. The Docker image is
only published if CI passes.
