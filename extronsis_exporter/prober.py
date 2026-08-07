"""
HTTPS device prober.

Connects to a device's web UI over HTTPS and extracts the firmware/software
version from the ``<div id="version">`` element on the page. Works generically
across any Extron device type (video switches, controllers, touch panels,
DSPs, ...) that exposes this convention, instead of relying on a
device-specific control protocol.
"""

from __future__ import annotations

import logging
import re
import ssl
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from http.client import HTTPException, HTTPSConnection
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_HTTPS_PORT = 443

# A device status page is small; cap the read so a misbehaving device can't
# make us buffer an unbounded response.
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# HTML5 void elements never get a matching end tag (and aren't necessarily
# self-closed with "/>" in real-world markup, e.g. "<input ...>"). They must
# not be counted when tracking nesting depth, or a void element inside
# #version throws off the depth count for the rest of the document.
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# Some devices (e.g. TLP Pro touch panel login pages) leave <div id="version">
# empty in the served markup and fill it in client-side via JS on window.onload
# from a "var version_number = ...;" literal. The literal itself is still
# present as plain text in the HTML we fetch, so it can be read directly
# without executing any JS. Unconfigured devices ship this as a "$..."
# placeholder (mirroring the device's own check_device_specific_string() JS
# guard), so treat that as "no version" too.
_JS_VERSION_RE = re.compile(r'var\s+version_number\s*=\s*"([^"]*)"')


class _VersionExtractor(HTMLParser):
    """Extracts the text content of the first ``<div id="version">`` element."""

    def __init__(self) -> None:
        super().__init__()
        self._depth: Optional[int] = None
        self._buf: list[str] = []
        self.version: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _VOID_ELEMENTS:
            return
        if self._depth is not None:
            self._depth += 1
            return
        if tag == "div" and dict(attrs).get("id") == "version":
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_ELEMENTS or self._depth is None:
            return
        self._depth -= 1
        if self._depth == 0:
            self.version = "".join(self._buf).strip()
            self._depth = None

    def handle_data(self, data: str) -> None:
        if self._depth is not None:
            self._buf.append(data)


def extract_version(html: str) -> Optional[str]:
    """
    Return the firmware/software version found in *html*, or None.

    Tries the text content of the first ``<div id="version">`` element first;
    falls back to the ``version_number`` JS literal used by devices that
    populate that div client-side (see ``_JS_VERSION_RE``).
    """
    parser = _VersionExtractor()
    parser.feed(html)
    if parser.version:
        return parser.version

    match = _JS_VERSION_RE.search(html)
    if match:
        value = match.group(1).strip()
        if value and not value.startswith("$"):
            return value

    return None


@dataclass
class ProbeResult:
    """Result of probing a single device's web UI."""

    success: bool = False
    status_code: Optional[int] = None
    version: Optional[str] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None


def probe_device(
    host: str,
    port: int = DEFAULT_HTTPS_PORT,
    path: str = "/www/index.html",
    timeout: float = 10.0,
    verify_tls: bool = False,
) -> ProbeResult:
    """
    Fetch *path* from the device's HTTPS web UI and extract the firmware version.

    Connectivity failures (DNS, TCP, TLS, HTTP-level errors) are caught and
    reported via :attr:`ProbeResult.success` / :attr:`ProbeResult.error`
    rather than raised, so callers can always emit a ``probe_success`` metric.
    A response is only considered successful if the server returns a 2xx/3xx
    status; the firmware version is extracted on a best-effort basis and left
    ``None`` if the ``#version`` element isn't found.
    """
    start = time.monotonic()
    result = ProbeResult()

    context = ssl.create_default_context()
    if not verify_tls:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    conn = HTTPSConnection(host, port, timeout=timeout, context=context)
    try:
        conn.request("GET", path, headers={"User-Agent": "extronsis-exporter"})
        response = conn.getresponse()
        result.status_code = response.status
        body = response.read(_MAX_RESPONSE_BYTES)
        result.success = 200 <= response.status < 400
        if result.success:
            html = body.decode("utf-8", errors="replace")
            result.version = extract_version(html)
            if result.version is None:
                logger.warning("No #version element found on %s:%d%s", host, port, path)
        else:
            logger.warning("Probe for %s:%d%s returned HTTP %d", host, port, path, response.status)
    except (OSError, HTTPException, ssl.SSLError) as exc:
        result.error = str(exc)
        logger.warning("Probe failed for %s:%d%s: %s", host, port, path, exc)
    finally:
        conn.close()
        result.duration_seconds = time.monotonic() - start

    return result
