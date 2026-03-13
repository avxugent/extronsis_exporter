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

from .sis_client import DeviceBanner, DeviceMetrics, ExtronSISClient, InputInfo

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


# Regex to parse the input info response:
# "Vid1 Typ0 Amt1 Vmt0 Hrt000.00 Vrt000.00"
_INPUT_INFO_RE = re.compile(
    r"Vid(?P<vid>\d+)\s+"
    r"Typ(?P<typ>\d+)\s+"
    r"Amt(?P<amt>\d+)\s+"
    r"Vmt(?P<vmt>\d+)\s+"
    r"Hrt(?P<hrt>[\d.]+)\s+"
    r"Vrt(?P<vrt>[\d.]+)",
    re.IGNORECASE,
)


def _parse_input_info(raw: str) -> Optional[InputInfo]:
    """
    Parse the response from the ``<n>*I`` SIS command.

    Expected format: ``Vid1 Typ0 Amt1 Vmt0 Hrt000.00 Vrt000.00``
    Returns an :class:`InputInfo` on success, or ``None`` if parsing fails.
    """
    m = _INPUT_INFO_RE.search(raw)
    if not m:
        return None
    return InputInfo(
        selected_input=int(m.group("vid")),
        video_type=int(m.group("typ")),
        audio_muted=int(m.group("amt")) == 1,
        video_muted=int(m.group("vmt")),
        horizontal_freq=float(m.group("hrt")),
        vertical_freq=float(m.group("vrt")),
    )


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
        yield GaugeMetricFamily("extron_input_video_type", "")
        yield GaugeMetricFamily("extron_input_audio_muted", "")
        yield GaugeMetricFamily("extron_input_video_muted", "")
        yield GaugeMetricFamily("extron_input_horizontal_freq_khz", "")
        yield GaugeMetricFamily("extron_input_vertical_freq_hz", "")
        yield GaugeMetricFamily("extron_output_audio_muted", "")
        yield GaugeMetricFamily("extron_output_video_muted", "")
        yield GaugeMetricFamily("extron_temperature_celsius", "")
        yield GaugeMetricFamily("extron_power_mode", "")

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

        # --- extron_input_video_type ---
        vid_type_family = GaugeMetricFamily(
            "extron_input_video_type",
            "Video signal type on the input: 0=No signal, 1=DVI, 2=HDMI, 3=DisplayPort.",
            labels=["device", "host", "input"],
        )
        for dev_cfg, m in all_metrics:
            for input_num, info in m.input_info.items():
                vid_type_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(input_num)],
                    float(info.video_type),
                )
        yield vid_type_family

        # --- extron_input_audio_muted ---
        inp_audio_family = GaugeMetricFamily(
            "extron_input_audio_muted",
            "1 if the input audio is muted, 0 otherwise.",
            labels=["device", "host", "input"],
        )
        for dev_cfg, m in all_metrics:
            for input_num, info in m.input_info.items():
                inp_audio_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(input_num)],
                    1.0 if info.audio_muted else 0.0,
                )
        yield inp_audio_family

        # --- extron_input_video_muted ---
        inp_video_family = GaugeMetricFamily(
            "extron_input_video_muted",
            "Video mute state on the input: 0=Unmuted, 1=Mute to black, 2=Mute video and sync.",
            labels=["device", "host", "input"],
        )
        for dev_cfg, m in all_metrics:
            for input_num, info in m.input_info.items():
                inp_video_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(input_num)],
                    float(info.video_muted),
                )
        yield inp_video_family

        # --- extron_input_horizontal_freq_khz ---
        hfreq_family = GaugeMetricFamily(
            "extron_input_horizontal_freq_khz",
            "Horizontal frequency of the input signal in kHz.",
            labels=["device", "host", "input"],
        )
        for dev_cfg, m in all_metrics:
            for input_num, info in m.input_info.items():
                hfreq_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(input_num)],
                    info.horizontal_freq,
                )
        yield hfreq_family

        # --- extron_input_vertical_freq_hz ---
        vfreq_family = GaugeMetricFamily(
            "extron_input_vertical_freq_hz",
            "Vertical frequency of the input signal in Hz.",
            labels=["device", "host", "input"],
        )
        for dev_cfg, m in all_metrics:
            for input_num, info in m.input_info.items():
                vfreq_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"], str(input_num)],
                    info.vertical_freq,
                )
        yield vfreq_family

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

        # --- extron_power_mode ---
        power_family = GaugeMetricFamily(
            "extron_power_mode",
            (
                "Current power save mode of the device (WPSAV). "
                "0=Full power, 1=Lowest power (TP disabled), "
                "2=Lower power (TP links active), 9=Over-heating."
            ),
            labels=["device", "host"],
        )
        for dev_cfg, m in all_metrics:
            if m.power_mode is not None:
                power_family.add_metric(
                    [dev_cfg["name"], dev_cfg["host"]],
                    float(m.power_mode),
                )
        yield power_family

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

            # --- Per-input general information ---
            for inp in range(1, num_inputs + 1):
                try:
                    raw = client.query_input_info(inp)
                    info = _parse_input_info(raw)
                    if info is not None:
                        metrics.input_info[inp] = info
                    else:
                        logger.warning(
                            "Could not parse input info for %s input %d: %r",
                            dev_cfg["name"], inp, raw,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to query input info for %s input %d: %s",
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

            # --- Power mode ---
            try:
                raw = client.query_power_mode()
                mode = _parse_int_response(raw)
                if mode is not None:
                    metrics.power_mode = mode
                else:
                    logger.warning(
                        "Could not parse power mode for %s: %r",
                        dev_cfg["name"], raw,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to query power mode for %s: %s",
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
