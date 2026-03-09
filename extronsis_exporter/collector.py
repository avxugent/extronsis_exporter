"""
Prometheus collector for Extron SIS devices.

Scrapes one or more Extron devices over SSH and exposes the collected data
as Prometheus metrics.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Generator, Optional

from prometheus_client.core import (
    GaugeMetricFamily,
    InfoMetricFamily,
    Metric,
)

from .sis_client import DeviceBanner, DeviceMetrics, ExtronSISClient

logger = logging.getLogger(__name__)

# Regex to extract a numeric value from a temperature response such as
# "Temp  C  25" or just "25"
_TEMP_RE = re.compile(r"(\d+(?:\.\d+)?)")

# Regex to parse the routing response for a single output:
# "Out01 In02 RGB" or just "2"
_ROUTING_RE = re.compile(r"(?:Out\d+\s+)?In(\d+)", re.IGNORECASE)


def _parse_int_response(raw: str) -> Optional[int]:
    """Return the first integer found in *raw*, or None."""
    m = re.search(r"(\d+)", raw)
    return int(m.group(1)) if m else None


def _parse_bool_response(raw: str) -> Optional[bool]:
    """
    Interpret a SIS boolean response.

    ``1`` / ``Mute`` / ``Lock`` → True
    ``0`` / ``No`` / ``Unmute`` / ``Unlock`` → False
    """
    raw = raw.strip()
    if not raw:
        return None
    v = _parse_int_response(raw)
    if v is not None:
        return v == 1
    lower = raw.lower()
    # Check more-specific (negative) terms first to avoid "unlock" matching "lock"
    if any(k in lower for k in ("unmute", "unlock")):
        return False
    if any(k in lower for k in ("mute", "lock", "yes")):
        return True
    if "no" in lower.split():
        return False
    return None


def _parse_temperature(raw: str) -> Optional[float]:
    """Extract a float temperature value from a SIS response."""
    # Try last numeric token first (most specific)
    tokens = raw.split()
    for token in reversed(tokens):
        try:
            return float(token)
        except ValueError:
            pass
    m = _TEMP_RE.search(raw)
    return float(m.group(1)) if m else None


def _parse_routing(raw: str) -> Optional[int]:
    """
    Parse the input number from a routing query response.

    Handles both ``Out01 In02 RGB`` style and bare ``2`` responses.
    """
    m = _ROUTING_RE.search(raw)
    if m:
        return int(m.group(1))
    # Bare integer
    v = _parse_int_response(raw)
    return v


class ExtronCollector:
    """
    A Prometheus custom collector that scrapes Extron SIS devices.

    Each call to :meth:`collect` opens a fresh SSH connection to every
    configured device, queries all metrics, and yields the results.
    """

    def __init__(self, devices: list[dict[str, Any]]) -> None:
        """
        :param devices: List of device configuration dicts.  Each dict must
            contain at least ``name`` and ``host``; optional keys are
            ``port``, ``username``, ``password``, ``timeout``,
            ``num_inputs``, and ``num_outputs``.
        """
        self._devices = devices

    # ------------------------------------------------------------------
    # prometheus_client collector interface
    # ------------------------------------------------------------------

    def describe(self) -> Generator[Metric, None, None]:
        # Yield empty families so prometheus_client knows what we produce.
        yield GaugeMetricFamily("extron_up", "")
        yield GaugeMetricFamily("extron_scrape_duration_seconds", "")
        yield InfoMetricFamily("extron_device", "")
        yield GaugeMetricFamily("extron_output_current_input", "")
        yield GaugeMetricFamily("extron_input_signal_locked", "")
        yield GaugeMetricFamily("extron_output_audio_muted", "")
        yield GaugeMetricFamily("extron_output_video_muted", "")
        yield GaugeMetricFamily("extron_temperature_celsius", "")

    def collect(self) -> Generator[Metric, None, None]:
        """Scrape all devices and yield Prometheus metric families."""
        # Collect metrics from all devices
        all_metrics: list[tuple[dict[str, Any], DeviceMetrics]] = []
        for dev_cfg in self._devices:
            metrics = self._scrape_device(dev_cfg)
            all_metrics.append((dev_cfg, metrics))

        # --- extron_up ---
        up_family = GaugeMetricFamily(
            "extron_up",
            "1 if the last scrape of the device was successful, 0 otherwise.",
            labels=["device", "host"],
        )
        for dev_cfg, m in all_metrics:
            up_family.add_metric(
                [dev_cfg["name"], dev_cfg["host"]],
                1.0 if m.up else 0.0,
            )
        yield up_family

        # --- extron_scrape_duration_seconds ---
        dur_family = GaugeMetricFamily(
            "extron_scrape_duration_seconds",
            "Duration of the last device scrape in seconds.",
            labels=["device", "host"],
        )
        for dev_cfg, m in all_metrics:
            dur_family.add_metric(
                [dev_cfg["name"], dev_cfg["host"]],
                m.scrape_duration_seconds,
            )
        yield dur_family

        # --- extron_device_info ---
        info_family = InfoMetricFamily(
            "extron_device",
            "Static information about the Extron device from the login banner.",
            labels=["device", "host"],
        )
        for dev_cfg, m in all_metrics:
            if m.up:
                info_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"]],
                    {
                        "model": m.banner.model,
                        "firmware_version": m.banner.firmware_version,
                        "part_number": m.banner.part_number,
                        "copyright_year": m.banner.copyright_year,
                        "device_datetime": m.banner.device_datetime,
                    },
                )
        yield info_family

        # --- extron_output_current_input ---
        input_family = GaugeMetricFamily(
            "extron_output_current_input",
            "The input number currently routed to this output (0 = no input).",
            labels=["device", "host", "output"],
        )
        for dev_cfg, m in all_metrics:
            for output_num, input_num in m.current_inputs.items():
                input_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(output_num)],
                    float(input_num),
                )
        yield input_family

        # --- extron_input_signal_locked ---
        sig_family = GaugeMetricFamily(
            "extron_input_signal_locked",
            "1 if the input has a locked (active) signal, 0 otherwise.",
            labels=["device", "host", "input"],
        )
        for dev_cfg, m in all_metrics:
            for input_num, locked in m.input_signal_locked.items():
                sig_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(input_num)],
                    1.0 if locked else 0.0,
                )
        yield sig_family

        # --- extron_output_audio_muted ---
        audio_family = GaugeMetricFamily(
            "extron_output_audio_muted",
            "1 if the output audio is muted, 0 otherwise.",
            labels=["device", "host", "output"],
        )
        for dev_cfg, m in all_metrics:
            for output_num, muted in m.output_audio_muted.items():
                audio_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(output_num)],
                    1.0 if muted else 0.0,
                )
        yield audio_family

        # --- extron_output_video_muted ---
        video_family = GaugeMetricFamily(
            "extron_output_video_muted",
            "1 if the output video is muted, 0 otherwise.",
            labels=["device", "host", "output"],
        )
        for dev_cfg, m in all_metrics:
            for output_num, muted in m.output_video_muted.items():
                video_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(output_num)],
                    1.0 if muted else 0.0,
                )
        yield video_family

        # --- extron_temperature_celsius ---
        temp_family = GaugeMetricFamily(
            "extron_temperature_celsius",
            "Internal device temperature in degrees Celsius.",
            labels=["device", "host"],
        )
        for dev_cfg, m in all_metrics:
            if m.temperature_celsius is not None:
                temp_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"]],
                    m.temperature_celsius,
                )
        yield temp_family

    # ------------------------------------------------------------------
    # Device scraping
    # ------------------------------------------------------------------

    def _scrape_device(self, dev_cfg: dict[str, Any]) -> DeviceMetrics:
        """Connect to a single device, collect all metrics, and return them."""
        metrics = DeviceMetrics()
        start = time.monotonic()

        num_inputs = int(dev_cfg.get("num_inputs", 8))
        num_outputs = int(dev_cfg.get("num_outputs", 1))

        client = ExtronSISClient(
            host=dev_cfg["host"],
            port=int(dev_cfg.get("port", 22023)),
            username=dev_cfg.get("username", "admin"),
            password=dev_cfg.get("password", ""),
            timeout=float(dev_cfg.get("timeout", 10.0)),
        )

        try:
            banner = client.connect()
            metrics.banner = banner
            metrics.up = True

            # --- Current input routing per output ---
            for out in range(1, num_outputs + 1):
                try:
                    raw = client.query_output_routing(out)
                    inp = _parse_routing(raw)
                    if inp is not None:
                        metrics.current_inputs[out] = inp
                    else:
                        logger.warning(
                            "Could not parse routing for %s output %d: %r",
                            dev_cfg["name"], out, raw,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to query routing for %s output %d: %s",
                        dev_cfg["name"], out, exc,
                    )

            # --- Input signal lock status ---
            for inp in range(1, num_inputs + 1):
                try:
                    raw = client.query_input_signal(inp)
                    locked = _parse_bool_response(raw)
                    if locked is not None:
                        metrics.input_signal_locked[inp] = locked
                    else:
                        logger.warning(
                            "Could not parse signal lock for %s input %d: %r",
                            dev_cfg["name"], inp, raw,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to query signal lock for %s input %d: %s",
                        dev_cfg["name"], inp, exc,
                    )

            # --- Output audio/video mute status ---
            # Audio mute is a global (device-wide) status; query once and apply
            # to all outputs so the metric is consistent with the device state.
            try:
                raw = client.query_audio_mute()
                muted = _parse_bool_response(raw)
                if muted is not None:
                    for out in range(1, num_outputs + 1):
                        metrics.output_audio_muted[out] = muted
                else:
                    logger.warning(
                        "Could not parse audio mute for %s: %r",
                        dev_cfg["name"], raw,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to query audio mute for %s: %s",
                    dev_cfg["name"], exc,
                )

            for out in range(1, num_outputs + 1):
                try:
                    raw = client.query_video_mute(out)
                    muted = _parse_bool_response(raw)
                    if muted is not None:
                        metrics.output_video_muted[out] = muted
                    else:
                        logger.warning(
                            "Could not parse video mute for %s output %d: %r",
                            dev_cfg["name"], out, raw,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to query video mute for %s output %d: %s",
                        dev_cfg["name"], out, exc,
                    )


            # --- Temperature ---
            try:
                raw = client.query_temperature()
                temp = _parse_temperature(raw)
                if temp is not None:
                    metrics.temperature_celsius = temp
                else:
                    logger.debug(
                        "Temperature not available for %s (response: %r)",
                        dev_cfg["name"], raw,
                    )
            except Exception as exc:
                logger.debug(
                    "Failed to query temperature for %s: %s",
                    dev_cfg["name"], exc,
                )

        except Exception as exc:
            logger.error("Failed to scrape device %s: %s", dev_cfg["name"], exc)
            metrics.up = False
            metrics.scrape_error = str(exc)
        finally:
            client.close()
            metrics.scrape_duration_seconds = time.monotonic() - start

        return metrics
