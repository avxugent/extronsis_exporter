# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Prometheus exporter for Extron AV devices — video switches, controllers, touch panels, DSPs, and
anything else that exposes an HTTPS web UI showing its firmware/software version in a
`<div id="version">` element. The exporter fetches that page and parses the version out of the
HTML rather than speaking any device-specific control protocol, so it works generically across
Extron's product line instead of being limited to devices supporting one particular protocol.
Pure-Python stdlib HTTP server (`http.server.ThreadingHTTPServer`) — no web framework.

The project was originally built around the Extron **Simple Instruction Set (SIS)** protocol
over SSH, which only worked for a handful of SIS-capable devices (e.g. the IN1804 matrix
switch). It was rewritten to the generic HTTPS/HTML approach so the same exporter can monitor
any device type in the fleet, at the cost of only exposing connectivity + firmware version
rather than rich per-input/output routing state.

## Commands

```bash
# Install deps (PyYAML, prometheus-client)
pip install -r requirements.txt

# Run locally
cp config.example.yaml config.yaml   # edit devices
python -m extronsis_exporter -c config.yaml

# CLI flags override config file values
python -m extronsis_exporter -c config.yaml --host 0.0.0.0 --port 9877 --log-level DEBUG

# Docker
docker build -t extronsis-exporter .
docker compose up -d
```

Tests live in `tests/` (pytest; no network or real devices needed — `HTTPSConnection` and
`probe_device` are stubbed, and `test_http.py` runs the real server on an ephemeral port).
Lint is `ruff` (config in `pyproject.toml`, includes bandit `S` rules). Dev deps are in
`requirements-dev.txt`:

```bash
pip install -r requirements-dev.txt
pytest --cov
ruff check .
```

CI (`.github/workflows/`): `ci.yml` (ruff, pytest on 3.10–3.13, pip-audit, Trivy image scan),
`codeql.yml`, `dependency-review.yml`; `docker.yml` builds and pushes the multiarch image on push
to `main` only after `ci.yml` passes. Dependabot (`.github/dependabot.yml`) covers pip, Docker
and GitHub Actions.

## Architecture

Three files under `extronsis_exporter/`, each with a single responsibility:

- **`prober.py`** — `probe_device()`: opens an `http.client.HTTPSConnection` to the device,
  issues a single `GET` for `path`, and returns a `ProbeResult` (`success`, `status_code`,
  `version`, `duration_seconds`, `error`). `success` is `True` for a 2xx/3xx status, OR if a
  version was extracted from the body regardless of status — some devices (e.g. TLP Pro touch
  panel login pages) return 401 with no `WWW-Authenticate` challenge (form-based auth, not HTTP
  auth) yet still serve the version in the body, and that's a working device, not a failed probe.
  Version extraction itself always runs against the response body regardless of status, since the
  body is what tells us whether the 401 is one of these "device is fine, page just requires login"
  cases. All connectivity failures (DNS, TCP, TLS, HTTP-level) are caught and folded into
  `ProbeResult.error` rather than raised, so the collector can always emit a `probe_success`
  value. TLS verification is opt-in per device (`verify_tls`, default `False`) since most Extron
  devices ship self-signed certs — when disabled, `context.check_hostname = False` and
  `context.verify_mode = ssl.CERT_NONE` are set explicitly rather than skipped, so it's obvious
  at the call site that verification is intentionally off. `_VersionExtractor(HTMLParser)` is a
  small stack-depth-based HTML parser: it starts counting depth on `<div id="version">`, capturing
  all text (including from nested tags) until that div's matching close tag, so the version isn't
  lost if the value is wrapped in a `<span>` or similar. Devices that populate that div
  client-side via JS instead of server-rendering it (again, TLP Pro panels) fall back to a regex
  match on the `var version_number = "...";` literal, which is still present as plain text in the
  served HTML even though the div itself is empty.

- **`collector.py`** — `ExtronCollector(prometheus_client.registry.Collector)`: turns
  `ProbeResult`s into Prometheus metric families. `collect()` is called fresh on every scrape (no
  caching) — it probes every configured device sequentially via `prober.probe_device()`.
  Per-device failures don't abort the whole scrape — every device gets a `probe_success` value
  regardless of whether others failed.

- **`__main__.py`** — config loading (`load_config`, YAML + defaults merge), the HTTP handler
  (`_make_handler` closes over the loaded `cfg` dict to build a `BaseHTTPRequestHandler`
  subclass), and `main()`/CLI argument parsing. Routes: `/` (HTML index), `/probe`, `/metrics`,
  `/healthz`.

### The two probing modes (both always active, can combine)

- **Config-file mode**: plain `GET /probe` scrapes every device listed under `devices:` in the
  YAML config.
- **URL-parameter mode**: `GET /probe?host=...&name=...&path=...` scrapes a single device
  described entirely by query params — no config-file entry needed. This is the intended way to
  drive many devices via Prometheus relabeling (see README "URL-parameter mode" section for the
  `relabel_configs` pattern). `_device_from_query_params()` in `__main__.py` builds the device
  dict; only `host` is required, everything else falls back to `_DEVICE_DEFAULTS`.

If a request supplies `host` as a query param, URL-parameter mode wins for that request even if
`devices:` is also configured — the two modes are per-request, not mutually exclusive globally.

### `/metrics` vs `/probe` — do not confuse these

- `/probe` returns **device** metrics (`probe_*`, `extron_firmware_info`), built from a fresh
  `CollectorRegistry` + `ExtronCollector` per request.
- `/metrics` returns the **exporter's own** self-metrics (`extronsis_probe_requests_total`,
  `extronsis_probe_duration_seconds`, plus default process/platform collectors) from
  `prometheus_client`'s global `REGISTRY`. It does not touch any Extron device.

Note the `probe_*` naming on `/probe` intentionally follows `blackbox_exporter` convention
(`probe_success`, `probe_duration_seconds`, `probe_http_status_code`) since that's the metric
naming users scraping this exporter will already expect.

### Adding a new metric

1. If it requires new data from the device page, extend `ProbeResult` in `prober.py` and
   populate it in `probe_device()`.
2. Yield a new `GaugeMetricFamily`/`InfoMetricFamily` in `ExtronCollector.collect()`, and add the
   matching empty-family stub in `describe()`.
3. Update the metrics table in `README.md`.

Note `InfoMetricFamily(name, ...)` renders as `<name>_info` in the exposition format — the
family is registered as `"extron_firmware"` in `collector.py` but shows up as
`extron_firmware_info` in scrape output.

## Configuration

`config.yaml` is gitignored (may contain internal hostnames) — `config.example.yaml` is the
tracked template and also what gets baked into the Docker image as the built-in default
(`/etc/extronsis-exporter/config.yaml`, overridable via `EXTRONSIS_CONFIG` env var or by
bind-mounting over that path). The `devices:` list is optional at every level — an exporter with
zero configured devices is valid and simply requires every `/probe` call to pass `host`.
