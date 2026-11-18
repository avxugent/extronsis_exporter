"""ExtronCollector turns ProbeResults into the documented metric families."""

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from extronsis_exporter import collector
from extronsis_exporter.prober import ProbeResult


@pytest.fixture
def scrape(monkeypatch):
    def run(devices, results):
        by_host = dict(results)
        monkeypatch.setattr(collector, "probe_device", lambda host, **_: by_host[host])
        reg = CollectorRegistry(auto_describe=True)
        reg.register(collector.ExtronCollector(devices))
        return generate_latest(reg).decode()

    return run


def _dev(name, host, **kw):
    return {"name": name, "host": host, **kw}


def test_metrics_for_healthy_device(scrape):
    out = scrape(
        [_dev("a", "10.0.0.1")],
        [("10.0.0.1", ProbeResult(True, 200, "1.08", 0.25))],
    )
    assert 'probe_success{device="a",host="10.0.0.1"} 1.0' in out
    assert 'probe_duration_seconds{device="a",host="10.0.0.1"} 0.25' in out
    assert 'probe_http_status_code{device="a",host="10.0.0.1"} 200.0' in out
    assert 'extron_firmware_info{device="a",host="10.0.0.1",version="1.08"} 1.0' in out


def test_failed_device_reports_zeroes_and_no_firmware(scrape):
    out = scrape(
        [_dev("a", "10.0.0.1")],
        [("10.0.0.1", ProbeResult(error="refused"))],
    )
    assert 'probe_success{device="a",host="10.0.0.1"} 0.0' in out
    assert 'probe_http_status_code{device="a",host="10.0.0.1"} 0.0' in out
    assert "extron_firmware_info{" not in out


def test_one_failure_does_not_hide_other_devices(scrape):
    out = scrape(
        [_dev("up", "h1"), _dev("down", "h2")],
        [("h1", ProbeResult(True, 200, "1.0", 0.1)), ("h2", ProbeResult())],
    )
    assert 'probe_success{device="up",host="h1"} 1.0' in out
    assert 'probe_success{device="down",host="h2"} 0.0' in out


def test_device_options_are_forwarded_with_defaults(monkeypatch):
    seen = {}
    monkeypatch.setattr(collector, "probe_device", lambda **kw: seen.update(kw) or ProbeResult())
    c = collector.ExtronCollector([_dev("a", "h", port="8443", timeout="2", verify_tls=1)])
    list(c.collect())
    assert seen == {"host": "h", "port": 8443, "path": "/www/index.html",
                    "timeout": 2.0, "verify_tls": True}


def test_no_devices_is_valid(scrape):
    out = scrape([], [])
    assert "probe_success{" not in out
