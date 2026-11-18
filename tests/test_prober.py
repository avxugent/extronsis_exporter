"""probe_device() with HTTPSConnection replaced by a fake -- no network."""

import ssl
from http.client import HTTPException

import pytest

from extronsis_exporter import prober


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self, _limit=None):
        return self._body


class FakeConn:
    """Stands in for HTTPSConnection; records how it was built and called."""

    instances = []

    def __init__(self, host, port, timeout=None, context=None, *, response=None, exc=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.response, self.exc = response, exc
        self.requested = None
        self.closed = False
        FakeConn.instances.append(self)

    def request(self, method, path, headers=None):
        self.requested = (method, path, headers)
        if self.exc:
            raise self.exc

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


@pytest.fixture
def fake_conn(monkeypatch):
    FakeConn.instances = []

    def install(response=None, exc=None):
        def factory(host, port, timeout=None, context=None):
            return FakeConn(host, port, timeout, context, response=response, exc=exc)

        monkeypatch.setattr(prober, "HTTPSConnection", factory)
        return FakeConn.instances

    return install


def test_success_with_version(fake_conn):
    inst = fake_conn(FakeResponse(200, b'<div id="version">1.08</div>'))
    r = prober.probe_device("dev.local", path="/x")
    assert (r.success, r.status_code, r.version, r.error) == (True, 200, "1.08", None)
    assert inst[0].requested[:2] == ("GET", "/x")
    assert inst[0].closed
    assert r.duration_seconds >= 0


def test_2xx_without_version_is_still_success(fake_conn):
    fake_conn(FakeResponse(200, b"<html></html>"))
    r = prober.probe_device("dev.local")
    assert r.success is True
    assert r.version is None


def test_401_login_page_with_version_counts_as_success(fake_conn):
    fake_conn(FakeResponse(401, b'<script>var version_number = "2.1.0";</script>'))
    r = prober.probe_device("dev.local")
    assert (r.success, r.status_code, r.version) == (True, 401, "2.1.0")


def test_error_status_without_version_is_failure(fake_conn):
    fake_conn(FakeResponse(500, b"oops"))
    r = prober.probe_device("dev.local")
    assert (r.success, r.status_code, r.version) == (False, 500, None)


def test_invalid_utf8_body_does_not_raise(fake_conn):
    fake_conn(FakeResponse(200, b'\xff\xfe<div id="version">1.0</div>'))
    assert prober.probe_device("dev.local").version == "1.0"


@pytest.mark.parametrize(
    "exc",
    [ConnectionRefusedError("refused"), TimeoutError("timed out"),
     ssl.SSLError("bad handshake"), HTTPException("bad status line")],
)
def test_connectivity_errors_are_folded_into_result(fake_conn, exc):
    inst = fake_conn(exc=exc)
    r = prober.probe_device("dev.local")
    assert r.success is False
    assert r.status_code is None
    assert str(exc) in r.error
    assert inst[0].closed


def test_tls_verification_is_off_by_default(fake_conn):
    inst = fake_conn(FakeResponse(200, b""))
    prober.probe_device("dev.local")
    ctx = inst[0].context
    assert ctx.verify_mode == ssl.CERT_NONE and ctx.check_hostname is False


def test_tls_verification_can_be_enabled(fake_conn):
    inst = fake_conn(FakeResponse(200, b""))
    prober.probe_device("dev.local", verify_tls=True)
    ctx = inst[0].context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_port_and_timeout_are_passed_through(fake_conn):
    inst = fake_conn(FakeResponse(200, b""))
    prober.probe_device("dev.local", port=8443, timeout=3.5)
    assert (inst[0].port, inst[0].timeout) == (8443, 3.5)
