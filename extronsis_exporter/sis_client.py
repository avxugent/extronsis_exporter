"""
Extron SIS SSH client.

Connects to an Extron device over SSH on port 22023, reads the login banner,
and provides methods to send SIS commands and parse responses.

Example banner emitted by the device upon login:
    (c) Copyright 2023, Extron Electronics, IN1804 DO, V1.08, 60-1699-13
    Mon, 09 Mar 2026 12:08:14
"""

from __future__ import annotations

import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Optional

import paramiko

logger = logging.getLogger(__name__)

# Regex to parse the Extron login banner line 1:
# (c) Copyright <year>, Extron Electronics, <model>, V<version>, <part_number>
_BANNER_RE = re.compile(
    r"\(c\)\s+Copyright\s+(?P<year>\d{4}),\s+"
    r"Extron Electronics,\s+"
    r"(?P<model>[^,]+),\s+"
    r"V(?P<firmware>[^,]+),\s+"
    r"(?P<part_number>\S+)",
    re.IGNORECASE,
)

# Regex to parse the date/time line 2 of the banner:
# Mon, 09 Mar 2026 12:08:14
_DATE_RE = re.compile(
    r"(?P<weekday>\w+),\s+(?P<date>\d{2}\s+\w+\s+\d{4}\s+\d{2}:\d{2}:\d{2})"
)

# Default SSH port for Extron SIS
DEFAULT_SSH_PORT = 22023

# How long to wait for the *first* byte of a response (overall deadline, seconds)
_RECV_TIMEOUT = 5.0
# How long to wait for the *next* chunk after data has already arrived (idle gap, seconds).
# The device sends a short one-line response and then goes silent; we use this short
# timeout to detect end-of-response without waiting the full _RECV_TIMEOUT each time.
_IDLE_TIMEOUT = 0.2
_RECV_CHUNK = 4096


@dataclass
class DeviceBanner:
    """Parsed information from the Extron login banner."""

    copyright_year: str = ""
    model: str = ""
    firmware_version: str = ""
    part_number: str = ""
    device_datetime: str = ""
    raw_banner: str = ""


@dataclass
class DeviceMetrics:
    """All metrics collected from a single Extron device."""

    banner: DeviceBanner = field(default_factory=DeviceBanner)

    # Current input selected per output (output_number -> input_number, 0 = no signal)
    current_inputs: dict[int, int] = field(default_factory=dict)

    # Input signal lock status (input_number -> True/False)
    input_signal_locked: dict[int, bool] = field(default_factory=dict)

    # Output audio mute status (output_number -> True/False)
    output_audio_muted: dict[int, bool] = field(default_factory=dict)

    # Output video mute status (output_number -> True/False)
    output_video_muted: dict[int, bool] = field(default_factory=dict)

    # Internal temperature in Celsius (None if not available)
    temperature_celsius: Optional[float] = None

    # Whether the scrape succeeded
    up: bool = False
    scrape_error: Optional[str] = None
    scrape_duration_seconds: float = 0.0


class ExtronSISClient:
    """
    SSH-based client for the Extron Simple Instruction Set (SIS) protocol.

    The Extron IN1804 and similar devices expose an SSH shell on port 22023.
    Upon login the device emits a two-line banner followed by a prompt.
    Commands are sent as plain text lines; responses are single lines.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_SSH_PORT,
        username: str = "admin",
        password: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

        self._ssh: Optional[paramiko.SSHClient] = None
        self._channel: Optional[paramiko.Channel] = None
        self._banner: DeviceBanner = DeviceBanner()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> DeviceBanner:
        """
        Open the SSH connection and read the login banner.

        Returns the parsed :class:`DeviceBanner`.
        Raises :class:`paramiko.SSHException` or :class:`socket.error` on failure.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.WarningPolicy())

        logger.debug("Connecting to %s:%d", self.host, self.port)
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=self.timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        # Open an interactive shell channel (Extron devices use a shell, not exec)
        channel = client.invoke_shell()
        channel.settimeout(_RECV_TIMEOUT)

        self._ssh = client
        self._channel = channel

        # Read and parse the initial banner
        self._banner = self._read_banner()
        logger.debug("Connected to %s – model=%s firmware=%s",
                     self.host, self._banner.model, self._banner.firmware_version)
        return self._banner

    def close(self) -> None:
        """Close the SSH connection."""
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None

    def __enter__(self) -> "ExtronSISClient":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Banner parsing
    # ------------------------------------------------------------------

    def _read_banner(self) -> DeviceBanner:
        """
        Read the initial output from the device after login and parse the banner.

        The device emits something like::

            (c) Copyright 2023, Extron Electronics, IN1804 DO, V1.08, 60-1699-13
            Mon, 09 Mar 2026 12:08:14

        followed by a prompt character (e.g. a bare ``>`` or nothing).
        We read until we stop receiving data or see a prompt.
        """
        raw = self._recv_until_quiet()
        banner = DeviceBanner(raw_banner=raw)

        for line in raw.splitlines():
            line = line.strip()
            m = _BANNER_RE.search(line)
            if m:
                banner.copyright_year = m.group("year")
                banner.model = m.group("model").strip()
                banner.firmware_version = m.group("firmware").strip()
                banner.part_number = m.group("part_number").strip()
                continue

            m = _DATE_RE.search(line)
            if m:
                banner.device_datetime = (m.group("weekday") + ", " + m.group("date")).strip()

        return banner

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def send_command(self, cmd: str) -> str:
        """
        Send a SIS command and return the response line.

        :param cmd: The SIS command string (without trailing CR/LF).
        :returns: The stripped response string.
        :raises RuntimeError: If not connected.
        """
        if self._channel is None:
            raise RuntimeError("Not connected – call connect() first")

        # Flush any pending data
        self._flush()

        payload = cmd + "\r"
        logger.debug("→ %r", payload)
        self._channel.send(payload)

        response = self._recv_until_quiet(max_wait=self.timeout)
        # The device echoes the sent command back before the actual response.
        # Strip the echo (everything up to and including the first \n) so callers
        # only see the response value.
        if "\n" in response:
            response = response.split("\n", 1)[1]
        response = response.strip()
        logger.debug("← %r", response)
        return response

    # ------------------------------------------------------------------
    # SIS query helpers
    # ------------------------------------------------------------------

    def query_input_routing(self) -> str:
        """
        Query the current input-to-output routing.

        SIS command ``!`` returns the complete routing map, e.g.::

            Out01 In02 RGB
            Out02 In02 RGB

        or for a single-output device just the input number.
        """
        return self.send_command("!")

    def query_output_routing(self, output: int) -> str:
        """
        Query the current input tied to a specific output.

        SIS command ``!`` returns the routing map; *output* is accepted for
        API compatibility but the device returns the full map regardless.
        """
        return self.send_command("!")

    def query_input_signal(self, input_num: int) -> str:
        """
        Query the signal lock status for input *input_num*.

        SIS command ``<n>*\\`` – response is typically ``1`` (locked) or ``0``.
        """
        return self.send_command(f"{input_num}*\\")

    def query_audio_mute(self) -> str:
        """
        Query the global audio mute status.

        SIS command ``Z`` – response is ``1`` (muted) or ``0``.
        Note: this is a device-wide (global) mute, not per-output.
        """
        return self.send_command("Z")

    def query_video_mute(self, output: int) -> str:
        """
        Query the video mute status for output *output*.

        SIS command ``<n>*B`` – response is ``1`` (muted) or ``0``.
        """
        return self.send_command(f"{output}*B")

    def query_temperature(self) -> str:
        """
        Query the internal temperature.

        SIS command ``28STAT`` – response varies by device, e.g. ``Temp  C  25``.
        """
        return self.send_command("^[28STAT")

    def query_firmware(self) -> str:
        """
        Query the firmware version string.

        SIS command ``Q`` – response is the firmware version, e.g. ``V1.08``.
        """
        return self.send_command("Q")

    def query_part_number(self) -> str:
        """
        Query the device part number.

        SIS command ``N`` – response is the part number string.
        """
        return self.send_command("N")

    # ------------------------------------------------------------------
    # Low-level I/O helpers
    # ------------------------------------------------------------------

    def _recv_until_quiet(self, max_wait: float = _RECV_TIMEOUT) -> str:
        """
        Read from the channel until no more data arrives.

        Uses a two-phase strategy:
        - Phase 1: wait up to *max_wait* seconds for the first byte (overall deadline).
        - Phase 2: once data starts arriving, switch to a short *_IDLE_TIMEOUT* gap
          to detect end-of-response quickly without waiting the full *max_wait*.

        Returns the accumulated data as a decoded string.
        """
        if self._channel is None:
            return ""
        buf = b""
        deadline = time.monotonic() + max_wait
        # Phase 1: wait for first byte with the full timeout
        self._channel.settimeout(min(max_wait, _RECV_TIMEOUT))
        try:
            while time.monotonic() < deadline:
                try:
                    chunk = self._channel.recv(_RECV_CHUNK)
                    if not chunk:
                        break
                    buf += chunk
                    # Phase 2: switch to short idle timeout now that data is flowing
                    self._channel.settimeout(_IDLE_TIMEOUT)
                except socket.timeout:
                    # No more data – device has finished its response
                    break
        finally:
            # Always restore the standard recv timeout
            self._channel.settimeout(_RECV_TIMEOUT)
        return buf.decode("utf-8", errors="replace")

    def _flush(self) -> None:
        """Discard any unread data from the channel."""
        if self._channel is None:
            return
        self._channel.settimeout(0.1)
        try:
            while True:
                data = self._channel.recv(_RECV_CHUNK)
                if not data:
                    break
        except (socket.timeout, OSError):
            pass
        finally:
            self._channel.settimeout(_RECV_TIMEOUT)
