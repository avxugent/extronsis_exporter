"""Client allowlist, Basic auth and URL-mode target allowlist."""

import base64
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from extronsis_exporter import collector
from extronsis_exporter.__main__ import (
    _client_allowed,
    _make_handler,
    _target_allowed,
    load_config,
)
from extronsis_exporter.prober import ProbeResult

ENV = ("EXTRONSIS_AUTH_USERNAME", "EXTRONSIS_AUTH_PASSWORD", "EXTRONSIS_ALLOWED_CLIENTS")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)


def cfg_from(tmp_path, text=""):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return load_config(str(p))


# --- config parsing --------------------------------------------------------

def test_defaults_are_open(tmp_path):
    cfg = cfg_from(tmp_path)
    assert cfg["auth"] == {"username": None, "password": None}
    assert cfg["allowed_clients"] == [] and cfg["allowed_hosts"] == []
    assert cfg["allowed_ports"] == [443]


def test_auth_from_file(tmp_path):
    cfg = cfg_from(tmp_path, "auth: {username: u, password: p}\n")
    assert cfg["auth"] == {"username": "u", "password": "p"}


def test_auth_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRONSIS_AUTH_USERNAME", "eu")
    monkeypatch.setenv("EXTRONSIS_AUTH_PASSWORD", "ep")
    cfg = cfg_from(tmp_path, "auth: {username: u, password: p}\n")
    assert cfg["auth"] == {"username": "eu", "password": "ep"}


@pytest.mark.parametrize("auth", ["{username: u}", "{password: p}"])
def test_half_configured_auth_fails_at_startup(tmp_path, auth):
    with pytest.raises(ValueError, match="both"):
        cfg_from(tmp_path, f"auth: {auth}\n")


def test_allowed_clients_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EXTRONSIS_ALLOWED_CLIENTS", "10.0.0.1, 10.1.0.0/24")
    cfg = cfg_from(tmp_path, "allowed_clients: [192.168.0.1]\n")
    assert [str(n) for n in cfg["allowed_clients"]] == ["10.0.0.1/32", "10.1.0.0/24"]


def test_bad_allowed_clients_entry_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="allowed_clients"):
        cfg_from(tmp_path, "allowed_clients: [not-an-ip]\n")


def test_bad_allowed_hosts_regex_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="allowed_hosts"):
        cfg_from(tmp_path, "allowed_hosts: ['(unclosed']\n")


# --- pure helpers ----------------------------------------------------------

def test_client_allowed(tmp_path):
    cfg = cfg_from(tmp_path, "allowed_clients: [10.1.0.0/24, '2001:db8::/32']\n")
    assert _client_allowed(cfg, "10.1.0.77")
    assert _client_allowed(cfg, "::ffff:10.1.0.77")  # dual-stack mapped IPv4
    assert _client_allowed(cfg, "2001:db8::1")
    assert not _client_allowed(cfg, "10.2.0.1")
    assert not _client_allowed(cfg, "garbage")


def test_client_allowed_when_unrestricted(tmp_path):
    assert _client_allowed(cfg_from(tmp_path), "203.0.113.9")


def test_target_allowed(tmp_path):
    cfg = cfg_from(tmp_path, "allowed_hosts: ['^sw\\d+\\.example\\.org$']\nallowed_ports: [443, 8443]\n")
    assert _target_allowed(cfg, "sw1.example.org", 443)
    assert _target_allowed(cfg, "sw1.example.org", 8443)
    assert not _target_allowed(cfg, "sw1.example.org", 22)
    assert not _target_allowed(cfg, "evil.com", 443)
    assert not _target_allowed(cfg, "sw1.example.org.evil.com", 443)  # fullmatch, not search


def test_target_allowed_when_unrestricted(tmp_path):
    assert _target_allowed(cfg_from(tmp_path), "anything", 1)


# --- through the real server ----------------------------------------------

@pytest.fixture
def serve(monkeypatch, tmp_path):
    probed = []
    monkeypatch.setattr(
        collector, "probe_device", lambda host, **kw: probed.append(host) or ProbeResult(True, 200, "1", 0.1)
    )
    servers = []

    def start(yaml_text=""):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(cfg_from(tmp_path, yaml_text)))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", probed

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def get(url, auth=None):
    req = urllib.request.Request(url)  # noqa: S310 (localhost test server)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


AUTH_CFG = "auth: {username: prom, password: s3cret}\n"


def test_no_auth_configured_means_open(serve):
    base, _ = serve()
    assert get(base + "/metrics")[0] == 200


@pytest.mark.parametrize("path", ["/", "/metrics", "/probe?host=h"])
def test_auth_required_on_guarded_endpoints(serve, path):
    base, probed = serve(AUTH_CFG)
    status, headers = get(base + path)
    assert status == 401
    assert headers["WWW-Authenticate"].startswith("Basic ")
    assert probed == []  # nothing probed without auth


def test_auth_wrong_password(serve):
    base, _ = serve(AUTH_CFG)
    assert get(base + "/metrics", auth="prom:wrong")[0] == 401


def test_auth_ok(serve):
    base, _ = serve(AUTH_CFG)
    assert get(base + "/metrics", auth="prom:s3cret")[0] == 200


def test_healthz_is_always_open(serve):
    base, _ = serve(AUTH_CFG + "allowed_clients: [203.0.113.0/24]\n")
    assert get(base + "/healthz")[0] == 200


def test_client_outside_allowlist_gets_403(serve):
    base, probed = serve("allowed_clients: [203.0.113.0/24]\n")
    assert get(base + "/metrics")[0] == 403
    assert get(base + "/probe?host=h")[0] == 403
    assert probed == []


def test_client_inside_allowlist_ok(serve):
    base, _ = serve("allowed_clients: [127.0.0.0/8]\n")
    assert get(base + "/metrics")[0] == 200


def test_allowlist_is_checked_before_auth(serve):
    base, _ = serve(AUTH_CFG + "allowed_clients: [203.0.113.0/24]\n")
    assert get(base + "/metrics", auth="prom:s3cret")[0] == 403


def test_url_mode_target_restriction(serve):
    base, probed = serve("allowed_hosts: ['^sw1\\.example\\.org$']\n")
    assert get(base + "/probe?host=sw1.example.org")[0] == 200
    assert get(base + "/probe?host=evil.com")[0] == 403
    assert get(base + "/probe?host=sw1.example.org&port=22")[0] == 403
    assert probed == ["sw1.example.org"]  # rejected targets are never contacted


def test_config_devices_bypass_target_restriction(serve):
    base, probed = serve(
        "allowed_hosts: ['^nothing$']\ndevices:\n  - {name: a, host: dev.example.org}\n"
    )
    assert get(base + "/probe")[0] == 200
    assert probed == ["dev.example.org"]
