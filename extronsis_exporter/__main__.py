"""
Entry point for the Extron SIS Prometheus Exporter.

Usage::

    python -m extronsis_exporter [-c config.yaml] [--port 9877] [--host 0.0.0.0]

Or via the installed console script::

    extronsis-exporter [-c config.yaml]
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from typing import Any

import yaml
from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import CollectorRegistry

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


def load_config(path: str) -> dict[str, Any]:
    """
    Load and validate the YAML configuration file.

    Missing top-level keys fall back to :data:`_DEFAULT_CONFIG`.
    """
    cfg = dict(_DEFAULT_CONFIG)

    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    cfg.update(data)

    if not cfg["devices"]:
        raise ValueError("Configuration must define at least one device under 'devices'.")

    for i, dev in enumerate(cfg["devices"]):
        if "name" not in dev:
            raise ValueError(f"Device #{i} is missing the required 'name' field.")
        if "host" not in dev:
            raise ValueError(f"Device '{dev.get('name', i)}' is missing the required 'host' field.")
        # Apply per-device defaults
        dev.setdefault("port", 22023)
        dev.setdefault("username", "admin")
        dev.setdefault("password", "")
        dev.setdefault("timeout", 10.0)
        dev.setdefault("num_inputs", 8)
        dev.setdefault("num_outputs", 1)

    return cfg


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

    logger.info(
        "Starting extronsis-exporter on %s:%d with %d device(s)",
        cfg["listen_host"],
        cfg["listen_port"],
        len(cfg["devices"]),
    )
    for dev in cfg["devices"]:
        logger.info(
            "  Device: %s  host=%s:%d  inputs=%d  outputs=%d",
            dev["name"],
            dev["host"],
            dev["port"],
            dev["num_inputs"],
            dev["num_outputs"],
        )

    # --- Prometheus registry ---
    # Use a custom registry so we don't expose default Python process metrics
    # unless the user explicitly wants them.
    registry = CollectorRegistry(auto_describe=True)
    collector = ExtronCollector(cfg["devices"])
    registry.register(collector)

    # --- HTTP server ---
    start_http_server(
        port=cfg["listen_port"],
        addr=cfg["listen_host"],
        registry=registry,
    )
    logger.info(
        "Metrics available at http://%s:%d%s",
        cfg["listen_host"],
        cfg["listen_port"],
        cfg["metrics_path"],
    )

    # --- Graceful shutdown ---
    _running = True

    def _handle_signal(signum: int, _frame: Any) -> None:
        nonlocal _running
        logger.info("Received signal %d, shutting down…", signum)
        _running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while _running:
        time.sleep(1)

    logger.info("extronsis-exporter stopped.")


if __name__ == "__main__":
    main()
