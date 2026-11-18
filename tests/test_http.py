"""End-to-end through the real HTTP server, with probing stubbed out."""

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from extronsis_exporter import collector
from extronsis_exporter.__main__ import _make_handler
from extronsis_exporter.prober import ProbeResult


def _serve(cfg):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(cfg))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def server(monkeypatch):
    probed = []

    def fake_probe(host, **kw):
        probed.append((host, kw))
        return ProbeResult(True, 200, "1.08", 0.1)

    monkeypatch.setattr(collector, "probe_device", fake_probe)
    servers = []

    def start(devices=()):
        cfg = {"devices": [
            {"name": "cfg", "host": "cfg.local", "port": 443, "path": "/www/index.html",
             "timeout": 10.0, "verify_tls": False} for _ in devices
        ]}
        httpd = _serve(cfg)
        servers.append(httpd)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        return base, probed

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 (localhost test server)
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_healthz(server):
    base, _ = server()
    assert get(base + "/healthz") == (200, "OK")


def test_unknown_path_404(server):
    base, _ = server()
    assert get(base + "/nope")[0] == 404


def test_index_renders(server):
    base, _ = server(devices=[1])
    status, body = get(base + "/")
    assert status == 200 and "extronsis-exporter" in body and "cfg.local" in body


def test_probe_url_parameter_mode(server):
    base, probed = server()
    status, body = get(base + "/probe?host=h1&name=room&port=8443")
    assert status == 200
    assert 'probe_success{device="room",host="h1"} 1.0' in body
    assert probed[0][0] == "h1" and probed[0][1]["port"] == 8443


def test_probe_config_mode(server):
    base, probed = server(devices=[1])
    status, body = get(base + "/probe")
    assert status == 200 and 'host="cfg.local"' in body


def test_host_param_wins_over_config(server):
    base, probed = server(devices=[1])
    get(base + "/probe?host=other")
    assert [h for h, _ in probed] == ["other"]


def test_probe_without_devices_or_host_is_400(server):
    base, _ = server()
    assert get(base + "/probe")[0] == 400


def test_probe_bad_parameter_is_400(server):
    base, _ = server()
    assert get(base + "/probe?host=h&port=abc")[0] == 400


def test_self_metrics_count_probe_requests(server):
    base, _ = server()
    get(base + "/probe?host=h")
    _, body = get(base + "/metrics")
    assert 'extronsis_probe_requests_total{result="success"}' in body
