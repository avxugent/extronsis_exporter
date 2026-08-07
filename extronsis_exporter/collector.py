"""
Prometheus collector for Extron device web UIs.

Probes one or more devices over HTTPS and exposes connectivity and firmware
version information as Prometheus metrics.
"""

from __future__ import annotations

import logging
from typing import Any, Generator

from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily, Metric

from .prober import ProbeResult, probe_device

logger = logging.getLogger(__name__)


class ExtronCollector:
    """
    A Prometheus custom collector that probes Extron device web UIs.

    Each call to :meth:`collect` probes every configured device fresh (no
    caching) and yields the results.
    """

    def __init__(self, devices: list[dict[str, Any]]) -> None:
        """
        :param devices: List of device configuration dicts. Each dict must
            contain at least ``name`` and ``host``; optional keys are
            ``port``, ``path``, ``timeout``, and ``verify_tls``.
        """
        self._devices = devices

    # ------------------------------------------------------------------
    # prometheus_client collector interface
    # ------------------------------------------------------------------

    def describe(self) -> Generator[Metric, None, None]:
        # Yield empty families so prometheus_client knows what we produce.
        yield GaugeMetricFamily("probe_success", "")
        yield GaugeMetricFamily("probe_duration_seconds", "")
        yield GaugeMetricFamily("probe_http_status_code", "")
        yield InfoMetricFamily("extron_firmware", "")

    def collect(self) -> Generator[Metric, None, None]:
        """Probe all devices and yield Prometheus metric families."""
        results: list[tuple[dict[str, Any], ProbeResult]] = [
            (dev_cfg, self._probe(dev_cfg)) for dev_cfg in self._devices
        ]

        # --- probe_success ---
        success_family = GaugeMetricFamily(
            "probe_success",
            "1 if the device's web UI was reachable and responded successfully, 0 otherwise.",
            labels=["device", "host"],
        )
        for dev_cfg, r in results:
            success_family.add_metric(
                [dev_cfg["name"], dev_cfg["host"]],
                1.0 if r.success else 0.0,
            )
        yield success_family

        # --- probe_duration_seconds ---
        duration_family = GaugeMetricFamily(
            "probe_duration_seconds",
            "Duration of the probe request in seconds.",
            labels=["device", "host"],
        )
        for dev_cfg, r in results:
            duration_family.add_metric(
                [dev_cfg["name"], dev_cfg["host"]],
                r.duration_seconds,
            )
        yield duration_family

        # --- probe_http_status_code ---
        status_family = GaugeMetricFamily(
            "probe_http_status_code",
            "HTTP status code returned by the device (0 if no response was received).",
            labels=["device", "host"],
        )
        for dev_cfg, r in results:
            status_family.add_metric(
                [dev_cfg["name"], dev_cfg["host"]],
                float(r.status_code or 0),
            )
        yield status_family

        # --- extron_firmware ---
        firmware_family = InfoMetricFamily(
            "extron_firmware",
            "Firmware/software version parsed from the device's web UI (div#version).",
            labels=["device", "host"],
        )
        for dev_cfg, r in results:
            if r.version:
                firmware_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"]],
                    {"version": r.version},
                )
        yield firmware_family

    # ------------------------------------------------------------------
    # Device probing
    # ------------------------------------------------------------------

    def _probe(self, dev_cfg: dict[str, Any]) -> ProbeResult:
        """Probe a single device's web UI and return the result."""
        return probe_device(
            host=dev_cfg["host"],
            port=int(dev_cfg.get("port", 443)),
            path=dev_cfg.get("path", "/www/index.html"),
            timeout=float(dev_cfg.get("timeout", 10.0)),
            verify_tls=bool(dev_cfg.get("verify_tls", False)),
        )
