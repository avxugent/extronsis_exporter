# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Prometheus exporter for Extron AV devices (e.g. IN1804 video matrix switch) that speak the
**Extron Simple Instruction Set (SIS)** protocol over SSH (port 22023, not the standard 22).
Pure-Python stdlib HTTP server (`http.server.ThreadingHTTPServer`) — no web framework.

## Commands

```bash
# Install deps (paramiko, PyYAML, prometheus-client)
pip install -r requirements.txt

# Run locally
cp config.example.yaml config.yaml   # edit devices/credentials
python -m extronsis_exporter -c config.yaml

# CLI flags override config file values
python -m extronsis_exporter -c config.yaml --host 0.0.0.0 --port 9877 --log-level DEBUG

# Docker
docker build -t extronsis-exporter .
docker compose up -d
```

There is **no test suite, linter, or type-checker configured** in this repo (no `pytest`, no
`pyproject.toml`, no CI lint step). The only CI job (`.github/workflows/docker.yml`) builds and
pushes a multiarch Docker image to Docker Hub on push to `main`. Verify changes manually by
running the exporter and curling its endpoints (see below), or by exercising the parsing
functions directly since they're pure functions with no I/O.

## Architecture

Three files under `extronsis_exporter/`, each with a single responsibility:

- **`sis_client.py`** — `ExtronSISClient`: raw SSH transport. Opens an *interactive shell*
  channel via paramiko (Extron devices don't support `exec_command`, only a shell), reads the
  two-line login banner on connect (parsed into `DeviceBanner`), then sends SIS command strings
  (e.g. `"1*I"`, `"WPSAV"`) terminated with `\r` and reads the reply. Response framing has no
  explicit terminator — `_recv_until_quiet()` detects end-of-response with a short idle-timeout
  heuristic (`_IDLE_TIMEOUT = 0.2s`) after the first byte arrives, rather than reading a fixed
  number of bytes. Every `send_command()` echoes the sent command back first; that echo line is
  stripped before returning. Dataclasses `DeviceBanner`, `InputInfo`, `DeviceMetrics` are the
  shared data shapes passed up to the collector.

- **`collector.py`** — `ExtronCollector(prometheus_client.registry.Collector)`: turns raw SIS
  responses into Prometheus metric families. `collect()` is called fresh on every scrape (no
  caching) — it opens one new SSH connection per configured device, sequentially queries routing
  per output, per-input info, mute states, temperature, and power mode, and always calls
  `client.close()` in a `finally` block. Per-device failures (`up=False`) do not abort the whole
  scrape — other devices still report. Parsing of raw SIS response strings (regexes for banner,
  routing, input info, temperature, bool-ish mute values) lives at module level in this file, not
  in `sis_client.py`.

- **`__main__.py`** — config loading (`load_config`, YAML + defaults merge), the HTTP handler
  (`_make_handler` closes over the loaded `cfg` dict to build a `BaseHTTPRequestHandler`
  subclass), and `main()`/CLI argument parsing. Routes: `/` (HTML index), `/probe`, `/metrics`,
  `/healthz`.

### The two probing modes (both always active, can combine)

- **Config-file mode**: plain `GET /probe` scrapes every device listed under `devices:` in the
  YAML config.
- **URL-parameter mode**: `GET /probe?host=...&name=...&num_inputs=...` scrapes a single device
  described entirely by query params — no config-file entry needed. This is the intended way to
  drive many devices via Prometheus relabeling (see README "URL-parameter mode" section for the
  `relabel_configs` pattern). `_device_from_query_params()` in `__main__.py` builds the device
  dict; only `host` is required, everything else falls back to `_DEVICE_DEFAULTS`.

If a request supplies `host` as a query param, URL-parameter mode wins for that request even if
`devices:` is also configured — the two modes are per-request, not mutually exclusive globally.

### `/metrics` vs `/probe` — do not confuse these

- `/probe` returns **device** metrics (`extron_*`), built from a fresh `CollectorRegistry` +
  `ExtronCollector` per request.
- `/metrics` returns the **exporter's own** self-metrics (`extronsis_probe_requests_total`,
  `extronsis_probe_duration_seconds`, plus default process/platform collectors) from
  `prometheus_client`'s global `REGISTRY`. It does not touch any Extron device.

This is the reverse of typical `blackbox_exporter`-style naming intuition — double-check which
endpoint a metric belongs on before adding one.

### Adding a new metric

1. Add a query method to `ExtronSISClient` in `sis_client.py` (SIS command string + docstring
   noting the raw response format).
2. Add a parser function at module level in `collector.py` (follow the `_parse_*` naming
   pattern; return `Optional[...]`, `None` on unparseable input — never raise).
3. Add the field to `DeviceMetrics` in `sis_client.py` if it's device-level, or extend
   `InputInfo` if it's per-input.
4. Populate it in `ExtronCollector._scrape_device()` inside its own `try/except` block — one
   failed query must not abort the others (this is why every metric query in that method is
   individually wrapped).
5. Yield a new `GaugeMetricFamily`/`InfoMetricFamily` in `ExtronCollector.collect()`, and add the
   matching empty-family stub in `describe()`.
6. Update the metrics table in `README.md`.

## Configuration

`config.yaml` is gitignored (contains device credentials) — `config.example.yaml` is the
tracked template and also what gets baked into the Docker image as the built-in default
(`/etc/extronsis-exporter/config.yaml`, overridable via `EXTRONSIS_CONFIG` env var or by
bind-mounting over that path). The `devices:` list is optional at every level — an exporter with
zero configured devices is valid and simply requires every `/probe` call to pass `host`.
