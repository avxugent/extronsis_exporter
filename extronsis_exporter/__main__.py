"""
Entry point for the Extron SIS Prometheus Exporter.

Usage::

    python -m extronsis_exporter [-c config.yaml] [--port 9877] [--host 0.0.0.0]

Or via the installed console script::

    extronsis-exporter [-c config.yaml]

The ``/metrics`` endpoint supports two modes:

**Config-file mode** — scrapes all devices defined in the configuration file:

    GET /metrics

**URL-parameter mode** — scrapes a single device specified entirely via query
parameters; no configuration file entry is required for the target device:

    GET /metrics?host=192.168.1.10&name=room-101&num_inputs=4

Available query parameters:

    host        (required) Hostname or IP address of the device.
    name        Label used in Prometheus metrics. Defaults to the value of host.
    port        SSH port. Default: 22023.
    username    SSH username. Default: admin.
    password    SSH password. Default: empty string.
    timeout     Per-command timeout in seconds. Default: 10.0.
    num_inputs  Number of inputs to query. Default: 8.
    num_outputs Number of outputs to query. Default: 1.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from .collector import ExtronCollector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "listen_host": "0.0.0.0",
    "listen_port": 9877,
    "metrics_path": "/metrics",
    "log_level": "INFO",
    "devices": [],
}

_DEVICE_DEFAULTS: dict[str, Any] = {
    "port": 22023,
    "username": "admin",
    "password": "",
    "timeout": 10.0,
    "num_inputs": 8,
    "num_outputs": 1,
}


def load_config(path: str) -> dict[str, Any]:
    """
    Load and validate the YAML configuration file.

    The ``devices`` list is optional; the exporter can run without any
    pre-configured devices when all targets are supplied via URL parameters.
    Missing top-level keys fall back to :data:`_DEFAULT_CONFIG`.
    """
    cfg = dict(_DEFAULT_CONFIG)

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    cfg.update(data)

    for i, dev in enumerate(cfg["devices"]):
        if "name" not in dev:
            raise ValueError(f"Device #{i} is missing the required 'name' field.")
        if "host" not in dev:
            raise ValueError(f"Device '{dev.get('name', i)}' is missing the required 'host' field.")
        for key, default in _DEVICE_DEFAULTS.items():
            dev.setdefault(key, default)

    return cfg


def _device_from_query_params(query_params: dict[str, list[str]]) -> dict[str, Any]:
    """
    Build a device configuration dict from URL query parameters.

    :raises ValueError: If a parameter value cannot be converted to its expected type.
    """
    host = query_params["host"][0]
    return {
        "host": host,
        "name": query_params.get("name", [host])[0],
        "port": int(query_params.get("port", [_DEVICE_DEFAULTS["port"]])[0]),
        "username": query_params.get("username", [_DEVICE_DEFAULTS["username"]])[0],
        "password": query_params.get("password", [_DEVICE_DEFAULTS["password"]])[0],
        "timeout": float(query_params.get("timeout", [_DEVICE_DEFAULTS["timeout"]])[0]),
        "num_inputs": int(query_params.get("num_inputs", [_DEVICE_DEFAULTS["num_inputs"]])[0]),
        "num_outputs": int(query_params.get("num_outputs", [_DEVICE_DEFAULTS["num_outputs"]])[0]),
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

def _make_handler(cfg: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    """Return a :class:`BaseHTTPRequestHandler` subclass closed over *cfg*."""

    class MetricsHandler(BaseHTTPRequestHandler):

        def do_GET(self) -> None:
            parsed = urlparse(self.path)

            if parsed.path == cfg["metrics_path"]:
                self._handle_metrics(parse_qs(parsed.query))
            elif parsed.path == "/healthz":
                self._handle_healthz()
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Not Found")

        def _handle_metrics(self, query_params: dict[str, list[str]]) -> None:
            # URL-parameter mode: at least `host` must be present.
            if "host" in query_params:
                try:
                    devices = [_device_from_query_params(query_params)]
                except ValueError as exc:
                    self.send_error(400, f"Invalid query parameter: {exc}")
                    return
                logger.debug(
                    "Scraping device from URL parameters: %s (%s:%d)",
                    devices[0]["name"], devices[0]["host"], devices[0]["port"],
                )
            else:
                # Config-file mode: use the static device list from the config.
                if not cfg["devices"]:
                    self.send_error(
                        400,
                        "No devices configured. Either add devices to the config file "
                        "or supply a 'host' query parameter.",
                    )
                    return
                devices = cfg["devices"]
                logger.debug("Scraping %d device(s) from config", len(devices))

            registry = CollectorRegistry(auto_describe=True)
            registry.register(ExtronCollector(devices))

            try:
                output = generate_latest(registry)
            except Exception as exc:
                logger.error("Failed to generate metrics: %s", exc)
                self.send_error(500, "Internal server error")
                return

            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(output)

        def _handle_healthz(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
            logger.debug(
                "%s - %s",
                self.address_string(),
                fmt % args,
            )

    return MetricsHandler


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extronsis-exporter",
        description="Prometheus exporter for Extron SIS devices (SSH, port 22023).",
    )
    parser.add_argument(
        "-c", "--config",
        default=os.environ.get("EXTRONSIS_CONFIG", "config.yaml"),
        metavar="FILE",
        help="Path to the YAML configuration file (default: config.yaml, "
             "env: EXTRONSIS_CONFIG).",
    )
    parser.add_argument(
        "--host",
        default=None,
        metavar="HOST",
        help="Override the HTTP listen host from the config file.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="Override the HTTP listen port from the config file.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="Override the log level from the config file.",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    # --- Load configuration ---
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print(
            f"ERROR: Configuration file not found: {args.config}\n"
            "Copy config.example.yaml to config.yaml and edit it.",
            file=sys.stderr,
        )
        sys.exit(1)
    except (ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: Invalid configuration: {exc}", file=sys.stderr)
        sys.exit(1)

    # CLI overrides
    if args.host is not None:
        cfg["listen_host"] = args.host
    if args.port is not None:
        cfg["listen_port"] = args.port
    if args.log_level is not None:
        cfg["log_level"] = args.log_level

    # --- Logging ---
    logging.basicConfig(
        level=getattr(logging, cfg["log_level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )

    if cfg["devices"]:
        logger.info(
            "Starting extronsis-exporter on %s:%d — %d device(s) pre-configured",
            cfg["listen_host"], cfg["listen_port"], len(cfg["devices"]),
        )
        for dev in cfg["devices"]:
            logger.info(
                "  Device: %s  host=%s:%d  inputs=%d  outputs=%d",
                dev["name"], dev["host"], dev["port"],
                dev["num_inputs"], dev["num_outputs"],
            )
    else:
        logger.info(
            "Starting extronsis-exporter on %s:%d — no pre-configured devices; "
            "targets must be supplied via URL parameters",
            cfg["listen_host"], cfg["listen_port"],
        )

    # --- HTTP server ---
    httpd = ThreadingHTTPServer(
        (cfg["listen_host"], cfg["listen_port"]),
        _make_handler(cfg),
    )
    logger.info(
        "Listening on http://%s:%d%s",
        cfg["listen_host"],
        cfg["listen_port"],
        cfg["metrics_path"],
    )

    # --- Graceful shutdown ---
    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Received signal %d, shutting down…", signum)
        # httpd.shutdown() is safe to call from a signal handler thread
        httpd.shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    httpd.serve_forever()
    logger.info("extronsis-exporter stopped.")


if __name__ == "__main__":
    main()
